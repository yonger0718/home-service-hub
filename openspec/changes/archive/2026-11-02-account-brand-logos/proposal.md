## Why

The owner wished (#175) for bank and card brand logos on accounts, as MOZE showed them. Accounts today carry only an emoji `icon`, which cannot tell two banks apart at a glance.

## What Changes

- A built-in set of about 30 institution logos ships with the SPA (no runtime fetch from third parties).
- `account.logo` (a slug into the set, nullable) sits next to `account.icon`; a logo wins over the emoji wherever account icons render, and the emoji stays the fallback.
- The MOZE backup importer maps `AHAccount.imageName` to a logo slug for accounts it creates, and for existing accounts under the rule it already uses for `icon`.
- The account settings page gets a logo picker (grid of the set plus 無) beside the 圖示 emoji input.

## Impact

- accounting-service: new nullable column `account.logo` (Alembic migration) and an API field on the account read and write endpoints.
- SPA: bundled logo assets, an account-icon component, the picker and the form plumbing.
- Importer: `imageName` to logo mapping.
