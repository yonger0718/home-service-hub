#!/usr/bin/env node
"use strict";
// CLI: node index.js <zip> --out <json> [--work <dir>]
// Unzips moze.realm into a private work directory, opens that copy (Realm upgrades it in place),
// writes one JSON document and deletes the copy. Exit 0 on success, 1 on failure, 2 on bad usage.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { exportRealm, taipeiIso } = require("./export");

const USAGE = "usage: node index.js <zip> --out <json> [--work <dir>]";
const MAX_ZIP_BYTES = 200 * 1024 * 1024;
// Cap on the uncompressed size of each extracted entry (zip bombs); tests lower it through the environment.
const MAX_UNZIPPED_BYTES = Number(process.env.MOZE_EXPORT_MAX_UNZIPPED_BYTES) || 2 * 1024 * 1024 * 1024;
// One `unzip -Zl` line: permissions, version, OS, uncompressed size, text/binary, compressed size, method,
// date, time, name (names may contain spaces, so the name is the rest of the line).
const ZIPINFO_LINE = /^(\S+)\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\d+\s+\S+\s+\S+\s+\S+\s(.+)$/;

class UsageError extends Error {}

function parseArgs(argv) {
  const args = { zip: null, out: null, work: null };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--out" || arg === "--work") {
      const value = argv[i + 1];
      if (!value) throw new UsageError(`${arg} needs a value\n${USAGE}`);
      args[arg.slice(2)] = value;
      i += 1;
    } else if (arg.startsWith("--")) {
      throw new UsageError(`unknown option ${arg}\n${USAGE}`);
    } else if (args.zip === null) {
      args.zip = arg;
    } else {
      throw new UsageError(`unexpected argument ${arg}\n${USAGE}`);
    }
  }
  if (!args.zip || !args.out) throw new UsageError(USAGE);
  return args;
}

function zipEntries(zip) {
  let listing;
  try {
    listing = execFileSync("unzip", ["-Zl", zip], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  } catch (error) {
    throw new Error(`cannot read ${zip} as a zip archive`);
  }
  const entries = new Map();
  for (const line of listing.split("\n")) {
    const match = ZIPINFO_LINE.exec(line);
    if (match) entries.set(match[3], { mode: match[1], size: Number(match[2]) });
  }
  return entries;
}

function checkEntry(name, entry) {
  // A symlink (l), directory (d) or other special entry would be extracted as such; Realm would then open and
  // upgrade whatever the link points at. Only regular files (a leading "-") are extracted.
  if (!entry.mode.startsWith("-")) throw new Error(`${name} is not a regular file in the archive`);
  if (entry.size > MAX_UNZIPPED_BYTES) {
    throw new Error(`${name} unpacks to ${entry.size} bytes, more than the ${MAX_UNZIPPED_BYTES}-byte cap`);
  }
}

function extract(zip, work) {
  const size = fs.statSync(zip).size;
  if (size > MAX_ZIP_BYTES) throw new Error(`archive is larger than 200 MB (${size} bytes)`);
  const entries = zipEntries(zip);
  if (!entries.has("moze.realm")) throw new Error("archive has no moze.realm");
  const wanted = entries.has("info") ? ["moze.realm", "info"] : ["moze.realm"];
  for (const name of wanted) checkEntry(name, entries.get(name));
  // TZ: zip entry times without a UTC extra field are local times of the phone that wrote them.
  execFileSync("unzip", ["-o", "-q", zip, ...wanted, "-d", work], {
    stdio: ["ignore", "ignore", "pipe"],
    env: { ...process.env, TZ: "Asia/Taipei" },
  });
  const realmPath = path.join(work, "moze.realm");
  const infoPath = path.join(work, "info");
  const info = fs.existsSync(infoPath) ? fs.readFileSync(infoPath, "utf8").trim() : null;
  return { realmPath, exportedAt: taipeiIso(fs.statSync(realmPath).mtime), info };
}

function cleanup(work, createdWork) {
  if (!work || !fs.existsSync(work)) return;
  if (createdWork) {
    fs.rmSync(work, { recursive: true, force: true });
    return;
  }
  for (const name of fs.readdirSync(work)) {
    if (name === "info" || name.startsWith("moze.")) fs.rmSync(path.join(work, name), { recursive: true, force: true });
  }
}

function writeJson(out, document) {
  const tmp = `${out}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(document), { mode: 0o600 });
  fs.renameSync(tmp, out);
}

function run(argv) {
  const args = parseArgs(argv);
  const createdWork = !args.work;
  let work;
  if (createdWork) {
    work = fs.mkdtempSync(path.join(os.tmpdir(), "moze-realm-"));
  } else {
    fs.mkdirSync(args.work, { recursive: true, mode: 0o700 });
    work = args.work;
  }
  let realm = null;
  try {
    const { realmPath, exportedAt, info } = extract(args.zip, work);
    const Realm = require("realm");
    try {
      realm = new Realm({ path: realmPath });
    } catch (error) {
      throw new Error(`cannot open moze.realm: ${error.message}`);
    }
    const document = exportRealm(realm, undefined, exportedAt);
    document.info = info;
    writeJson(args.out, document);
    const counts = Object.entries(document.classes).map(([name, rows]) => `${name}=${rows.length}`);
    process.stderr.write(`moze-realm-export: wrote ${args.out} (${counts.join(" ")})\n`);
  } finally {
    if (realm && !realm.isClosed) realm.close();
    cleanup(work, createdWork);
  }
}

if (require.main === module) {
  try {
    run(process.argv.slice(2));
    process.exit(0); // Realm keeps the event loop alive; exit explicitly
  } catch (error) {
    process.stderr.write(`moze-realm-export: ${error.message}\n`);
    process.exit(error instanceof UsageError ? 2 : 1);
  }
}

module.exports = { parseArgs, run, UsageError };
