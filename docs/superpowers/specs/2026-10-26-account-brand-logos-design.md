# Account brand logos: a built-in logo set for accounting accounts (#175) — design

Status: rev 2 (2026-10-26, after the builder check: nothing important left). Owner's wish (2026-10-25, while reviewing the line-icon sweep):
"我之後會想把一部分 ex: 國泰就找真的icon來放這樣" — MOZE showed a bank logo next to every account in its account list;
HomeHub shows an emoji. Decisions marked *[call]* are mine; everything else the owner said.

## 1. Intent

Accounting accounts carry `icon` (an emoji, VARCHAR(16)). The owner wants the account list, the account picker and the
account settings page to show the institution's real logo for accounts that belong to a bank / card issuer / broker,
the way MOZE did, and to keep the emoji for accounts that have no brand (錢包, 去日本的錢, 應收應付).

Success: after the release every brand account in the owner's ledger shows its institution's logo wherever account
icons appear, a MOZE backup import gives new accounts the right logo automatically, and the owner can change or clear
a logo from the account settings page without asking anyone.

## 2. Goals, non-goals, anti-goals

Goals
- A built-in set of ~30 institution logos shipped with the SPA (no runtime fetch from third parties).
- `account.logo` (slug into the set, nullable) next to `account.icon`; a logo wins over the emoji wherever account
  icons render; the emoji stays the fallback.
- The MOZE backup importer maps `AHAccount.imageName` → logo slug for accounts it creates (and for existing accounts
  under the same rule it already uses for `icon`).
- Account settings page: a logo picker (grid of the set + 無) beside the existing 圖示 emoji input.
- One-off: the owner's existing accounts get their logos applied after the release (section 9).

Non-goals
- No per-account image upload, no arbitrary URLs, no logos for categories or counterparties.
- No logo on timeline / entry / split rows (they show the category icon) and nothing on the portfolio pages (the
  portfolio service has its own accounts).
- No change to MOZE's image assets: the backup carries only the code (`imageName`), never the picture, and MOZE's
  pictures are not reused.
- The 收件匣 group header (`inbox-panel`) keeps its 💳 / 🏦 marker *[call]*: its `InboxAccount` payload has no icon
  field today and the header is a label, not an icon slot. Follow-up issue if the owner wants it.

Anti-goals
- A broken-image glyph or a layout shift when a logo is missing, slow to load or unknown to the SPA.
- The SPA requesting a bank's web site for an image at runtime (privacy, availability).
- Two sources of truth for the set that drift (a slug the importer writes that the SPA cannot draw).

## 3. Facts the design rests on (verified 2026-10-26 on main 5b519a2)

