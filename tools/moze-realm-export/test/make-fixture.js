"use strict";
// Build a tiny synthetic Realm with a subset of MOZE's class schema (no owner data).

const fs = require("node:fs");
const path = require("node:path");
const Realm = require("realm");

const SCHEMA = [
  {
    name: "AHCurrency",
    primaryKey: "code",
    properties: { code: "string", market: "string", isFavorite: "bool", isDeleted: "bool" },
  },
  {
    name: "AHAccountGroup",
    primaryKey: "identifier",
    properties: { identifier: "string", name: "string", sequence: "int", type: "int", isDeleted: "bool" },
  },
  {
    name: "AHAccount",
    primaryKey: "identifier",
    properties: {
      identifier: "string",
      name: "string",
      mainCurrency: "AHCurrency?",
      group: "AHAccountGroup?",
      originalAmount: "double",
      isCreditAccount: "bool",
      cacheDate: "date",
      balanceInfo: "double{}",
      passcode: "string?",
      isDeleted: "bool",
    },
  },
  {
    name: "AHRecord",
    primaryKey: "identifier",
    properties: {
      identifier: "string",
      type: "int",
      price: "double",
      fee: "double",
      bonus: "double",
      total: "double",
      currency: "AHCurrency?",
      account: "AHAccount?",
      date: "date",
      chargeDate: "date",
      name: "string",
      tags: "string",
      transferID: "string?",
      isTransferIn: "bool",
      isDeleted: "bool",
    },
  },
  {
    name: "AHTransfer",
    primaryKey: "identifier",
    properties: { identifier: "string", outRecord: "AHRecord?", inRecord: "AHRecord?", exchangeRate: "double", isDeleted: "bool" },
  },
  {
    name: "AHPackage",
    primaryKey: "identifier",
    properties: { identifier: "string", type: "int", eventType: "int", records: "AHRecord[]", name: "string", isDeleted: "bool" },
  },
];

const AT = (iso) => new Date(iso);

/** Create `<dir>/moze.realm` and return its path. Times are UTC instants; Taipei is UTC+8. */
function makeFixture(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "moze.realm");
  const realm = new Realm({ path: file, schema: SCHEMA, schemaVersion: 1 });
  realm.write(() => {
    const twd = realm.create("AHCurrency", { code: "TWD", market: "fiat", isFavorite: true, isDeleted: false });
    const jpy = realm.create("AHCurrency", { code: "JPY", market: "fiat", isFavorite: true, isDeleted: false });
    realm.create("AHCurrency", { code: "XAU", market: "fiat", isFavorite: false, isDeleted: false });
    const bank = realm.create("AHAccountGroup", { identifier: "G-BANK", name: "APP_GROUP_BANK", sequence: 1, type: 0, isDeleted: false });
    const wallet = realm.create("AHAccount", {
      identifier: "A-WALLET", name: "錢包", mainCurrency: twd, group: bank, originalAmount: 2000,
      isCreditAccount: false, cacheDate: AT("2026-09-30T16:00:00Z"), balanceInfo: { 1759248000: 1500.5 },
      passcode: "1234", isDeleted: false,
    });
    const yen = realm.create("AHAccount", {
      identifier: "A-YEN", name: "日幣", mainCurrency: jpy, group: null, originalAmount: 0,
      isCreditAccount: false, cacheDate: AT("2026-09-30T16:00:00Z"), balanceInfo: {}, passcode: null, isDeleted: false,
    });
    const base = { fee: 0, bonus: 0, tags: "", isTransferIn: false, isDeleted: false };
    const lunch = realm.create("AHRecord", {
      ...base, identifier: "R-LUNCH", type: 0, price: -120, total: -120, currency: twd, account: wallet,
      date: AT("2026-09-01T04:30:00Z"), chargeDate: AT("2026-09-01T04:30:00Z"), name: "午餐",
    });
    realm.create("AHRecord", {
      ...base, identifier: "R-GONE", type: 0, price: -99, total: -99, currency: twd, account: wallet,
      date: AT("2026-09-02T04:30:00Z"), chargeDate: AT("2026-09-02T04:30:00Z"), name: "刪除", isDeleted: true,
    });
    const out = realm.create("AHRecord", {
      ...base, identifier: "R-OUT", type: 2, price: -1000, total: -1000, currency: twd, account: wallet,
      date: AT("2026-09-03T01:00:00Z"), chargeDate: AT("2026-09-03T01:00:00Z"), name: "", transferID: "T-1",
    });
    const into = realm.create("AHRecord", {
      ...base, identifier: "R-IN", type: 2, price: 4620, total: 4620, currency: jpy, account: yen,
      date: AT("2026-09-03T01:00:00Z"), chargeDate: AT("2026-09-03T01:00:00Z"), name: "", transferID: "T-1",
      isTransferIn: true,
    });
    realm.create("AHTransfer", { identifier: "T-1", outRecord: out, inRecord: into, exchangeRate: 4.62, isDeleted: false });
    realm.create("AHPackage", { identifier: "P-1", type: 0, eventType: 0, records: [lunch, out], name: "拆帳", isDeleted: false });
  });
  realm.close();
  return file;
}

module.exports = { makeFixture, SCHEMA };
