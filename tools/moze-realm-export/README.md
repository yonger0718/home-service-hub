moze-realm-export
=================

Converts a MOZE backup archive (`MOZE_4.0*.zip`, containing `moze.realm`, `info` and `re/*.jpg`) into one JSON
document for `app.services.moze_backup_import_service` (accounting-service).

Setup
-----

    cd tools/moze-realm-export
    npm ci          # realm is pinned to exactly 12.13.1 (prebuilt binary, works on aarch64 with Node 22)

Usage
-----

    node tools/moze-realm-export/index.js <backup.zip> --out <backup.json> [--work <dir>]

- Unzips only `moze.realm` and `info` into the work directory (default: a new private directory under `$TMPDIR`)
  and opens that copy; Realm upgrades the copy's file format in place, the archive itself is never modified.
- Writes `{"exported_at", "info", "classes"}` with mode 600: live rows (`isDeleted == false`) of the classes in
  `export.js` (`EXPORT_CLASSES`, a field whitelist per class), object links flattened to the linked row's primary
  key, lists of links to arrays of keys, dates as `YYYY-MM-DDTHH:MM:SS` in Asia/Taipei, dictionaries copied.
  `exported_at` is the `moze.realm` entry time inside the zip.
- Deletes the work copy afterwards (the whole directory when it created it, otherwise `moze.*` and `info`).
- Exit codes: 0 success, 1 failure (`moze-realm-export: <reason>` on stderr, e.g. `archive has no moze.realm`,
  `cannot open moze.realm: …`), 2 bad usage. The process exits explicitly because Realm keeps the event loop alive.

The accounting importer runs this tool as a subprocess. `MOZE_REALM_EXPORTER` in the root `.env` may name another
script (`.js`, run with `node` from `PATH`) or an executable; the default is
`node <repo>/tools/moze-realm-export/index.js`.

The backup holds private data: run the tool only into a private scratch directory (mode 700) and delete the JSON
after use. Tests (`npm test`, which runs `node --test test/*.test.js`; Node 22 rejects `node --test test/`) use a
synthetic Realm built by `test/make-fixture.js`, never a real backup; the test file calls `Realm.shutdown()` in an
`after` hook, otherwise `node --test` never finishes.
