# Account logo upload — design

Rev 4, 2026-11-02 (rev 3 → rev 4 folds the AGENT-384 re-check: both Pillow bomb exceptions are caught at the
open/load boundary → 422 `logo_dimensions`; the normaliser shrinks first (`thumbnail`) and pastes onto a fresh
256×256 canvas, with an honest allocation bound and `MAX_LOGO_PIXELS` lowered to 25 M; the SPA store trusts the
response `ETag`, never the requested `v`, so a mismatch is cached under its real hash; the async PUT does all
synchronous work — normalise, DB write, detail build — in one `run_in_threadpool` call. Rev 2 → rev 3 folded the Multica spec review AGENT-384, dev-astra FAIL: the upload is a raw
image body read from the request stream under a byte budget instead of multipart, so nothing is spooled before the
cap; pixel-count check before decode with the bomb warning promoted to an error; `logo_hash` is unpacked from the
select row and never added to `ACCOUNT_FIELDS`; `immutable` only when `?v` matches the stored hash, `no-store`
otherwise; the SPA logo store gets an in-flight/generation contract; the settings page keeps the dirty form across
image writes; `frontend-ui-primitives` joins the OpenSpec deltas. Rev 1 same day; rev 2 folded the builder check: error bodies use the shared `{code, message,
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
not drag 72 PNGs along; a side table keeps the hot row small and lets `logo_hash` be a cheap scalar subquery.
Why `bytea` and not the filesystem: see anti-goals — the pg_dump backup and the release-checkout
deploys cover the database and nothing else.

`Account.logo` (bundled slug) is unchanged and independent: an account may have both. Deleting the uploaded
image falls back to the slug.

Alembic revision: new head, down-revision = current head at plan time, `upgrade` creates the table, `downgrade`
drops it. `test_migration.py` head pin updated.

## 5. API

All three routes live in `routers/accounts.py` under the existing router (`Depends(method_scope())`).

### `PUT /accounts/{account_id}/logo`

- Body: the **raw image bytes** (`Content-Type: image/png`, `image/jpeg` or `image/webp` — declared type is
  recorded for diagnostics and otherwise ignored). **Not multipart.** Starlette parses a multipart body before
  the handler runs and spools every file part to a temp file, so a `file.file.read(MAX + 1)` check inside the
  handler cannot stop a 2 GiB upload from hitting the disk first. A raw body read from `request.stream()` under
  a byte budget has no such pre-parse step; it also makes the Hermes call a one-liner (`--data-binary`) and the
  SPA call a `Blob` body.
- Limits, in order:
  1. If `Content-Length` is present and > `MAX_LOGO_UPLOAD_BYTES = 5 MiB` → 413 `logo_too_large` before reading
     any body byte.
  2. The route is `async def` (the only one in `accounts.py` — the stream read needs `await`) and reads
     `request.stream()` chunk by chunk into a buffer; the moment the running total exceeds 5 MiB it stops
     reading and returns 413 `logo_too_large` (chunked uploads without `Content-Length` included). An empty body
     → 422 `logo_empty`.
  3. After the stream is complete, **everything synchronous runs in one `run_in_threadpool(worker)` call**:
     `normalise_logo(data)`, the upsert of `account_logo_image`, `commit`, and `ledger_service.get_account` for
     the response. The SQLAlchemy `Session` from the `get_db` dependency is handed to the worker and used only
     there, sequentially — never from the event-loop thread, never from two threads at once. The async handler
     itself touches no ORM object; it only reads the stream and returns the worker's result. Acceptance: with
     the worker monkeypatched to sleep 1 s (slow DB / slow Pillow), a concurrent `GET /health` on the same app
     answers in well under that time (async test client, two tasks).
- Validation (`services/account_logo_service.py`, pure function `normalise_logo(data: bytes) -> NormalisedLogo`),
  **in this order, each step before any pixel is decoded**:
  1. `Image.open(BytesIO(data))` inside the exception boundary. The boundary (one `try` around open **and** the
     later `load`/transform steps) maps: `Image.DecompressionBombError` and `Image.DecompressionBombWarning`
     (promoted to an error, see 3) → 422 `logo_dimensions`; `UnidentifiedImageError`, `OSError`, `ValueError`
     → 422 `logo_unreadable`. `Image.open` reads only the header, but with `Image.MAX_IMAGE_PIXELS` set it already
     raises `DecompressionBombError` for > 2 × that many pixels — the 9000×9000 fixture takes exactly this path,
     so the bomb exceptions must be caught **here**, not only after the dimension check.
  2. Sniffed format `im.format` must be one of `PNG`, `JPEG`, `WEBP`; anything else (GIF, BMP, TIFF, SVG as text,
     ICO, PDF…) → 422 `logo_format`. Animated images: first frame only (`im.seek(0)` is the default).
  3. Dimensions from the header: both sides ≥ 16 px and ≤ 8192 px, **and** `width × height ≤ 25_000_000`
     (`MAX_LOGO_PIXELS`, i.e. 5000×5000), else 422 `logo_dimensions`. This is the gate that is relied on:
     Pillow's own `MAX_IMAGE_PIXELS` only warns between 1× and 2× and raises past 2×, so it is a second line,
     not the threshold. The module sets `Image.MAX_IMAGE_PIXELS = MAX_LOGO_PIXELS` and the normaliser runs
     under `warnings.catch_warnings()` with `DecompressionBombWarning` turned into an error; both the warning
     (25–50 M, e.g. a 6000×6000 fixture) and the error (> 50 M, e.g. 9000×9000) land in the boundary of step 1
     → `logo_dimensions`.
  4. Only now the pixels are decoded, **shrink first, square last**: `im.load()`; `im.thumbnail((256, 256),
     Image.LANCZOS, reducing_gap=3.0)` (in the image's native mode, in place, aspect ratio kept, so an
     8192×16 strip becomes 256×1 — never an 8192×8192 canvas); then `ImageOps.exif_transpose` and
     `convert("RGBA")` on the now ≤ 256×256 image; then paste it centred onto a fresh
     `Image.new("RGBA", (256, 256), (0, 0, 0, 0))`; encode PNG with `optimize=True`; no metadata chunks are
     written (fresh canvas, no `pnginfo`). Any Pillow error here → 422 `logo_unreadable`.
     Allocation bound, stated honestly: the decoded original (≤ 25 M px × ≤ 4 B = ≤ 100 MiB) plus the
     `reduce()` intermediate `thumbnail` creates (≤ ½ of that) are the only large buffers; transpose/convert
     operate on ≤ 256×256. Peak ≈ 1.5 × the decoded original ≈ 150 MiB worst case, one request at a time per
     worker thread. For JPEG the normaliser calls `im.draft("RGB", (512, 512))` before `load()` so the decoder
     itself downscales and the common case never allocates the full original. Acceptance: an 8192×16 PNG and a
     16×8192 PNG both produce a 256×256 RGBA PNG whose non-transparent pixels form a 256×1 / 1×256 line
     centred on the canvas, and peak RSS growth measured with `tracemalloc` on a 5000×5000 input stays under
     the stated bound.
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

- 200 `image/png` with the stored bytes; always `ETag: "<sha256>"`, `X-Content-Type-Options: nosniff`,
  `Content-Disposition: inline; filename="logo.png"`. `If-None-Match` matching the ETag → 304.
- Query `v` (optional) selects the cache policy, never the content — the server always serves the current image:
  - `v` present **and equal to the current `logo_hash`** (the 16-char prefix) → `Cache-Control: private,
    max-age=31536000, immutable`. The URL now names exactly these bytes, so a one-year cache is correct.
  - `v` missing or **not** equal to the current hash → `Cache-Control: no-store`. The client is holding a stale
    hash (the list was read before a re-upload); it gets the right picture but nothing is cached under a
    mismatching URL. The SPA refreshes its hashes through the normal account reload (`bumpAccounts` after any
    write) and the next fetch carries the right `v`.
  - This closes the A→B→A trap: a stale `?v=A` request never caches B under A's URL. Tests cover A→B→A and the
    list-read-then-PUT-then-GET interleaving.
- Clients SHOULD send `?v=<logo_hash>` from the account response they are rendering. The SPA store does; the
  Hermes text shows it too.
- 404 when the account does not exist or has no uploaded logo (the SPA falls back; it never shows a broken tile).
- `read` scope (a GET under `method_scope()`); the SPA fetches it through HttpClient so the interceptor adds the
  token.

### `AccountOut.logo_hash`

- `str | None`: first 16 hex chars of `sha256`, `None` when no uploaded image. Present on every `AccountOut`
  (list and detail) — it is a 16-char string, not an image. `AccountOut` is assembled from a dict built in
  `ledger_service.list_accounts` (`select(Account, …)` with labelled columns, no `from_attributes`), so
  `logo_hash` is one more labelled column on that select: a correlated scalar subquery `substr(sha256, 1, 16)`
  on `account_logo_image`. The value is taken from the unpacked row and written as `item["logo_hash"]` next to
  the other labelled values; it is **not** added to `ACCOUNT_FIELDS`, which is read through `getattr(account,
  field)` on the ORM object and has no such attribute. Every place that unpacks that select's row tuple is
  updated for the extra column. `get_account` (the detail and the write responses) goes through the same
  builder, so list, detail, `PUT /accounts/{id}` and `PUT /accounts/{id}/logo` all carry it; `None` when no
  image. No per-account query, and the PNG column is never selected outside the GET image route.
- `AccountIn` is unchanged: `logo_hash` is read-only; a client that sends it gets it ignored (schema ignores
  unknown fields as today).

### Errors summary

| case | status | detail |
|---|---|---|
| empty body | 422 | `logo_empty` |
| `Content-Length` > 5 MiB, or the stream exceeds 5 MiB | 413 | `logo_too_large` |
| not an image Pillow can open (header or pixels) | 422 | `logo_unreadable` |
| image but not PNG/JPEG/WEBP | 422 | `logo_format` |
| side < 16 px or > 8192 px, or width × height > 25 M, or Pillow bomb warning/error | 422 | `logo_dimensions` |
| unknown account | 404 | `account not found` (existing wording) |
| missing `write` scope | 403 | `scope` (existing) |

Wire shape, everywhere in this document: the shared error handler rewrites every `HTTPException` into
`{"code": <status>, "message": "<token>", "trace_id": …}` (`shared_lib/errors.py`), so the tokens above arrive in
`message`, e.g. `{"code": 422, "message": "logo_format", "trace_id": "…"}`; only FastAPI's own validation errors
keep the `detail` list (none of the logo routes rely on it). The SPA's `http-errors.ts` already reads both. Tests assert `message`.
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
- A new injectable `AccountLogoStore` (`services/account-logo-store.ts`, `providedIn: 'root'`):
  `url(accountId, hash): Signal<string | null>`. Contract:
  - State per account: `{hash, objectUrl, failed}` in a `Map<accountId, …>`, exposed through one signal per
    account id so every slot showing that account reads the same value.
  - Keyed by **(id, hash)**: a request is started only when the account's current hash differs from the one
    already resolved or in flight. In-flight requests are deduplicated in a `Map<"id:hash", Observable>` so ten
    slots of the same account trigger one fetch.
  - Generation guard: each account keeps the hash it last asked for; a response whose hash no longer equals the
    account's current hash is discarded (its blob is never turned into a URL), so a slow A cannot overwrite a
    newer B.
  - Fetch: `GET /api/accounting/accounts/{id}/logo.png?v={hash}` as `blob` through HttpClient with
    `observe: 'response'` (the token interceptor applies; same origin, so the `ETag` header is readable). 404 or
    any error marks `failed` for that (id, hash) so the slot falls back to the slug / emoji and no retry happens
    until the hash changes.
  - **The response `ETag` is the truth, not the requested `v`.** The store derives `actualHash` = the first 16
    hex chars of the `ETag` value. If `actualHash === requested`, the blob is stored as the resolved entry for
    (id, requested). If they differ (the server answered `no-store` with newer bytes), the blob is stored as the
    resolved entry for (id, **actualHash**) and the account's state becomes `{hash: actualHash, url}`; the slot
    shows the right picture immediately, the stale requested hash is never recorded as resolved, and the store
    asks `AccountingService` to refresh the accounts (`bumpAccounts`) so the models converge. A missing `ETag`
    is treated as a mismatch with an unknown hash: show the blob, cache nothing, refresh.
  - A→B→A end to end: SPA holds A; Hermes uploads B; the slot fetches `?v=A`, gets B/`ETag B`/`no-store` → entry
    (id, B); Hermes uploads A again; the account reload brings `logo_hash = A` ≠ store's B → refetch `?v=A` →
    `ETag A` → entry (id, A). Test this sequence against the store with a mocked HttpClient, not only the HTTP
    headers.
  - Revocation: when a new URL replaces an old one, or the hash becomes `null` (logo deleted), the old object URL
    is revoked **after** the signal has been updated, on the next macrotask, so `<img>` elements swap first; a
    URL that is still the current value of any account is never revoked. Nothing is revoked on component
    destroy (accounts are long-lived in the shell).
  - Tests: out-of-order responses (A slow, B fast → B shown, A dropped), dedupe (N slots → 1 request), delete →
    `null` + revoke, failure → fallback and no retry, hash change → refetch, ETag mismatch → cached under the
    actual hash + accounts refresh, missing ETag → shown but not cached, the A→B→A sequence above.
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
- Buttons: 上傳圖片 (file input, same accept list), 移除圖片 (only when an uploaded image exists; calls DELETE),
  and the existing 選擇內建 logo toggle for the grid picker. The grid picker and `pickLogo()` are unchanged; the
  hint under the block says the uploaded image takes precedence over the bundled choice.
- Upload here happens immediately (not deferred to 儲存), because the image is a separate resource from the
  `AccountIn` document; the copy says so (「上傳後立即生效」). On the `accounts/new` route the upload button is
  disabled with the hint 「先建立帳戶，再上傳 logo」.
- **The form is not reset by an image write.** `apply()` on this page rebuilds the form, the baseline and the
  credit-sharing state from a fresh detail; running it after an upload or a delete would silently drop whatever
  the owner had typed. The upload/delete handlers only patch the page's `logo_hash` (a dedicated signal read by
  the big icon and the state line) from the PUT response (or to `null` after DELETE); the dirty form, its
  baseline (so the 儲存 button's enabled state is unchanged) and the sharing members stay as they were. Test:
  edit the name → upload → the name field still holds the edit and 儲存 is still enabled; same for 移除.

### 6.4 Service + copy

- `AccountingService.uploadAccountLogo(id, file: Blob): Observable<AccountDetail>` — `PUT`
  `${apiUrl}/accounts/${id}/logo` with the `Blob` as the body and `Content-Type: <file.type || 'application/octet-
  stream'>`, inside `bumpAccounts` like `updateAccount`. No `FormData`.
- `AccountingService.deleteAccountLogo(id): Observable<void>` — `DELETE`, inside `bumpAccounts`.
- Error copy (toast): 413 → 「圖片太大（上限 5 MB）」; 422 `logo_format` → 「只接受 PNG、JPEG、WebP」; 422
  `logo_dimensions` → 「圖片尺寸不符（每邊 16–8192 px）」; 422 `logo_unreadable`/`logo_empty` → 「這個檔案無法當作圖片
  使用」; 404 on the PUT route → 「伺服器尚未支援上傳」; other → generic existing error toast. The client also
  refuses files > 5 MiB before sending (same message as 413) so the common case never leaves the browser.
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
  curl -fsS -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: image/png" \
    --data-binary @logo.png http://127.0.0.1:8000/accounts/<id>/logo
  ```
- to view what is stored: `GET /accounts/<id>/logo.png?v=<logo_hash>` (binary; save to a file, do not print).
- list accounts without any logo: `GET /accounts` rows with `logo_hash == null and logo == null`.
- never put the token or the owner's account names in chat artefacts; report the account id and the source URL.
The skill file lives outside the repo; the change is made on the host by the operator as part of the rollout,
like the reward rules skill text. It is rollout documentation, not part of the repository implementation that the
PR review covers.

## 8. Security review points (for the builder and the reviewer)

- The server never stores or serves uploaded bytes; only Pillow's re-encoded PNG.
- Resource caps, in order of application: `Content-Length` pre-check → streamed 5 MiB budget (no multipart
  parsing, nothing spooled before the cap) → header-only `Image.open` inside the boundary that also catches
  Pillow's bomb warning/error → format allow-list → side caps **and** `width × height ≤ 25 M` before `load()`
  → `thumbnail` before any mode conversion or canvas. Peak memory ≈ 1.5 × the decoded original (≤ 150 MiB),
  see §5 step 4; the 256×256 canvas is the only thing created at full RGBA after that.
- Event loop: the only async route does no synchronous work itself — stream read, then one worker call for
  Pillow + DB + response building (§5 limits step 3).
- Format allow-list is on the **sniffed** format (`im.format`), never on extension or declared type. SVG is text
  and fails `Image.open` → 422.
- Response headers on the GET: `nosniff`, `inline` disposition, fixed `image/png`. The SPA never injects the
  bytes anywhere but an `<img src="blob:…">`.
- No token in URLs: the SPA fetches via HttpClient. `GET` requires `read` like every other GET.
- Pillow pin matches the worker constraints (12.3.0); the release venv gets it through `requirements.txt`.
- Tests: fixtures are generated in the test (Pillow draws a 300×100 red rectangle; a 1-px image; a valid
  6000×6000 PNG — 36 M pixels, inside the side cap, over `MAX_LOGO_PIXELS`, Pillow warning band — and a valid
  9000×9000 PNG — side cap and Pillow error band — both 422 `logo_dimensions`; 8192×16 and 16×8192 strips for
  the shrink-first path;
  a `<svg>` text blob; a GIF; a JPEG with EXIF orientation 6); a body of 5 MiB + 1 byte with and without
  `Content-Length` (413 both ways, and the handler stops reading — asserted with a counting stream); an empty
  body (422 `logo_empty`). Assert status codes and `message` tokens, the stored PNG is 256×256 RGBA, padding is
  transparent, EXIF orientation is honoured, `ETag`/304, `immutable` only when `v` matches and `no-store`
  otherwise (A→B→A), scope 403 for a read-only token (auto-enumerated by `test_scope_enforcement`), `logo_hash`
  on list, detail and both PUT responses, `None` without an image, no PNG column in the list query, cascade on
  account delete.

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

- Where exactly the scalar subquery is attached in `list_accounts` and how the row unpacking is kept readable
  (a small named-tuple or a dict of labelled columns) — the contract in §5 is fixed, the shape of the code is not.
- Threading: `GET`/`DELETE` logo routes are sync `def` like their neighbours; only the `PUT` is `async def`
  because it reads the request stream, and it hands the bytes **and the session** to one worker
  (`run_in_threadpool`) that does normalise + write + commit + detail build, per §5. The builder picks the
  worker's shape (closure vs small function), not whether the DB work is in it.
- Component placement/styling of the new avatar in the account page header on phone widths; it must not push
  the 對帳 / settings controls off the row.
- OpenSpec: deltas under `openspec/changes/account-logo-upload/` for `accounting-ledger` (routes, `logo_hash`,
  normalisation requirements), `frontend-accounting-ledger` (precedence, entry points, error copy) **and**
  `frontend-ui-primitives` (the account-icon slot requirement currently says "bundled brand-logo set"; it must
  allow the uploaded image in the same slots with the same tile/fallback rules), written against the spec text
  **after** `account-brand-logos` is archived; strict validation with the current CLI.