- `Account.icon` is `String(16)` nullable (`services/accounting-service/app/models/ledger.py:118`); `moze_id`
  `String(64)` nullable unique (:134) holds MOZE's `identifier`. Alembic head `a7c3e1d9b2f4` (`alembic/versions/
  a7c3e1d9b2f4_owner_rules.py`).
- `AccountIn` (`app/schemas/writes.py:186`) has `icon: Annotated[str, Field(max_length=16)] | None` (:192) and no
  other icon validation; `AccountOut` (`app/schemas/ledger.py:44`, icon :54); the read whitelist `ACCOUNT_FIELDS`
  (`app/services/ledger_service.py:48-52`) decides which columns reach `AccountOut`. `create_account` /
  `update_account` (`app/services/settings_service.py:551/561`) copy `model_dump()` onto the row, so a new `AccountIn`
  field flows through without more code; `update_account` sets `settings_locally_edited = True`.
- Router `app/routers/accounts.py`: `GET /accounts`, `POST /accounts`, `PUT /accounts/{id}` (full `AccountIn`),
  `DELETE /accounts/{id}`; archiving = `PUT` with `is_archived`.
- Importer `app/services/moze_backup_import_service.py:280-302` (`_upsert_accounts`): new accounts are created with
  `moze_id = record["identifier"]`; `icon` is set from `ICON_BY_GROUP` only when `account.icon is None and not
  account.settings_locally_edited` (:301-302). `AHAccount` schema (`app/services/moze_backup_json.py:286-293`) already
  carries `imageName` (:291); `ICON_BY_IMAGE` (:231) maps category image names → emoji; `ICON_BY_GROUP` (:255) maps
  group keys → emoji.
- Frontend `LedgerAccount` (`frontend/src/app/models/accounting.model.ts:57`, `icon: string | null` :68),
  `AccountInput` (:400). Account-icon render sites, all `{{ account.icon ?? '<emoji>' }}` text:
  `components/accounting/accounts/accounts.html:21` (`.ico`, fallback 📁), `account-picker/account-picker.html:15`
  (trigger `.acct-ico`, 🏦), `:51` (最近使用 chip, inline text), `:71` (option row `.acct-ico`),
  `account-settings/account-settings.html:18` (`.bigicon .ico`, 📁), `reminders/reminders.html:45` (card rows; icon
  map built in `reminders.ts:157-169`, fallback 💳). `.ico` in `accounts.scss:62` is a `var(--ico-size)` circle with
  `--app-surface-soft` background.
- Account settings editor: `AccountForm` (`account-settings.ts:40`), `formFrom` (:131, `icon: account.icon ?? ''`),
  `formToInput` (:~160, `icon: textOrNull(form.icon)`), `accountToInput` (:106); template `.rw` 圖示 row
  (`account-settings.html:58-64`) with `input.icon-input maxlength=16` and the colour input.
- Assets: `angular.json` serves `public/` at the root and `src/assets/` at `assets/`; the only static `<img>` is the
  sidebar's `assets/app-icon.svg` (relative URL). No shared icon component exists; PrimeIcons are used as
  `<i class="pi pi-…" aria-hidden="true">`.
- Line-icon gate `frontend/src/app/theme/line-icons.spec.ts`: static emoji in markup fails; emoji inside `{{ }}` pass;
  allowed glyphs ✕ ✓ ⏎. Spec `frontend-ui-primitives` "Line icons for interface chrome" already allows emoji in
  owner-chosen account icon slots with default-emoji fallbacks.
- OpenSpec: `accounting-ledger` "Account model" (:16 `icon … an emoji`), `accounting-moze-backup-import` "Accounts"
  bullet (:121, "icon by group … when the account has none"), `frontend-accounting-ledger` "Account settings page"
  (:144) and the accounts list requirement (:12 "icon and name"), `frontend-ui-primitives` (:126).
- Egress from the VPS works but is uneven (one bank site timed out, one answers 403 to curl), so the logo fetch needs
  a fallback source (section 4).

## 4. The logo set

**Location** *[call]*: `frontend/src/assets/logos/<slug>.svg` (PNG ≥ 180 px square only when no vector exists), served
at `assets/logos/<slug>.svg` like the app icon. Shipped in the SPA bundle; no CDN, no runtime fetch elsewhere.

**Manifest** *[call]*: `frontend/src/app/theme/account-logos.ts` exports
`ACCOUNT_LOGOS: readonly AccountLogo[]` with `{ slug, label, file }` (label = the institution's Chinese short name
as the owner uses it, e.g. 國泰世華, 匯豐, 星展) and `accountLogo(slug: string | null): AccountLogo | undefined`.
The file list on disk is the contract: a frontend spec asserts every manifest `file` exists, every file on disk is in
the manifest, every SVG has a square `viewBox` (width == height), and every slug matches `^[a-z0-9-]{1,32}$`.

**Attribution** *[call]*: `frontend/src/assets/logos/SOURCES.md` — one row per logo: slug, institution, source URL,
fetch date, "trademark of its owner; bundled for this household's private app, not redistributed".

**Sourcing** (owner: 「你去官網抓，統一裁成方形」): the builder fetches each mark from the institution's official site
(brand / press page, site header SVG, or `apple-touch-icon` / favicon SVG); when the site blocks or serves only
raster, use the official mark as hosted on Wikimedia Commons (SVG) *[call]*; last resort the official site's
`apple-touch-icon` / PWA icon PNG (≥ 180 px, already square, brand background kept) *[call]*. A probe on
2026-10-26 found Commons SVGs for some institutions (國泰世華, 匯豐, 中華郵政) and none for others (台新, 華南, 星展,
LINE Bank, Firstrade, Pionex), so expect a mixed SVG / PNG set. Normalise every file: square viewBox, the mark centred with ~8 % padding, no background rectangle
(the SPA draws the tile), colours untouched. A logo that cannot be obtained in acceptable quality is left out of
the set and listed in the PR body; its accounts keep their emoji. Downloaded files are untrusted input: fetch into
an empty scratch directory, inspect (`file`, size, an SVG parse that rejects `<script>`, `<foreignObject>`, external
`href`), strip metadata, and only then copy into `src/assets/logos/`.

**Slugs** (institution as the owner confirmed on 2026-10-25):

| slug | institution | slug | institution |
|---|---|---|---|
| `standard-chartered` | 渣打 | `chang-hwa` | 彰銀 |
| `bank-of-taiwan` | 臺灣銀行 | `starbucks` | 星巴克 |
| `ctbc` | 中國信託 | `kgi` | 凱基 |
| `taishin` | 台新 | `richart` | 台新 Richart |
| `cathay-united` | 國泰世華 | `line-points` | LINE Points |
| `hsbc` | 匯豐 | `dawho` | 永豐 DAWHO |
| `sinopac` | 永豐 | `rakuten-bank` | 樂天銀行 |
| `hua-nan` | 華南 | `sinopac-securities` | 永豐金證券 |
| `yuanta` | 元大 | `interactive-brokers` | Interactive Brokers |
| `taipei-fubon` | 台北富邦 | `firstrade` | Firstrade |
| `line-bank` | LINE Bank | `dbs` | 星展 |
| `amex` | 美國運通 | `next-bank` | 將來銀行 |
| `union-bank` | 聯邦 | `pionex` | Pionex |
| `chunghwa-post` | 中華郵政 | `cota` | 高雄三信（三信商業銀行） |
| `china-airlines` | 華航 | `charles-schwab` | Charles Schwab |

`richart` and `dawho` are separate slugs so the sub-brand mark can be used when it exists; if only the parent bank's
mark is obtainable, their files are copies of `taishin` / `sinopac` *[call]*.

## 5. Data model and API

- Migration `<rev>_account_logo.py` (down `a7c3e1d9b2f4`): `ALTER TABLE account ADD COLUMN logo VARCHAR(32) NULL`.
  No backfill (section 9 does it through the API).
- `Account.logo = Column(String(32), nullable=True)`; `ACCOUNT_FIELDS` gains `"logo"`; `AccountOut.logo: str | None`;
  `AccountIn.logo: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,32}$")] | None = None`. The API validates the shape
  only, not membership in the set *[call]*: the set lives in the SPA, and an unknown slug renders as the emoji
  fallback (section 6), so the two never need to be released together.
