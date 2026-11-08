import { app, BrowserWindow, dialog, ipcMain, safeStorage, session } from "electron";
import { pathToFileURL } from "node:url";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { readiness, realRunner, recordedChange, recordedDemo, recordedTrace, runChangeCheck, runDemo, runTrace, locate, type Ctx, type Paths } from "./actions";
import { CHANGES, chooseDsn, isTrustedSender, MIN_PYTHON, redactDsn, TRACE_TABLES, isTraceTable, versionText, type ChangeId, type Saved } from "./core";
import { smoke } from "./smoke";

let win: BrowserWindow | null = null;
/** A database address typed in this session and not saved. Gone when the app quits. */
let memoryDsn: string | null = null;

const settingsPath = () => join(app.getPath("userData"), "settings.json");

function readSaved(): Saved {
  try {
    const j = JSON.parse(readFileSync(settingsPath(), "utf8")) as Saved;
    return { python: j.python, dataDir: j.dataDir, dsnEncrypted: j.dsnEncrypted };
  } catch {
    return {};
  }
}

function writeSaved(s: Saved): void {
  mkdirSync(app.getPath("userData"), { recursive: true });
  writeFileSync(settingsPath(), JSON.stringify(s, null, 2), { mode: 0o600 });
}

/** Electron's safeStorage encrypts with a key held in the macOS Keychain. Without it nothing is saved. */
const keychainAvailable = (): boolean => {
  try { return safeStorage.isEncryptionAvailable(); } catch { return false; }
};

function keychainDsn(saved: Saved): string | null {
  if (!saved.dsnEncrypted || !keychainAvailable()) return null;
  try { return safeStorage.decryptString(Buffer.from(saved.dsnEncrypted, "base64")); } catch { return null; }
}

const paths = (): Paths => app.isPackaged
  ? { besideApp: null, recordedDir: join(process.resourcesPath, "recorded"), examplesDir: join(process.resourcesPath, "examples") }
  : { besideApp: join(__dirname, "..", ".."), recordedDir: join(__dirname, "..", "recorded"), examplesDir: join(__dirname, "..", "..", "docs", "examples") };

function ctx(): Ctx {
  const saved = readSaved();
  return { env: process.env, saved, dsn: chooseDsn({ env: process.env, keychain: keychainDsn(saved), memory: memoryDsn }), paths: paths(), run: realRunner };
}

const pageUrl = () => pathToFileURL(join(__dirname, "renderer", "index.html")).toString();
const refuse = (m: string) => ({ ok: false, error: { kind: "failed", message: m } });

function registerIpc(): void {
  const handle = (channel: string, fn: (...a: any[]) => unknown): void => {
    ipcMain.handle(channel, (e, ...a) => {
      if (!isTrustedSender(e.senderFrame, pageUrl())) return refuse("Request refused: not from the app's own page.");
      return fn(...a);
    });
  };
  handle("state", async () => {
    const c = ctx();
    const loc = await locate(c);
    const py = loc.python.chosen;
    return {
      dataDir: { value: loc.dataDir.value, source: loc.dataDir.source, valid: loc.dataDirValid },
      python: py ? { value: py.path, source: py.source, version: versionText(py.version) } : { value: null, help: loc.python.help },
      pythonOverride: { env: !!(process.env.CLEARLEDGER_PYTHON ?? "").trim(), saved: !!readSaved().python },
      minPython: MIN_PYTHON.join("."),
      dsn: { source: c.dsn.source, shown: redactDsn(c.dsn.dsn) },
      keychain: keychainAvailable(),
      tables: TRACE_TABLES,
      changes: CHANGES.map((x) => ({ id: x.id, title: x.title, intent: x.intent })),
    };
  });
  handle("demo", () => runDemo(ctx()));
  handle("demo-recorded", () => recordedDemo(paths(), "You asked for the recording."));
  handle("trace", (table: unknown, key: unknown) => {
    if (!isTraceTable(table)) return refuse("Unknown table.");
    if (typeof key !== "string" || !key.trim() || key.length > 200) return refuse("Type a customer name or a deal id first.");
    if (table === "deal_to_cash" && !/^\d+$/.test(key.trim())) return refuse("A deal id is a number.");
    return runTrace(ctx(), table, key.trim());
  });
  handle("trace-recorded", () => recordedTrace(paths()));
  handle("change", (id: unknown) => {
    if (!CHANGES.some((x) => x.id === id)) return refuse("Unknown change.");
    return runChangeCheck(ctx(), id as ChangeId);
  });
  handle("change-recorded", (id: unknown) => {
    if (!CHANGES.some((x) => x.id === id)) return refuse("Unknown change.");
    return recordedChange(paths(), id as ChangeId, "You asked for the recording.");
  });
  handle("readiness", () => readiness(ctx()));
  handle("choose-setting", async (kind: unknown) => {
    if (!win || (kind !== "python" && kind !== "dataDir")) return { ok: false };
    const r = await dialog.showOpenDialog(win, {
      title: kind === "python" ? `Choose a Python ${MIN_PYTHON.join(".")} or newer` : "Choose the ClearLedger folder",
      properties: kind === "python" ? ["openFile", "showHiddenFiles"] : ["openDirectory"],
    });
    const picked = r.canceled ? null : (r.filePaths[0] ?? null);
    if (!picked) return { ok: false };
    writeSaved({ ...readSaved(), [kind]: picked });
    return { ok: true };
  });
  handle("clear-setting", (kind: unknown) => {
    if (kind !== "python" && kind !== "dataDir") return { ok: false };
    const s = readSaved();
    delete s[kind];
    writeSaved(s);
    return { ok: true };
  });
  handle("set-dsn", (dsn: unknown, remember: unknown) => {
    if (typeof dsn !== "string" || !dsn.trim() || dsn.length > 2000) return refuse("Type a database address first.");
    const s = readSaved();
    delete s.dsnEncrypted;
    if (remember === true) {
      if (!keychainAvailable()) return refuse("The keychain is not available, so the address cannot be saved. It is kept for this session only.");
      s.dsnEncrypted = safeStorage.encryptString(dsn.trim()).toString("base64");
      memoryDsn = null;
    } else memoryDsn = dsn.trim();
    writeSaved(s);
    return { ok: true };
  });
  handle("clear-dsn", () => {
    memoryDsn = null;
    const s = readSaved();
    delete s.dsnEncrypted;
    writeSaved(s);
    return { ok: true };
  });
}

function openWindow(): void {
  win = new BrowserWindow({
    width: 1100, height: 800, title: "ClearLedger",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, preload: join(__dirname, "preload.js") },
  });
  win.webContents.on("will-navigate", (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  void win.loadFile(join(__dirname, "renderer", "index.html"));
}

if (process.argv.includes("--smoke")) {
  // No window, no dock icon: the smoke runs headless and exits.
  app.dock?.hide();
  app.whenReady().then(async () => {
    const r = await smoke(paths(), readSaved(), process.env);
    console.log(JSON.stringify(r.summary, null, 2));
    app.exit(r.ok ? 0 : 1);
  }).catch((e: unknown) => { console.error(e instanceof Error ? e.message : String(e)); app.exit(1); });
} else {
  if (!app.requestSingleInstanceLock()) app.quit();
  app.whenReady().then(() => {
    session.defaultSession.setPermissionRequestHandler((_wc, _p, cb) => cb(false));
    registerIpc();
    openWindow();
    app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) openWindow(); });
  });
  app.on("window-all-closed", () => app.quit());
}

