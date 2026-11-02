# Account logo upload — design

Rev 5.1, 2026-11-02 — store: a return to the shown hash cancels the other in-flight load; a new lease for an aliased hash drops the alias (probe round 7 findings 1 and 2). Rev 5.0 — lease-based logo store (icon acquires/releases; fetch driven by leases only). Rev 4.6 — succeeded-hash set replaces the stale-hash guard; silent interceptor for logo GETs. Rev 4.5 — unified store failure model (see notes). Rev 4.4 — Multica AGENT-388 fold: null consumers no longer clear a slot another consumer still uses (deferred clear); the stale-hash window re-resolves on expiry. Rev 4.3 — final-review fixes: small inputs are enlarged to fit; 404 copy split; If-None-Match list parsing. Rev 4.2 — implementation notes appended at the end (PR 1 execution: P-mode expansion, store missing-ETag and
stale-hash guard semantics, icon null coalescing); no contract change. Rev 4.1 — Multica AGENT-384 PASS-with-minor at rev 4 `5cf90e6a`; the two minors are folded here: the
memory figure is a budget to be measured (OS peak RSS in a subprocess), not a proven bound, and the store's
late-response guard compares the request generation, never the response hash. Rev 3 → rev 4 folded the AGENT-384 re-check: both Pillow bomb exceptions are caught at the
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
- `PUT /accounts/{id}/logo` (raw image body, `write` scope) stores a normalised PNG for the account;
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
     8192×16 strip becomes 256×1 — never an 8192×8192 canvas); an input whose longer side is < 256 px is
     instead enlarged to fit the 256×256 box (`ImageOps.contain(..., Image.Resampling.LANCZOS)`, aspect kept)
     rather than padded as a speck; then `ImageOps.exif_transpose` and
     `convert("RGBA")` on the now ≤ 256×256 image; then paste it centred onto a fresh
     `Image.new("RGBA", (256, 256), (0, 0, 0, 0))`; encode PNG with `optimize=True`; no metadata chunks are
     written (fresh canvas, no `pnginfo`). Any Pillow error here → 422 `logo_unreadable`.
     Memory: the decoded original is ≤ 25 M px × ≤ 4 B = ≤ 100 MiB; `thumbnail` adds a `reduce()` intermediate
     and, for RGBA/LA inputs, Pillow's resize also makes a premultiplied-alpha copy of what it resizes, so the
     peak is a small multiple of the decoded original, **not** "original plus a little". No fixed figure is
     promised here. The plan measures it: a subprocess normalises a 5000×5000 RGBA and a 5000×5000 LA fixture
     (fixture creation in a separate step so it is not counted) and records OS peak RSS
     (`resource.getrusage(RUSAGE_CHILDREN).ru_maxrss`), and the test pins a conservative budget derived from that
     measurement with headroom; `tracemalloc` is not used (it counts traced Python allocations, not process
     memory). Transpose/convert operate on ≤ 256×256. One request at a time per worker thread. For JPEG the
     normaliser calls `im.draft("RGB", (512, 512))` before `load()` so the decoder itself downscales and the
     common case never allocates the full original. Acceptance: an 8192×16 PNG and a 16×8192 PNG both produce a
     256×256 RGBA PNG whose non-transparent pixels form a 256×1 / 1×256 line centred on the canvas.
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
  1. the store's slot for `accountId` holds an image → uploaded image (object URL);
  2. else bundled slug as today;
  3. else emoji / fallback.