- `PUT /accounts/{id}` keeps its full-payload semantics; `logo` omitted = cleared, as for `icon`.
- Spec `accounting-ledger` "Account model": add `logo (VARCHAR(32), nullable; a slug of the SPA's built-in brand-logo
  set, `^[a-z0-9-]{1,32}$`)`; the accounts list / detail read fields include `logo`.

## 6. Rendering

**Component** *[call]*: `components/accounting/account-icon/account-icon.ts` (`<app-account-icon>`, standalone,
OnPush), inputs `logo: string | null`, `icon: string | null`, `fallback: string` (the slot's current default emoji),
`size: 'row' | 'big'` (defaults `row`). Renders:
- logo known to the manifest → `<img class="logo" [src]="'assets/logos/' + file" alt="" aria-hidden="true" width height
  loading="lazy" decoding="async">` on a white tile (`background: #fff` in both themes — brand marks are designed for
  white; token `--app-logo-tile` so it can be themed later), `border-radius` inherited from the host slot,
  `object-fit: contain`, fixed box = the slot's `--ico-size`, so a slow or missing image never shifts layout;
  `(error)` on the image flips a signal and the slot falls back to the emoji.
- otherwise → `<span class="emoji">{{ icon || fallback }}</span>` (the emoji stays inside an interpolation, so the
  line-icon gate is unaffected).

**Slots** (all today's account-icon sites; each keeps its current class, size and fallback emoji):

| site | fallback |
|---|---|
| `accounts.html:21` `.ico` | 📁 |
| `account-picker.html:15` trigger, `:71` option row (`.acct-ico`) | 🏦 |
| `account-picker.html:51` 最近使用 chip — the inline `{{ icon }} {{ name }}` text becomes `<app-account-icon>` + name | 🏦 |
| `account-settings.html:18` `.bigicon .ico` (`size="big"`) — shows the picked logo live while editing | 📁 |
| `reminders.html:45` card rows — `cardReminders()` carries `logo` beside `icon` (`Pick<LedgerAccount, 'id' | 'icon' | 'logo'>`) | 💳 |

`LedgerAccount.logo: string | null` and `AccountInput.logo: string | null`; every frontend fixture that builds a
`LedgerAccount` gains `logo: null` (or the TS compiler names the places).

## 7. Account settings: the picker

In the 圖示 row (`account-settings.html:58-64`) the emoji input and the colour input stay. A new row 品牌 logo
*[call]* above it holds a button showing the current choice (the logo tile + its label, or 無) that toggles an inline
grid under the row: first cell 無 (clears `logo`), then every manifest entry in manifest order, each a
`<button type="button" [attr.aria-pressed]>` with the tile and the label under it (44 px tiles, wraps, keyboard
reachable; the grid closes on a choice). `AccountForm.logo: string | null`; `formFrom` copies `account.logo ?? null`;
`formToInput` passes it through; `accountToInput` copies `account.logo`. Choosing a logo does not touch the emoji
(it stays as the fallback). The `.bigicon` reflects the choice immediately through the form signal.

Spec `frontend-accounting-ledger` "Account settings page": the header shows the account's logo when set, else its
emoji; a 品牌 logo row offers the built-in set and 無. Accounts list requirement: "logo or icon, and name". New
scenarios: logo set → `<img>` rendered, emoji not; logo unknown / image fails → emoji; picker clear → `logo: null`
in the saved payload.

Spec `frontend-ui-primitives` "Line icons for interface chrome": add "An owner-chosen account logo image from the
app's bundled brand-logo set MAY render in the same slots" so the requirement does not read as forbidding `<img>`.

## 8. Importer

`moze_backup_json.py`: `LOGO_BY_IMAGE: dict[str, str]` (MOZE `imageName` → slug):

| imageName | slug | imageName | slug |
|---|---|---|---|
| `AccountCollection01_02` | `standard-chartered` | `AccountCollection06_01` | `kgi` |
| `AccountCollection01_04` | `bank-of-taiwan` | `AccountCollection06_02` | `chang-hwa` |
| `AccountCollection01_08` | `ctbc` | `AccountCollection06_04` | `richart` |
| `AccountCollection01_11` | `taishin` | `AccountCollection06_10` | `line-points` |
| `AccountCollection01_12` | `cathay-united` | `AccountCollection09_20` | `taipei-fubon` |
| `AccountCollection01_13` | `hsbc` | `AccountCollection10_01` | `dawho` |
| `AccountCollection01_14` | `sinopac` | `AccountCollection10_03` | `rakuten-bank` |
| `AccountCollection01_16` | `hua-nan` | `AccountCollection10_05` | `cathay-united` |
| `AccountCollection01_17` | `yuanta` | `AccountCollection10_06` | `sinopac-securities` |
| `AccountCollection01_20` | `taipei-fubon` | `AccountCollection10_18` | `interactive-brokers` |
| `AccountCollection02_01` | `line-bank` | `AccountCollection10_19` | `firstrade` |
| `AccountCollection02_05` | `amex` | `AccountCollection11_02` | `hsbc` |
| `AccountCollection02_07` | `union-bank` | `AccountCollection11_05` | `dbs` |
| `AccountCollection02_10` | `chunghwa-post` | `AccountCollection13_05` | `next-bank` |
| `AccountCollection02_11` | `chang-hwa` | `AccountCollection20_01` | `pionex` |
| `AccountCollection02_17` | `union-bank` | `Account_02` | `cota` |
| `AccountCollection05_15` | `starbucks` | | |

`Account_05`, `Account_24`, `Payable` and every other code are absent from the map (generic MOZE pictures → no logo).
The pairs that map two codes to one slug are MOZE's old and new picture of the same bank (owner, 2026-10-25).

`_upsert_accounts`: on a **new** account `account.logo = LOGO_BY_IMAGE.get(record["imageName"])`; on an **existing**
account `logo` follows the `icon` rule at :301-302 — set only when `account.logo is None and not
account.settings_locally_edited`. A unit test asserts every slug in `LOGO_BY_IMAGE` has a file under
`frontend/src/assets/logos/` (path resolved from the repo root; the test is skipped, not failed, when the frontend
tree is absent, e.g. in a service-only checkout) *[call]*.

Spec `accounting-moze-backup-import` "Accounts" bullet: add `imageName → logo per the design's table (no logo for
generic pictures)` next to "icon by group".

## 9. Rollout and the one-off apply

1. Merge; deploy the accounting API first (`alembic upgrade head` + restart through the usual `homehub-api-ctl`
   path), then the SPA release. An SPA that sends `logo` before the API knows it would be rejected, never the other
   way round.
2. One-off apply (by me, after the owner's go, from the private 2026-10-25 table of the owner's 72 accounts): for
   each brand account `GET /accounts/{id}` → `PUT /accounts/{id}` with the same payload plus `logo`. The two
   accounts under MOZE's generic `Account_05` picture that the owner wants branded anyway (華航哩程 → `china-airlines`,
   Charles Schwab → `charles-schwab`) are in that list; 三信薪轉戶 gets `cota` and `is_archived = true` (owner: 「也可以
   封存了」). The script runs with an admin-scoped token, prints counts only, and its (id → slug) input stays outside
   the repo (owner data).
3. Evidence for the PR: Chromium screenshots of the accounts list, picker, settings page and 🔔 card rows with
   fictional fixtures, light + dark, 1440 / 390.

## 10. Left to the builder

- Exact tile padding / radius values inside the component; the grid's responsive columns; whether the 品牌 logo
  row shows the label text beside the tile at 390 px.
- SVG normalisation tooling (hand-edited viewBox vs a small script); PNG fallback dimensions beyond "≥ 256 px, square".
- Test file layout; fixture updates; alembic revision id.
- Whether `reminders.ts` keeps one `Map` of icons and one of logos or a `Map<id, Pick<LedgerAccount, …>>`.
