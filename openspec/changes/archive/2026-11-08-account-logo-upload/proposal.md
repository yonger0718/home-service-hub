## Why

Each new brand logo currently needs a code change and a release. Letting the owner upload an image per account removes that loop.

## What Changes

- API: `PUT`/`DELETE /accounts/{id}/logo` and `GET /accounts/{id}/logo.png`, normalising uploads to a 256×256 RGBA PNG stored in a side table; `logo_hash` on account reads.
- SPA: uploaded image takes precedence over the bundled slug, then the emoji; upload from the account page header and a Logo block on the settings page.
- Icon-slot rule: uploaded logo images may render in owner-chosen icon slots.

## Impact

- API: new table `account_logo_image` (migration), three endpoints, one response field.
- SPA: accounts list, account page, settings page, a logo store.
- OpenSpec: `accounting-ledger`, `frontend-accounting-ledger`, `frontend-ui-primitives`.