- A new injectable `AccountLogoStore` (`services/account-logo-store.ts`, `providedIn: 'root'`), lease model
  (rev 5.0). API:
  `slot(accountId): Signal<string | null>` — the account's read-only image signal (object URL or null), created
  lazily; reading it never fetches. `acquire(accountId, hash): LogoLease` — registers a live consumer binding and
  may start a fetch; `lease.release()` (idempotent) removes it. Contract:
  - **Leases drive fetching, not reads.** Every `<app-account-icon>` holds one lease for its current
    (`accountId`, `logoHash`), taken in an `effect` and released by its cleanup (the pair changed or the icon was
    destroyed); `uploadedUrl` is a `computed` over `slot(id)` only. The store therefore knows which hashes are
    live and never confuses "the model moved to X" (a new lease) with "the same consumer re-read X" (nothing),
    which is what made the pull model loop or show the wrong image.
  - **Target** per account = the hash of the newest non-null lease (null when every lease is null or none
    exists). `evaluate()` runs on acquire, on a release that changes the target, and once when a failure
    window expires; it fetches the target's *effective* hash (`aliases`, below) unless that hash is already
    shown, in flight, or inside its 30 s failure window. Nothing else ever starts a GET. When the effective
    hash is the one already shown while a load for another hash is in flight, that load is cancelled
    (generation bump + unsubscribe), so a flip B → A → B before A answers, or the newest icon being destroyed
    and recreated beside an older-hash icon, keeps the shown image (rev 5.1).
  - Generation guard: each fetch bumps a per-account `generation`; only the current generation's response is
    honoured, success or error — a superseded response is ignored entirely (no object URL, no `failed` entry).
  - Fetch: `GET /api/accounting/accounts/{id}/logo.png?v={hash}` as `blob` with `observe: 'response'` and the
    `SILENT_HTTP_ERRORS` request context (no retry, no toast). A null or 0-byte body is a failure.
  - **The response `ETag` is the truth.** `actual` = first 16 hex of an unquoted strong `ETag`, else the
    requested hash (missing, `W/` or non-hex → requested, no refresh). The slot shows the blob and `shownHash`
    becomes `actual`. When `actual ≠ requested`, `aliases[requested] = actual` (chains are flattened) and the
    store refreshes the accounts once per alias creation so the models converge; the leases already live for
    the requested hash are satisfied by the alias and cost no GET. An alias describes the leases that were live
    when it was learned: a **new** lease for an aliased hash drops the alias (and its refresh mark) before
    evaluating, so a genuine re-upload of that earlier logo is fetched once more; against a server still
    answering the newer bytes the alias is simply re-created (one GET and one refresh per such acquire;
    rev 5.1).
  - Failure: the shown image stays; the hash joins `failed` and one 30 s timer per account (replaced by every
    failure) empties `failed` on expiry and evaluates once. So a live target that keeps failing costs at most
    one GET per 30 s, and nothing happens when no lease wants an unresolved hash.
  - Deletion: when the target becomes null (every lease null or released) a deferred clear runs on the next
    macrotask unless a non-null lease arrives first; it bumps the generation (in-flight responses dropped),
    forgets `aliases` and `failed`, blanks the slot and revokes the URL on the following macrotask. Null leases
    stay registered and do nothing; a release + re-acquire of the same hash in one macrotask costs nothing.
  - A→B→A end to end: SPA holds A → GET A → shows A; the owner (or Hermes) uploads B → the list reloads, the icon
    releases A and acquires B → GET B → shows B; A is uploaded again → acquire A → A is the newest lease → GET A
    → shows A. Three GETs, the right image every time (the rev 4.6 "succeeded" edge is gone).
  - Revocation: the old object URL is revoked **after** the signal has been updated, on the next macrotask, and
    never while it is any account's current value. `ngOnDestroy` cancels every timer and in-flight load and
    revokes every URL the store still owns.
  - Bounds: GETs per account ≤ non-null acquires + releases that change the target to another non-null hash,
    plus at most one per 30 s while the live target keeps failing; accounts refreshes ≤ mismatching 200s ≤
    GETs. A seeded random-sequence spec (200 steps, per-hash and per-account stubs) pins the bound and the final
    image. Accepted edge: a one-shot stale detail acquired *after* the list (so it is the newest
    lease) whose old bytes the browser cache serves with the old `ETag` shows the old image until the next
    acquire.
  - Tests (`account-logo-store.spec.ts`, scenario matrix a–p; `account-icon.spec.ts` end to end through a host
    with two icons): dedupe, generation guard, ETag mismatch alias + one refresh, missing / weak / non-hex ETag,
    stale null lease, deletion, release + re-acquire in one macrotask, A→B→A (real server, server answering B,
    browser-cache variant), stale detail + list in both orders, both 404 (one GET per window), A 404 then B 200,
    transient error, 0-byte body, destroy, silent context, NG0600 (slot in a computed, acquire in an effect).
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
  → `thumbnail` before any mode conversion or canvas. Peak memory is a small multiple of the decoded original
  (≤ 100 MiB), measured and budgeted in the plan per §5 step 4; the 256×256 canvas is the only full-RGBA
  object created after the shrink.
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

## Implementation notes (rev 4.2, 2026-11-02)

Deviations settled during PR 1 execution; each one is covered by a test named in the plan ledger.

- **Normaliser (§5):** palette (`P`) images are expanded to RGBA *before* `thumbnail()`, because Pillow's
  reducing path cannot resample palette images; the measured peak RSS stays inside the 384 MiB budget.
