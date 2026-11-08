import { contextBridge, ipcRenderer } from "electron";

// The page can only ask for these named actions. It never supplies a file path to open or a command to run:
// files and folders come from a native dialog in the main process.
contextBridge.exposeInMainWorld("clearledger", {
  state: (): Promise<unknown> => ipcRenderer.invoke("state"),
  demo: (): Promise<unknown> => ipcRenderer.invoke("demo"),
  demoRecorded: (): Promise<unknown> => ipcRenderer.invoke("demo-recorded"),
  trace: (table: string, key: string): Promise<unknown> => ipcRenderer.invoke("trace", table, key),
  traceRecorded: (): Promise<unknown> => ipcRenderer.invoke("trace-recorded"),
  change: (id: string): Promise<unknown> => ipcRenderer.invoke("change", id),
  changeRecorded: (id: string): Promise<unknown> => ipcRenderer.invoke("change-recorded", id),
  readiness: (): Promise<unknown> => ipcRenderer.invoke("readiness"),
  chooseSetting: (kind: string): Promise<unknown> => ipcRenderer.invoke("choose-setting", kind),
  clearSetting: (kind: string): Promise<unknown> => ipcRenderer.invoke("clear-setting", kind),
  setDsn: (dsn: string, remember: boolean): Promise<unknown> => ipcRenderer.invoke("set-dsn", dsn, remember),
  clearDsn: (): Promise<unknown> => ipcRenderer.invoke("clear-dsn"),
});
