# Account logo upload — design

Rev 2, 2026-11-02 (rev 1 same day; rev 2 folds the builder check: error bodies use the shared `{code, message,
trace_id}` shape, the GET URL carries `?v=<logo_hash>` so `immutable` caching is safe, routes are sync `def` like
their neighbours, `logo_hash` is a labelled scalar subquery on the existing account select, `AccountDetailOut`
is the PUT response, the `account-brand-logos` OpenSpec change is archived first, the proxy has no CSP so
`blob:` URLs are fine). Owner-approved direction (2026-11-01/02): an account's logo can be **uploaded** through the
accounting API (owner or the Hermes agent) and from the SPA, instead of being added to the bundled logo set with a
pull request and an SPA release. The bundled set (#175, PR #185) stays as it is and keeps working.

## 1. Intent

Since PR #185 every account can carry a `logo` slug that names one of 27 institution marks bundled with the SPA
(`frontend/src/assets/logos/`, manifest `frontend/src/app/theme/account-logos.ts`). That design was right for
the one-off MOZE migration (27 known banks) but every new institution costs a repository change: find a mark,
run the offline sanitiser, hash-name the file, extend the manifest, pass the asset gate, release the SPA, then
`PUT` the slug. The first new account after the migration (a Wise USD account, created 2026-11-01) showed the
cost: the account exists, the API accepts `logo: "wise"`, and the SPA renders the emoji because no such asset is
bundled.

The owner's picture of how this should work (2026-11-02):

- **In the SPA**: open the accounts list → tap an account → tap the big avatar/logo on its page → pick a file →
  the logo changes right there.
- **Through Hermes**: 「幫我找 Wise 的 logo 放上去」 — the agent finds an image on the web, converts it if needed and
  uploads it with the API token it already has.

Success: adding a logo for a new account needs no pull request, no SPA release and no new permission. A logo
uploaded from either entry point shows in every account-icon slot of the SPA within one reload, survives
backups/restores with the rest of the ledger, and a hostile image file cannot do anything worse than be rejected.

## 2. Goals / non-goals / anti-goals

Goals
- `PUT /accounts/{id}/logo` (multipart image, `write` scope) stores a normalised PNG for the account;
  `DELETE /accounts/{id}/logo` removes it; `GET /accounts/{id}/logo.png` (`read` scope) serves it.
- The stored image is **re-encoded by the server** (Pillow): never the uploaded bytes. Square (padded, not
  cropped), 256×256, PNG, metadata stripped.
- Account responses carry `logo_hash` so clients know whether an uploaded logo exists and when it changed.
- Precedence in the SPA: uploaded image → bundled slug (`logo`) → emoji (`icon`) → kind fallback.
- SPA entry points: a big avatar on the account page (new) that opens the file picker; 上傳 / 移除 on the account
  settings page beside the existing 品牌 logo picker.
- The Hermes `homehub-api` skill documents the upload (download → convert to PNG if needed → `PUT`).
- PR 2: an in-browser crop box (drag + zoom, square PNG out through a canvas) in front of the upload.

Non-goals
- Accepting SVG. Owner ruling 2026-11-02: SVG is converted to a raster by the uploader (Hermes or the owner).
  The server never parses SVG.
- Fetching images from a URL on the server (SSRF surface; the uploader downloads).
- Changing the bundled set, the manifest, the asset gate or the MOZE importer's `LOGO_BY_IMAGE` behaviour.
- Logos for categories, groups or projects. Same mechanism could serve them later; not now.
- Multiple sizes / responsive variants. One 256×256 PNG serves every slot (`object-fit: contain`).
- Caddy / CDN caching rules. The SPA caches in memory per `logo_hash`; HTTP caching headers are set but the
  reverse proxy is not changed in this work.

Anti-goals (a build that does these has failed even if it "works")
- Storing or serving user-supplied bytes verbatim, or trusting the `Content-Type` / file extension.
- Any path where the SPA's `<img>` needs the API token in a query string.
- Logo images on the filesystem outside the database (they would be missed by the pg_dump backup and by the
  release-checkout deploys, which have no shared writable directory for the accounting API).
- Making `GET /accounts` heavier: no base64 images inside list responses.

## 3. Constraints

- Monorepo `home-service-hub`; accounting API = FastAPI 0.129 + SQLAlchemy + Alembic (head `c7a2e9f4b1d8`
  at the time of writing — re-check at plan time), Postgres in `stonk-postgres-1`, SPA = Angular 21 standalone
  components with signals, OnPush.
- Auth: bearer token with scopes (`app/auth.py`): routers use `Depends(method_scope())` → `read` for GET/HEAD,
  `write` otherwise (CORS preflight is answered outside the auth middleware). The Hermes label holds read + propose + write and must not need more.
- The SPA sends the token only through the HttpClient interceptor on `/api/accounting` URLs
  (`interceptors/accounting-token.interceptor.ts`). Plain `<img src>` requests carry no token, so when scopes are
  enforced the image must be fetched through HttpClient (blob) and shown via an object URL.
- `python-multipart` is already a dependency (`POST /imports/moze`, `POST /imports/moze-backup`); Pillow is not
  in `requirements.txt` (it is pinned at 12.3.0 in the worker constraints file). Add `pillow` to the API
  requirements with the same pin.
- Owner data never enters the repo or tests: fixtures use generated images (Pillow-drawn squares), fictional
  account names and ids.
- Deploy order: API first (Alembic migration + new dependency installed in the release venv), then SPA. Old SPA
  against new API is harmless (ignores `logo_hash`); new SPA against old API shows the slug/emoji path and the
  upload button gets a 404 it must surface as 「伺服器尚未支援上傳」 rather than crash.
- The SPA publisher's static store is at its size cap (issue #279); this work adds no static assets, which is
  part of why uploads live in the database.

## 4. Data model

New table `account_logo_image` (one row per account, optional):

| column | type | notes |
|---|---|---|
| `account_id` | int, PK, FK `account.id` ON DELETE CASCADE | one logo per account |
| `png` | `bytea`, not null | the normalised 256×256 PNG; expected 2–40 KiB |
| `sha256` | `char(64)`, not null | hex digest of `png`; exposed truncated as `logo_hash` |
| `source_media_type` | `varchar(32)`, not null | sniffed type of the upload (`image/png` …), for diagnostics |
| `source_bytes` | int, not null | size of the upload, for diagnostics |
| `created_at` / `updated_at` | timestamptz, not null | server time |

Why a separate table and not a column on `account`: `GET /accounts` and every join that loads `Account` must
not drag 72 PNGs along; a side table keeps the hot row small and lets `logo_hash` be a cheap scalar subquery or
a `selectinload`. Why `bytea` and not the filesystem: see anti-goals — the pg_dump backup and the release-checkout
deploys cover the database and nothing else.

`Account.logo` (bundled slug) is unchanged and independent: an account may have both. Deleting the uploaded
image falls back to the slug.

Alembic revision: new head, down-revision = current head at plan time, `upgrade` creates the table, `downgrade`
drops it. `test_migration.py` head pin updated.

## 5. API

All three routes live in `routers/accounts.py` under the existing router (`Depends(method_scope())`).

### `PUT /accounts/{account_id}/logo`

- Body: `multipart/form-data`, one field `file` (`UploadFile`). Any filename, any declared content type — both
  are ignored except for diagnostics.
- Limits: read at most `MAX_LOGO_UPLOAD_BYTES = 5 MiB` (413 beyond, same pattern as `imports.py`).
- Validation (`services/account_logo_service.py`, pure function `normalise_logo(data: bytes) -> NormalisedLogo`):
  1. `Image.MAX_IMAGE_PIXELS = 40_000_000` module-level and `Image.open` inside `try` → `UnidentifiedImageError`,
     `DecompressionBombError`, `OSError`, `ValueError` all → 422 `{"detail": "logo_unreadable"}`.
  2. Sniffed format must be one of `PNG`, `JPEG`, `WEBP`; anything else (GIF, BMP, TIFF, SVG as text, ICO, PDF…)
     → 422 `{"detail": "logo_format"}`. Animated images: first frame only (`im.seek(0)` is the default).
  3. Dimensions: both sides ≥ 16 px and ≤ 8192 px, else 422 `{"detail": "logo_dimensions"}`.
  4. Normalise: `ImageOps.exif_transpose`, convert to `RGBA`, pad the shorter side with transparent pixels so the
     canvas is square (centre the image), resize to 256×256 with `Image.LANCZOS` (`reducing_gap=3.0`), encode
     PNG with `optimize=True`; no metadata chunks are written (fresh `Image.new`, no `pnginfo`).
  5. The result is the only thing persisted. Its `sha256` is computed on the PNG bytes.
- Account must exist → 404 `account not found` otherwise (archived accounts are fine; there is no soft delete —
  `delete_account` hard-deletes and refuses accounts with entries, so the cascade test uses an empty account).
- Write: upsert the `account_logo_image` row; `updated_at = now()`.
- Response: 200 with `AccountDetailOut` (via `ledger_service.get_account`), same shape as `PUT /accounts/{id}`,
  now with `logo_hash` set.
- Idempotent: uploading the same bytes twice yields the same `sha256` and a fresh `updated_at`.

### `DELETE /accounts/{account_id}/logo`

- 204 when a row was removed, 404 when the account does not exist, **204 also when the account exists but has no
  uploaded logo** (deleting nothing is not an error for the client's 移除 button).

### `GET /accounts/{account_id}/logo.png`

- 200 `image/png` with the stored bytes; headers `Cache-Control: private, max-age=31536000, immutable`,
  `ETag: "<sha256>"`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline; filename="logo.png"`.
  `If-None-Match` matching the ETag → 304.
- Clients MUST append `?v=<logo_hash>` (the server ignores the query): the URL changes with the content, which is
  what makes `immutable` correct and lets a re-upload show on the next fetch instead of after a year. The SPA
  store does this; the Hermes text shows it too.
- 404 when the account does not exist or has no uploaded logo (the SPA falls back; it never shows a broken tile).
- `read` scope (a GET under `method_scope()`); the SPA fetches it through HttpClient so the interceptor adds the
  token.

### `AccountOut.logo_hash`

- `str | None`: first 16 hex chars of `sha256`, `None` when no uploaded image. Present on every `AccountOut`
  (list and detail) — it is a 16-char string, not an image. `AccountOut` is assembled from a dict built in
  `ledger_service.list_accounts` (`select(Account, …)` with labelled columns, no `from_attributes`), so
  `logo_hash` is one more labelled column: a correlated scalar subquery `substr(sha256, 1, 16)` on
  `account_logo_image`, added to that select and to `ACCOUNT_FIELDS`. No per-account query.
- `AccountIn` is unchanged: `logo_hash` is read-only; a client that sends it gets it ignored (schema ignores
  unknown fields as today).

### Errors summary

| case | status | detail |
|---|---|---|
| no `file` field / empty file | 422 | FastAPI validation / `logo_empty` |
| > 5 MiB | 413 | `logo_too_large` |
| not an image Pillow can open, or decompression bomb | 422 | `logo_unreadable` |
| image but not PNG/JPEG/WEBP | 422 | `logo_format` |
| side < 16 px or > 8192 px | 422 | `logo_dimensions` |
| unknown account | 404 | `account not found` (existing wording) |
| missing `write` scope | 403 | `scope` (existing) |

Body shape: the shared error handler rewrites every `HTTPException` into `{"code": <status>, "message": <detail>,
"trace_id": …}` (`shared_lib/errors.py`), so the tokens above arrive in `message`, not `detail`; only FastAPI's own
validation errors keep the `detail` list. The SPA's `http-errors.ts` already reads both. Tests assert `message`.
`test_scope_enforcement` enumerates mutation routes automatically, so the new PUT/DELETE get the hermes-read-only
403 case for free.

## 6. SPA

### 6.1 Rendering (`<app-account-icon>`)

- New optional inputs `logoHash = input<string | null>(null)` and `accountId = input<number | null>(null)`
  (both optional: the settings-page logo grid renders `<app-account-icon [logo]="l.slug">` with no account).
  The uploaded path is taken only when both are set. Precedence inside `entry()`:
  1. `logoHash` set and the blob fetch has not failed → uploaded image (object URL);
  2. else bundled slug as today;
  3. else emoji / fallback.
- A new injectable `AccountLogoStore` (`services/account-logo-store.ts`): `url(accountId, hash): Signal<string |
  null>`; keeps `Map<accountId, {hash, objectUrl}>`; on a hash change it fetches
  `GET /api/accounting/accounts/{id}/logo.png?v={hash}` as `blob` through HttpClient (token interceptor applies), revokes
  the previous object URL, stores the new one; on 404/any error it records the failure for that hash so the slot
  falls back and does not retry until the hash changes. Store is `providedIn: 'root'`; object URLs are revoked
  when replaced (not on destroy — accounts are long-lived in the shell).
- Every call site that passes `[logo]` gains `[logoHash]` and `[accountId]`: accounts list rows, account picker
  (trigger, sheet rows, recent chips), 信用卡帳單 reminders, account settings big icon, and the new account page
  avatar. `LedgerAccount` (and so `AccountDetail extends LedgerAccount`) gains `logo_hash: string | null`.
- The reverse proxy sets no Content-Security-Policy today (checked 2026-11-02), so `blob:` image sources work;
  if a CSP is added later (#187) it must allow `img-src blob:`.

### 6.2 Account page avatar (owner's path)

`account-entries.html` header: an `<app-account-icon size="big">` between the back link and the `<h1>`, wrapped
in a `<button type="button" class="avatar" aria-label="更換 logo">`. Tapping opens a hidden `<input type="file"
accept="image/png,image/jpeg,image/webp">`. On change: in PR 1 the file goes straight to
`AccountingService.uploadAccountLogo(id, file)`; the button shows a busy state (`aria-busy`), on success the
account detail is replaced (new `logo_hash` → the store refetches → the avatar updates), on error a toast with
the mapped message (see 6.4). A long-press / secondary 移除 is **not** on this page; removal lives on the
settings page.

### 6.3 Account settings page

The 品牌 logo row (`account-settings.html` 61–78) becomes a 「Logo」 block:
- Current state line: 「已上傳的圖片」 when `logo_hash` is set (with the image), else 「內建：<label>」 when a slug is
  chosen, else 「無」.
- Buttons: 上傳圖片 (file input, same accept list), 移除圖片 (only when an uploaded image exists; calls DELETE,
  then refreshes), and the existing 選擇內建 logo toggle for the grid picker. The grid picker and `pickLogo()`
  are unchanged; the hint under the block says the uploaded image takes precedence over the bundled choice.
- Upload here happens immediately (not deferred to 儲存), because the image is a separate resource from the
  `AccountIn` document; the copy says so (「上傳後立即生效」). On the `accounts/new` route the upload button is
  disabled with the hint 「先建立帳戶，再上傳 logo」.

### 6.4 Service + copy

- `AccountingService.uploadAccountLogo(id, file): Observable<AccountDetail>` — `FormData` with `file`, `PUT`
  `${apiUrl}/accounts/${id}/logo`, inside `bumpAccounts` like `updateAccount`.
- `AccountingService.deleteAccountLogo(id): Observable<void>` — `DELETE`, inside `bumpAccounts`.
- Error copy (toast): 413 → 「圖片太大（上限 5 MB）」; 422 `logo_format` → 「只接受 PNG、JPEG、WebP」; 422
  `logo_unreadable`/`logo_dimensions` → 「這個檔案無法當作圖片使用」; 404 on the PUT route → 「伺服器尚未支援上傳」;
  other → generic existing error toast.
- Line-icons gate: no new emoji in templates; the camera/upload glyph uses the existing line-icon set.

### 6.5 PR 2 — crop box

- A `LogoCropSheet` (bottom sheet on phone, dialog on desktop) opened from both entry points after a file is
  chosen: shows the image inside a square frame, drag to pan, pinch/wheel/slider to zoom (min = image covers the
  frame, max = 4×), 「使用原圖」 shortcut, 確定 / 取消. Output: a canvas draw of the visible square at 512×512 →
  `canvas.toBlob('image/png')` → the same upload call. The server still pads/resizes, so the crop sheet is purely
  a convenience and the API contract does not change. Owner ruling: manual crop is allowed but auto is the
  default behaviour; the sheet must have a one-tap 「使用原圖」.
- Out of PR 1 so the upload can ship (and Wise can get its logo) without waiting on the gesture work.

## 7. Hermes skill

`~/.hermes/skills/productivity/homehub-api/references/accounting-api.md` gains an "Account logo" section:
- find an official mark (prefer the institution's own site, then Wikimedia Commons), download it to a scratch
  file, convert SVG → PNG at ≥ 256 px locally (e.g. `rsvg-convert` or `cairosvg` on the agent host — not on the
  API), then:
  ```
  curl -fsS -X PUT -H "Authorization: Bearer $TOKEN" -F "file=@logo.png" \
    http://127.0.0.1:8000/accounts/<id>/logo
  ```
- to view what is stored: `GET /accounts/<id>/logo.png?v=<logo_hash>` (binary; save to a file, do not print).
- list accounts without any logo: `GET /accounts` rows with `logo_hash == null and logo == null`.
- never put the token or the owner's account names in chat artefacts; report the account id and the source URL.
The skill file lives outside the repo; the change is made on the host as part of the rollout, like the reward
rules skill text.

## 8. Security review points (for the builder and the reviewer)

- The server never stores or serves uploaded bytes; only Pillow's re-encoded PNG. Decompression bomb guard via
  `MAX_IMAGE_PIXELS` + 8192 px side cap + 5 MiB body cap. `Image.open` + full `load()` happen inside the
  exception boundary.
- Format allow-list is on the **sniffed** format (`im.format`), never on extension or declared type. SVG is text
  and fails `Image.open` → 422.
- Response headers on the GET: `nosniff`, `inline` disposition, fixed `image/png`. The SPA never injects the
  bytes anywhere but an `<img src="blob:…">`.
- No token in URLs: the SPA fetches via HttpClient. `GET` requires `read` like every other GET.
- Pillow pin matches the worker constraints (12.3.0); the release venv gets it through `requirements.txt`.
- Tests: fixtures are generated in the test (Pillow draws a 300×100 red rectangle, a 1-px image, a 9000×9000
  PNG header without data for the bomb case, a `<svg>` text blob, a GIF); assert status codes, the stored PNG is
  256×256 RGBA, padding is transparent, EXIF orientation is honoured, ETag/304 path, scope 403 for a read-only
  token, `logo_hash` on list and detail, cascade on account delete.

## 9. Rollout

0. Archive the finished `openspec/changes/account-brand-logos/` change first (its deltas touch the same
   requirements this change modifies; archiving order decides which text wins), so the new change's deltas are
   written against the merged spec text. Small housekeeping PR or the first commit of PR 1.
1. Merge PR 1 → deploy API via the release checkout (`alembic upgrade head`, venv gains Pillow). The host-side
   deploy script (`homehub-api-ctl` release recipe, not in the repo) adds `import PIL` to its import check; the
   app itself imports Pillow at startup through `account_logo_service`, so a venv without it fails fast.
2. SPA release via the publisher (blocked today by #279 — the static store cap; the API side can go first and
   Hermes can upload the Wise logo immediately, the SPA shows it once it is released).
3. Hermes skill text updated on the host.
4. First real use: owner or Hermes uploads the Wise logo (account id known to the owner) and checks the accounts
   list; then any other account whose slug is missing a mark (#186: union-bank, china-airlines, pionex) can be
   given an uploaded logo instead of waiting for a bundled one.
5. PR 2 (crop sheet) follows its own plan and review.

## 10. Left to the builder

- Exact SQLAlchemy loading strategy for `logo_hash` (selectinload vs scalar subquery) — measured against the
  existing `list_accounts` query count test if one exists.
- Nothing about threading: the account routes are sync `def` (FastAPI runs them in its threadpool), the logo
  routes are sync too, and the 413 pattern is `file.file.read(MAX + 1)` as in `imports.py`.
- Component placement/styling of the new avatar in the account page header on phone widths; it must not push
  the 對帳 / settings controls off the row.
- OpenSpec: deltas under `openspec/changes/account-logo-upload/` for `accounting-ledger` (routes, `logo_hash`,
  normalisation requirements) and `frontend-accounting-ledger` (precedence, entry points, error copy), written
  against the spec text **after** `account-brand-logos` is archived; strict validation with the current CLI.
