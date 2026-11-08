#!/usr/bin/env node
// A tiny fake for the core's commands in tests: FAKE_MODE picks the behaviour.
import { readFileSync } from "node:fs";
const mode = process.env.FAKE_MODE ?? "fixture";
if (mode === "hang") setInterval(() => {}, 1000);
else if (mode === "fail") { console.error("line1\nTraceback: boom"); process.exit(2); }
else if (mode === "no-psycopg") { console.error("Traceback (most recent call last):\nModuleNotFoundError: No module named 'psycopg'"); process.exit(1); }
else if (mode === "garbage") console.log("this is not json");
else if (mode === "args") console.log(JSON.stringify({ argv: process.argv.slice(2), dsn: process.env.CLEARLEDGER_DSN ?? null, cwd: process.cwd() }));
else { process.stdout.write(readFileSync(process.env.FAKE_FIXTURE, "utf8")); process.exit(Number(process.env.FAKE_EXIT ?? 0)); }
