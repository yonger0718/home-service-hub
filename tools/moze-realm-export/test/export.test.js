"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync, spawnSync } = require("node:child_process");
const { after, test } = require("node:test");
const Realm = require("realm");

const { exportRealm, taipeiIso } = require("../export");
const { makeFixture } = require("./make-fixture");

const INDEX = path.join(__dirname, "..", "index.js");

// Realm keeps the event loop alive after the in-process tests; let the test process end.
after(() => Realm.shutdown());

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "moze-export-test-"));
}

function exportFixture() {
  const dir = tempDir();
  const file = makeFixture(path.join(dir, "src"));
  const realm = new Realm({ path: file });
  try {
    return exportRealm(realm, undefined, "2026-10-01T17:00:37");
  } finally {
    realm.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function fixtureZip({ withRealm = true } = {}) {
  const dir = tempDir();
  const src = path.join(dir, "src");
  makeFixture(src);
  fs.writeFileSync(path.join(src, "info"), "version: 205\n");
  fs.mkdirSync(path.join(src, "re"));
  fs.writeFileSync(path.join(src, "re", "receipt.jpg"), "not really a jpeg");
  const zip = path.join(dir, "MOZE_test.zip");
  const members = withRealm ? ["moze.realm", "info", "re/receipt.jpg"] : ["re/receipt.jpg"];
  execFileSync("zip", ["-q", zip, ...members], { cwd: src });
  return { dir, zip };
}

function runCli(args) {
  return spawnSync(process.execPath, [INDEX, ...args], { encoding: "utf8", timeout: 60000 });
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

test("deleted rows are excluded and classes missing from the Realm are left out", () => {
  const { classes } = exportFixture();
  assert.deepEqual(classes.AHRecord.map((r) => r.identifier).sort(), ["R-IN", "R-LUNCH", "R-OUT"]);
  assert.equal(classes.AHBonusReward, undefined);
  assert.deepEqual(classes.AHCurrency.map((c) => c.code).sort(), ["JPY", "TWD"]);
});

test("object links become primary keys and lists of links become arrays of keys", () => {
  const { classes } = exportFixture();
  const lunch = classes.AHRecord.find((r) => r.identifier === "R-LUNCH");
  assert.equal(lunch.account, "A-WALLET");
  assert.equal(lunch.currency, "TWD");
  const [transfer] = classes.AHTransfer;
  assert.deepEqual([transfer.outRecord, transfer.inRecord, transfer.exchangeRate], ["R-OUT", "R-IN", 4.62]);
  assert.deepEqual(classes.AHPackage[0].records, ["R-LUNCH", "R-OUT"]);
  const yen = classes.AHAccount.find((a) => a.identifier === "A-YEN");
  assert.equal(yen.group, null);
  assert.equal(yen.mainCurrency, "JPY");
});

test("dates are Asia/Taipei wall-clock ISO strings and dictionaries are copied", () => {
  const { classes, exported_at } = exportFixture();
  assert.equal(exported_at, "2026-10-01T17:00:37");
  const lunch = classes.AHRecord.find((r) => r.identifier === "R-LUNCH");
  assert.equal(lunch.date, "2026-09-01T12:30:00");
  assert.equal(lunch.chargeDate, "2026-09-01T12:30:00");
  const wallet = classes.AHAccount.find((a) => a.identifier === "A-WALLET");
  assert.equal(wallet.cacheDate, "2026-10-01T00:00:00");
  assert.deepEqual(wallet.balanceInfo, { 1759248000: 1500.5 });
  assert.equal(taipeiIso(new Date("2026-12-31T16:00:00Z")), "2027-01-01T00:00:00");
});

test("fields outside the whitelist are never exported", () => {
  const { classes } = exportFixture();
  for (const account of classes.AHAccount) {
    assert.equal("passcode" in account, false);
    assert.equal("isDeleted" in account, false);
  }
});

test("CLI converts a zip, leaves the zip untouched and deletes its work copy", () => {
  const { dir, zip } = fixtureZip();
  const before = sha256(zip);
  const out = path.join(dir, "backup.json");
  const work = path.join(dir, "work");

  const result = runCli([zip, "--out", out, "--work", work]);

  assert.equal(result.status, 0, result.stderr);
  const document = JSON.parse(fs.readFileSync(out, "utf8"));
  assert.equal(document.info, "version: 205");
  assert.match(document.exported_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/);
  assert.equal(document.classes.AHRecord.length, 3);
  assert.equal(sha256(zip), before);
  assert.deepEqual(fs.readdirSync(work), []);
  assert.equal(fs.statSync(out).mode & 0o777, 0o600);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CLI without --work uses and removes its own temporary directory", () => {
  const { dir, zip } = fixtureZip();
  const out = path.join(dir, "backup.json");
  const before = fs.readdirSync(os.tmpdir()).filter((name) => name.startsWith("moze-realm-"));

  const result = runCli([zip, "--out", out]);

  assert.equal(result.status, 0, result.stderr);
  const after = fs.readdirSync(os.tmpdir()).filter((name) => name.startsWith("moze-realm-"));
  assert.deepEqual(after.sort(), before.sort());
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CLI exits 1 naming moze.realm when the archive has no database", () => {
  const { dir, zip } = fixtureZip({ withRealm: false });
  const out = path.join(dir, "backup.json");

  const result = runCli([zip, "--out", out]);

  assert.equal(result.status, 1);
  assert.match(result.stderr, /archive has no moze\.realm/);
  assert.equal(fs.existsSync(out), false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CLI exits 1 when the file is not a zip or the database cannot be opened", () => {
  const dir = tempDir();
  const notZip = path.join(dir, "notes.zip");
  fs.writeFileSync(notZip, "plain text");
  const bad = runCli([notZip, "--out", path.join(dir, "a.json")]);
  assert.equal(bad.status, 1);
  assert.match(bad.stderr, /cannot read .* as a zip archive/);

  const src = path.join(dir, "src");
  fs.mkdirSync(src);
  fs.writeFileSync(path.join(src, "moze.realm"), crypto.randomBytes(4096));
  const corrupt = path.join(dir, "corrupt.zip");
  execFileSync("zip", ["-q", corrupt, "moze.realm"], { cwd: src });
  const work = path.join(dir, "work");
  const broken = runCli([corrupt, "--out", path.join(dir, "b.json"), "--work", work]);
  assert.equal(broken.status, 1);
  assert.match(broken.stderr, /cannot open moze\.realm/);
  assert.deepEqual(fs.readdirSync(work), []);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CLI exits 2 on bad usage", () => {
  const result = runCli(["only.zip"]);
  assert.equal(result.status, 2);
  assert.match(result.stderr, /usage: node index\.js <zip> --out <json>/);
});

test("CLI refuses a moze.realm entry that is not a regular file", () => {
  const dir = tempDir();
  const src = path.join(dir, "src");
  fs.mkdirSync(src);
  fs.writeFileSync(path.join(src, "target.realm"), "elsewhere");
  fs.symlinkSync("target.realm", path.join(src, "moze.realm"));
  const zip = path.join(dir, "link.zip");
  execFileSync("zip", ["-q", "-y", zip, "moze.realm"], { cwd: src }); // -y stores the symlink itself
  const work = path.join(dir, "work");

  const result = runCli([zip, "--out", path.join(dir, "a.json"), "--work", work]);

  assert.equal(result.status, 1);
  assert.match(result.stderr, /moze\.realm is not a regular file/);
  assert.deepEqual(fs.readdirSync(work), []);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CLI refuses an archive whose entries unpack beyond the size cap", () => {
  const dir = tempDir();
  const src = path.join(dir, "src");
  fs.mkdirSync(src);
  fs.writeFileSync(path.join(src, "moze.realm"), Buffer.alloc(2 * 1024 * 1024)); // compresses to a few KB
  const zip = path.join(dir, "bomb.zip");
  execFileSync("zip", ["-q", zip, "moze.realm"], { cwd: src });
  const work = path.join(dir, "work");

  const result = spawnSync(process.execPath, [INDEX, zip, "--out", path.join(dir, "a.json"), "--work", work], {
    encoding: "utf8",
    timeout: 60000,
    env: { ...process.env, MOZE_EXPORT_MAX_UNZIPPED_BYTES: String(1024 * 1024) },
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /moze\.realm unpacks to 2097152 bytes, more than the 1048576-byte cap/);
  assert.deepEqual(fs.readdirSync(work), []);
  fs.rmSync(dir, { recursive: true, force: true });
});

// zip will not store two members with one name, so the second is added as "moze.realX" and the name bytes are
// patched afterwards (names are not covered by the CRC).
function duplicateRealmZip(dir, addFirst) {
  const src = path.join(dir, "src");
  fs.mkdirSync(src);
  addFirst(src);
  fs.writeFileSync(path.join(src, "moze.realm"), "x");
  const zip = path.join(dir, "dup.zip");
  execFileSync("zip", ["-q", "-y", zip, "moze.realX", "moze.realm"], { cwd: src });
  const bytes = fs.readFileSync(zip);
  fs.writeFileSync(zip, Buffer.from(bytes.toString("latin1").replaceAll("moze.realX", "moze.realm"), "latin1"));
  assert.equal(execFileSync("unzip", ["-Z1", zip], { encoding: "utf8" }), "moze.realm\nmoze.realm\n");
  return zip;
}

function runCliWithCap(zip, dir, work) {
  return spawnSync(process.execPath, [INDEX, zip, "--out", path.join(dir, "a.json"), "--work", work], {
    encoding: "utf8",
    timeout: 60000,
    env: { ...process.env, MOZE_EXPORT_MAX_UNZIPPED_BYTES: "1024" },
  });
}

test("CLI refuses an archive with two moze.realm members when the first is oversized", () => {
  const dir = tempDir();
  const zip = duplicateRealmZip(dir, (src) => fs.writeFileSync(path.join(src, "moze.realX"), Buffer.alloc(10000)));
  const work = path.join(dir, "work");

  const result = runCliWithCap(zip, dir, work);

  assert.equal(result.status, 1);
  assert.match(result.stderr, /archive has more than one moze\.realm/);
  assert.deepEqual(fs.readdirSync(work), []);
  fs.rmSync(dir, { recursive: true, force: true });
});

test("CLI refuses an archive with a symlink moze.realm followed by a regular one", () => {
  const dir = tempDir();
  const zip = duplicateRealmZip(dir, (src) => {
    fs.writeFileSync(path.join(src, "target.realm"), "elsewhere");
    fs.symlinkSync("target.realm", path.join(src, "moze.realX"));
  });
  const work = path.join(dir, "work");

  const result = runCliWithCap(zip, dir, work);

  assert.equal(result.status, 1);
  assert.match(result.stderr, /archive has more than one moze\.realm/);
  assert.deepEqual(fs.readdirSync(work), []);
  fs.rmSync(dir, { recursive: true, force: true });
});
