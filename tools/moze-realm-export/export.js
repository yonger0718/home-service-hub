"use strict";
// Turn the live rows of an open MOZE Realm into plain JSON values (no file or process I/O here).

const TAIPEI = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Taipei",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

const ALL_FIELDS = "*";

// Field whitelist per class. Anything not listed (passcodes, e-invoice credentials, caches) is never exported.
const EXPORT_CLASSES = {
  AHAccount: [
    "identifier", "name", "mainCurrency", "group", "originalAmount", "isArchived", "sequence", "desc",
    "isBalanceIncluded", "isCreditAccount", "startDay", "paymentDeadlineType", "paymentDeadline", "creditLimit",
    "combinedAccount", "creditSharingID", "autoPaidAccount", "isCurrencyFeeEnabled", "feePercentage",
    "feeCalculation", "isRefundWithCurrencyFee", "type", "imageName", "cacheDate", "balanceInfo",
  ],
  AHAccountGroup: ["identifier", "name", "sequence", "type"],
  AHCategory: ["identifier", "name", "type", "imageName", "colorHex", "isHidden", "sequence"],
  AHClassification: ["identifier", "name", "category", "defaultAccount", "defaultProject", "isHidden", "sequence", "imageName"],
  AHProject: ["identifier", "name", "isArchived", "sequence"],
  AHTarget: ["identifier", "name", "type", "isSettle"],
  AHRecord: [
    "identifier", "type", "price", "fee", "bonus", "total", "currency", "currencyConversion", "account", "project",
    "classification", "target", "bonusRewards", "date", "chargeDate", "dateString", "chargeDateString", "name",
    "desc", "tags", "store", "feeName", "bonusName", "transferID", "refundID", "rewardID", "rewardRecordID",
    "packageID", "relatedID", "eventID", "feeID", "isTransferIn", "isRefund", "isEnabled", "invoiceNumber",
  ],
  AHTransfer: ["identifier", "outRecord", "inRecord", "exchangeRate"],
  AHPackage: ["identifier", "type", "eventType", "records", "name", "store", "desc"],
  AHBonusReward: [
    "identifier", "accountID", "name", "desc", "type", "rewardPercentage", "rewardAmount", "rewardPeriodType",
    "rewardTimeType", "rewardDelayDays", "rewardMonth", "rewardDay", "rewardCalculation", "totalRewardCalculation",
    "rewardLimit", "totalRewardLimit", "rewardSharingID", "spendThreshold", "totalSpendThreshold",
    "minCountThreshold", "isBasic", "rewardAccountID", "rewardProjectID", "startDate", "dueDate", "isEnabled",
    "sequence",
  ],
  AHBonusRewardSharing: ["identifier", "bonusRewards"],
  AHCreditSharing: ["identifier", "accounts"],
  AHCurrencyConversion: ["recordID", "exchangeRate", "baseCurrencyCode", "targetCurrencyCode"],
  AHCurrency: ["code", "customName", "symbol", "market", "decimalPlace", "baseCode", "isFavorite"],
  AHPeriod: ALL_FIELDS,
  AHInstallment: ALL_FIELDS,
  AHPreference: [
    "identifier", "expenseIncomeColor", "numberPadType", "firstWeekday", "mainCurrency", "hideRewardsOnHome",
    "isTotalBalanceAbbreviate",
  ],
  AHAppConfig: ["identifier", "timeZoneName"],
};

// Extra Realm query per class, combined with `isDeleted == false` when the class has that field.
const EXTRA_FILTERS = {
  AHCurrency: "isFavorite == true OR market == 'custom'",
};

function taipeiIso(value) {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) return null;
  const parts = Object.fromEntries(TAIPEI.formatToParts(value).map((part) => [part.type, part.value]));
  return `${parts.year.padStart(4, "0")}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
}

function scalar(value) {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return taipeiIso(value);
  if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") return value;
  if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) return null;
  return String(value); // decimal128, objectId, uuid
}

function primaryKeys(realm) {
  return Object.fromEntries(realm.schema.map((objectSchema) => [objectSchema.name, objectSchema.primaryKey]));
}

function convert(value, property, keys) {
  if (value === null || value === undefined) return property.type === "list" || property.type === "set" ? [] : null;
  const isLink = property.objectType !== undefined && property.objectType in keys;
  switch (property.type) {
    case "object":
      return value[keys[property.objectType]] ?? null;
    case "list":
    case "set":
      return Array.from(value, (item) => (isLink ? item[keys[property.objectType]] : scalar(item)));
    case "dictionary":
      return Object.fromEntries(
        Object.entries(value).map(([key, item]) => [key, isLink ? item?.[keys[property.objectType]] ?? null : scalar(item)]),
      );
    default:
      return scalar(value);
  }
}

function exportClass(realm, objectSchema, fields, keys) {
  const names = fields === ALL_FIELDS ? Object.keys(objectSchema.properties) : fields.filter((name) => name in objectSchema.properties);
  const filters = [];
  if ("isDeleted" in objectSchema.properties) filters.push("isDeleted == false");
  if (EXTRA_FILTERS[objectSchema.name]) filters.push(`(${EXTRA_FILTERS[objectSchema.name]})`);
  let rows = realm.objects(objectSchema.name);
  if (filters.length) rows = rows.filtered(filters.join(" AND "));
  return Array.from(rows, (row) =>
    Object.fromEntries(names.map((name) => [name, convert(row[name], objectSchema.properties[name], keys)])),
  );
}

/**
 * Export the classes named in `classes` ({className: [fields] | "*"}) from an open Realm.
 * Classes missing from the Realm are left out of the result; fields missing from a class are skipped.
 */
function exportRealm(realm, classes = EXPORT_CLASSES, exportedAt = null) {
  const keys = primaryKeys(realm);
  const byName = Object.fromEntries(realm.schema.map((objectSchema) => [objectSchema.name, objectSchema]));
  const out = {};
  for (const [name, fields] of Object.entries(classes)) {
    if (byName[name]) out[name] = exportClass(realm, byName[name], fields, keys);
  }
  return { exported_at: exportedAt, classes: out };
}

module.exports = { ALL_FIELDS, EXPORT_CLASSES, EXTRA_FILTERS, exportRealm, taipeiIso };