- **Rev 5.0, store lease model (§6.1):** replaces every store bullet of rev 4.2–4.6 (missing-`ETag` rule,
  mismatch guard, `succeeded` set, unified failure model, null-hash deferred clear). The icon holds one lease per
  (`accountId`, `logoHash`) through an `effect` with `onCleanup(() => lease.release())`; `uploadedUrl` reads
  `store.slot(id)` only, and an undefined hash is coalesced to `null` before `acquire()`. Store rules: (1) target =
  hash of the newest non-null lease, `effective(h) = aliases.get(h) ?? h`; (2) `acquire(id, h)` records the lease;
  a non-null `h` cancels a pending clear and evaluates, a null `h` with a null target schedules the deferred clear;
  `release()` removes the lease and schedules the clear when the target is now null, or evaluates when the target
  changed to another non-null hash; (3) `evaluate()` fetches `effective(target)` unless it equals `shownHash`, is
  `pending`, or is in `failed`; (4) only the current generation's response counts; success sets `shownHash` to the
  `ETag` hash (else the requested one), records the alias and refreshes the accounts once per (account, requested)
  when they differ, swaps the URL and revokes the old one on the next macrotask; `evaluate()` is not called after a
  success; (5) a current failure (HTTP error, null or 0-byte body) keeps the image, adds the hash to `failed` and
  arms/replaces one 30 s timer per account that empties `failed` and evaluates once; (6) the deferred clear fires
  only while the target is still null: generation bump, `pending`/`aliases`/`failed`/`refreshed` reset, fail timer
  cleared, slot null, URL revoked next macrotask; (7) `ngOnDestroy` cancels every timer and in-flight load and
  revokes every held URL, and every async callback checks the destroyed flag; (8) every synchronous signal write
  runs inside `untracked()` (acquire from an effect, slot from a computed). Slot signals use plain equality — no
  force-notify anywhere.
- **Rev 5.1, cancel on return; new lease drops its alias (§6.1):** probe round 7 found two bounded
  wrong-image defects in rev 5.0. (1) `evaluate()` returned early when `effective === shownHash` even while a
  current-generation load for another hash was in flight, so a flip B → A → B before A answered (or the newest
  icon destroyed and recreated beside a stale older-hash icon) ended on A. Now that case cancels the pending
  load: generation bump, the tracked `pendingSub` unsubscribed and forgotten, `pending = null`; the cancelled
  load leaves no `failed` entry and no timer. (2) `aliases[A] = B` was learned when A was stale and survived
  until the deferred clear, so a genuine re-upload of A (every lease moving to A) resolved to the shown B with
  0 GETs. Now `acquire(id, h)` with a non-null `h` deletes `aliases[h]` and `refreshed[h]` before evaluating:
  a new lease for an aliased hash fetches it once. Against the real server still at B the alias is re-created
  with one GET and one `refreshAccounts()` per such acquire ("once per alias creation", chosen over "once per
  pair ever" because the refresh is what converges a stale one-shot detail, and it is bounded: refreshes ≤
  mismatching 200s ≤ GETs ≤ acquires + target-changing releases + windows). Leases already live for the
  aliased hash keep costing nothing.
- **Rev 5.0, bounds and accepted edge (§6.1):** GETs per account ≤ acquires + target-changing releases, plus at
  most one per 30 s while the live target keeps failing; the scenario matrix pins the counts (N same leases 1;
  A then B 2; A→B→A 3; stale detail + list 2 in either order; both 404 2 then 1 per window; A 404 / B 200 2, then
  1 when the window ends with A the target again). Accepted edge: a one-shot stale detail acquired after the list
  is the newest lease; when the browser cache serves its old bytes with the old `ETag` the old image shows until
  the next acquire (a real server answers the current bytes with their `ETag`, which the alias handles with one
  refresh). Stale detail acquired *before* the list costs nothing.
- **Rev 4.3, normaliser upscale branch (§5 step 4):** when `max(size) < 256` the image is enlarged with
  `ImageOps.contain` (LANCZOS) instead of `thumbnail()`, which never enlarges; inputs with a side ≥ 256 keep
  the `thumbnail` shrink path, so the memory bound is unchanged. Both branches resize before
  `exif_transpose` (the longer side and the square box are orientation-invariant).
- **Rev 4.3, 404 copy split (§6.4):** a 404 whose `message` is `account not found` (account deleted
  elsewhere) shows 「帳戶不存在」; any other 404 keeps 「伺服器尚未支援上傳」. Applies to the PUT and DELETE
  error toasts. `GET logo.png` also parses `If-None-Match` as a list (`W/` and quotes stripped, `*` matches).
- **Rev 4.6, silent interceptor (§6.1):** `error-logging.interceptor.ts` exports the request context token
  `SILENT_HTTP_ERRORS`; a request carrying it bypasses the interceptor (no `retry(1)`, no console group, no
  toast; the error still reaches the subscriber). The store's logo GET sets it; the PUT / DELETE upload calls
  keep the normal path. Without it every failed logo GET went over the wire twice and raised a red toast.
