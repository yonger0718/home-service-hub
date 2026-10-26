# Account brand logos (#175) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accounting accounts get an optional `logo` (slug into a bundled set of ~30 institution logos) that renders instead of the emoji in every account-icon slot, is picked on the account settings page, and is filled by the MOZE backup importer.

**Architecture:** One nullable column + API field on the accounting service (shape-validated slug, no set membership); the logo set and its manifest live in the SPA (`src/assets/logos/` + `theme/account-logos.ts`), so an unknown slug simply falls back to the emoji. One standalone `<app-account-icon>` component replaces the five `{{ account.icon ?? '…' }}` slots; the settings page gains a 品牌 logo row with an inline grid picker; the importer maps MOZE `imageName` codes to slugs. Spec deltas travel as an OpenSpec change `account-brand-logos`.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 (Python 3.13, pytest); Angular 21 standalone / signals / OnPush / zoneless, Vitest; OpenSpec CLI.

**Spec:** `docs/superpowers/specs/2026-10-26-account-brand-logos-design.md` (rev 2). Rev 1 recon facts: `/home/opc/.claude/jobs/2741e73c/tmp/f175-recon.md` (controller only).

## Global Constraints

- Branch `feat/account-brand-logos` in worktree `/home/opc/workspace/home-hub-redesign` (from origin/main 5b519a2). Never touch other branches, never `git stash`, `git add` explicit paths.
- Slug shape everywhere: `^[a-z0-9-]{1,32}$`. Column `account.logo VARCHAR(32) NULL`. API validates shape only.
- Logo files: `frontend/src/assets/logos/<slug>.svg` (or `.png`, ≥ 180 px, square); served at `assets/logos/<file>` (relative URL, no leading slash, like `assets/app-icon.svg`). No runtime fetch from any third-party host.
- Every SVG must have a square `viewBox` (width == height); no `<script>`, `<foreignObject>`, `<image>`, no `on*` attributes, no `href` / `xlink:href` that does not start with `#`; `<metadata>` stripped.
- Downloaded files are untrusted: fetch into an empty scratch directory outside the repo (`/home/opc/.claude/jobs/2741e73c/tmp/logo-fetch/`), run checks with `python3 -I`, copy into the repo only after they pass.
- Emoji may appear in templates only inside `{{ … }}` interpolations (gate `frontend/src/app/theme/line-icons.spec.ts`); line icons are PrimeIcons `<i class="pi pi-…" aria-hidden="true">`. Colours via `--app-*` tokens only; new token `--app-logo-tile` = `#fff` in light and dark.
- Copy strings (exact): row label `品牌 logo`, clear option `無`, 圖示 row unchanged.
- Fixtures fictional (玉山 …, A-… ids); no owner account names or balances in tests, PR text or reviewer comments.
- Tests: backend `cd services/accounting-service && pytest tests/…`; frontend `cd frontend && npx ng test --watch=false --include '<glob>'` (quote globs, zsh). OpenSpec: `npx -y @fission-ai/openspec@latest validate account-brand-logos --strict` from the repo root.
- Commit trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A `logo` slug the SPA does not know (removed from the set, typo via API) → the slot shows the emoji, never a broken-image glyph. (Task 4)
2. The image file 404s or fails to decode → the slot falls back to the emoji after the `error` event, and a later different slug renders again. (Task 4)
3. `PUT /accounts/{id}` without `logo` clears it (full-payload semantics), and `accountToInput` on a re-save keeps the stored logo — so a 圖示-only edit must not silently drop the logo. (Tasks 1, 6)
4. A re-import of a backup never overwrites a logo on an account the owner edited (`settings_locally_edited`) or that already has one. (Task 3)
5. Dark mode: the logo tile stays white in both themes while the emoji slot keeps `--app-surface-soft`. (Task 4)

---

### Task 1: `account.logo` column, migration, API field, OpenSpec change skeleton

**Files:**
- Create: `services/accounting-service/alembic/versions/b8d4f2a1c6e3_account_logo.py`
- Modify: `services/accounting-service/app/models/ledger.py:118` (after `icon`), `app/schemas/writes.py:192`, `app/schemas/ledger.py:54`, `app/services/ledger_service.py:48-52`
- Create: `openspec/changes/account-brand-logos/proposal.md`, `tasks.md`, `design.md`, `specs/accounting-ledger/spec.md`
- Test: `services/accounting-service/tests/integration/test_accounts_api.py`, `tests/integration/test_settings_crud_api.py`

**Interfaces:**
- Produces: `Account.logo: Mapped[str | None]` (`String(32)`), `AccountIn.logo: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,32}$")] | None = None`, `AccountOut.logo: str | None`, `"logo"` in `ACCOUNT_FIELDS`. JSON field name `logo` on `GET /accounts`, `GET /accounts/{id}`, `POST /accounts`, `PUT /accounts/{id}`.

- [ ] **Step 1: Write the failing tests**

In `test_accounts_api.py` add `"logo"` to the `set(body[0]) >= {…}` assertion (:59-64). In `test_settings_crud_api.py` add:

```python
def test_account_logo_round_trips_and_clears_when_omitted(client, db_session):
    created = client.post("/accounts", json=_account_body(logo="cathay-united")).json()
    assert created["logo"] == "cathay-united"
    assert client.get("/accounts").json()[0]["logo"] == "cathay-united"
    body = _account_body()  # no logo key
    assert client.put(f"/accounts/{created['id']}", json=body).json()["logo"] is None


@pytest.mark.parametrize("bad", ["Cathay", "cathay_united", "a" * 33, ""])
def test_account_logo_must_be_a_slug(client, db_session, bad):
    assert client.post("/accounts", json=_account_body(logo=bad)).status_code == 422
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd services/accounting-service && pytest tests/integration/test_settings_crud_api.py -k logo tests/integration/test_accounts_api.py -q`
Expected: FAIL (`KeyError: 'logo'` / 422 vs 201 mismatch).

- [ ] **Step 3: Implement**

`ledger.py`: `logo: Mapped[str | None] = mapped_column(String(32))` after `icon` (match the file's column style). `writes.py:192` → add `logo` after `icon` with the pattern above. `ledger.py` (schemas) `AccountOut`: `logo: str | None` after `icon`. `ACCOUNT_FIELDS`: `"logo"` after `"icon"`. Migration `b8d4f2a1c6e3_account_logo.py` with `down_revision = "a7c3e1d9b2f4"`, `op.add_column("account", sa.Column("logo", sa.String(32), nullable=True))`, `op.drop_column` in `downgrade`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/integration/test_settings_crud_api.py tests/integration/test_accounts_api.py tests/integration/test_models_schema.py -q`
Expected: PASS. Also `alembic heads` from `services/accounting-service` prints exactly one head `b8d4f2a1c6e3`.

- [ ] **Step 5: OpenSpec change skeleton + ledger delta**

`openspec/changes/account-brand-logos/proposal.md` (## Why: owner wish #175, MOZE showed bank logos; ## What Changes: the four bullets of the design's §2 Goals; ## Impact: accounting-service column + API field, SPA assets + component + picker, importer), `tasks.md` (one checkbox per task of this plan, numbered 1–7), `design.md` (three lines pointing at `docs/superpowers/specs/2026-10-26-account-brand-logos-design.md` and listing the *[call]* decisions: shape-only API validation, white tile, inbox header untouched). `specs/accounting-ledger/spec.md`: `## MODIFIED Requirements` / `### Requirement: Account model` — copy the current requirement text from `openspec/specs/accounting-ledger/spec.md:6…` in full and add the bullet `- \`logo\` (VARCHAR(32), nullable; a slug of the SPA's built-in brand-logo set, \`^[a-z0-9-]{1,32}$\`; the API validates the shape only)` after the `icon`/`color` bullet, plus scenario:

```
#### Scenario: Logo slug shape
- **WHEN** an account is created or updated with `logo` `"Cathay"`, `"cathay_united"` or 33 characters
- **THEN** the API SHALL answer 422; `"cathay-united"` SHALL be stored and returned on every account read
```

Run: `npx -y @fission-ai/openspec@latest validate account-brand-logos --strict` → valid.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/alembic/versions/b8d4f2a1c6e3_account_logo.py services/accounting-service/app/models/ledger.py services/accounting-service/app/schemas/writes.py services/accounting-service/app/schemas/ledger.py services/accounting-service/app/services/ledger_service.py services/accounting-service/tests/integration/test_accounts_api.py services/accounting-service/tests/integration/test_settings_crud_api.py openspec/changes/account-brand-logos
git commit -m "feat(accounting): account.logo slug column and API field (#175)"
```

---

### Task 2: The logo set — fetch, normalise, manifest, SOURCES.md, manifest gate

**Files:**
- Create: `frontend/src/assets/logos/<slug>.svg|png` (one per obtainable slug), `frontend/src/assets/logos/SOURCES.md`
- Create: `tools/account-logos/normalize.py` (stdlib only), `frontend/src/app/theme/account-logos.ts`, `frontend/src/app/theme/account-logos.spec.ts`

**Interfaces:**
- Produces: `export interface AccountLogo { slug: string; label: string; file: string }`, `export const ACCOUNT_LOGOS: readonly AccountLogo[]` (manifest order = the design §4 table order), `export function accountLogo(slug: string | null | undefined): AccountLogo | undefined`, `export const LOGO_SLUG = /^[a-z0-9-]{1,32}$/`.
- The slug/label pairs are the design §4 table (30 rows). Slugs with no obtainable file are **omitted** from the manifest and listed in the task report (Task 3 reads that list).

- [ ] **Step 1: Write the failing manifest gate** `account-logos.spec.ts` (node `fs`, same style as `line-icons.spec.ts`):

```ts
const DIR = join(__dirname, '../../assets/logos');
it('every manifest entry has a square file and every file is in the manifest', () => {
  const files = readdirSync(DIR).filter(f => f !== 'SOURCES.md');
  expect(new Set(ACCOUNT_LOGOS.map(l => l.file))).toEqual(new Set(files));
  for (const l of ACCOUNT_LOGOS) {
    expect(l.slug).toMatch(LOGO_SLUG);
    expect(l.file).toBe(`${l.slug}${extname(l.file)}`);
    const [w, h] = dimensions(join(DIR, l.file));   // svg: root viewBox w/h; png: IHDR width/height
    expect(w).toBe(h);
    if (l.file.endsWith('.png')) expect(w).toBeGreaterThanOrEqual(180);
  }
});
it('svg files carry no script, foreignObject, image, event handler or external href', …);  // regex over the text
it('slugs are unique and labels non-empty', …);
it('accountLogo resolves a slug and returns undefined for unknown / null', () => {
  expect(accountLogo('cathay-united')?.label).toBe('國泰世華');
  expect(accountLogo('nope')).toBeUndefined();
  expect(accountLogo(null)).toBeUndefined();
});
```

`dimensions()` is a helper in the spec: SVG → parse the root `viewBox="a b w h"` with a regex; PNG → read bytes 16–24 of the IHDR as two big-endian uint32.

- [ ] **Step 2: Run it to verify it fails** — `cd frontend && npx ng test --watch=false --include 'src/app/theme/account-logos.spec.ts'` → FAIL (module not found).

- [ ] **Step 3: `tools/account-logos/normalize.py`** (stdlib: `xml.etree.ElementTree`, `struct`, `argparse`; run with `python3 -I`):
  `normalize.py <in.svg|in.png> <out-dir> <slug>`; for SVG: parse, reject on any forbidden element / attribute (Global Constraints), drop `<metadata>`; take the root's `viewBox` (or `width`/`height` when no viewBox), compute `S = max(w, h) * 1.16`, write a new root `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 S S">` whose single child is the original root `<svg>` with `x`, `y`, `width`, `height` set so the mark is centred with 8 % padding on the long side (`width=w`, `height=h`, `x=(S-w)/2`, `y=(S-h)/2`; the inner element keeps its original `viewBox`, so the mark scales with the outer square); exit 1 with a reason on rejection. For PNG: verify IHDR width == height ≥ 180, copy as-is. Prints the output path.

- [ ] **Step 4: Fetch and normalise.** Per design §4 slug table, in order: official site (brand/press page, header SVG, `apple-touch-icon` / PWA icon link tags), then Wikimedia Commons (`https://commons.wikimedia.org/w/api.php?action=query&list=search&srnamespace=6&format=json&srsearch=<name> logo svg`, then `action=query&prop=imageinfo&iiprop=url&titles=File:…`), then official PNG. `curl -sS -L -m 20 -A 'Mozilla/5.0 (X11; Linux) HomeHub-logo-fetch'` into `/home/opc/.claude/jobs/2741e73c/tmp/logo-fetch/<slug>/`, `file` each download, then `python3 -I tools/account-logos/normalize.py … frontend/src/assets/logos <slug>`. `richart` / `dawho`: if no sub-brand mark is found, copy the `taishin` / `sinopac` output under the sub-brand slug. Record every row in `SOURCES.md` (`| slug | institution | source URL | fetched | notes |`, header paragraph: trademarks of their owners, bundled for this household's private app, not redistributed). Slugs that fail → not in the manifest, listed in the report.

- [ ] **Step 5: Manifest** `account-logos.ts` — `ACCOUNT_LOGOS` with the obtained slugs in table order, labels from the design table; `accountLogo()` as a `Map` lookup; `LOGO_SLUG` regex.

- [ ] **Step 6: Run the gate and the line-icon gate**

Run: `npx ng test --watch=false --include 'src/app/theme/account-logos.spec.ts' --include 'src/app/theme/line-icons.spec.ts'` → PASS. `ls frontend/src/assets/logos | wc -l` reported.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/assets/logos tools/account-logos/normalize.py frontend/src/app/theme/account-logos.ts frontend/src/app/theme/account-logos.spec.ts
git commit -m "feat(frontend): bundled institution logo set with manifest and gate (#175)"
```

---

### Task 3: Importer — `LOGO_BY_IMAGE` and `_upsert_accounts`

**Files:**
- Modify: `services/accounting-service/app/services/moze_backup_json.py` (after `ICON_BY_IMAGE`, :231-252), `app/services/moze_backup_import_service.py:54-55, 289-302`
- Create: `openspec/changes/account-brand-logos/specs/accounting-moze-backup-import/spec.md`
- Test: `tests/integration/test_backup_settings_import.py`, `tests/unit/test_moze_backup_json.py`

**Interfaces:**
- Consumes: Task 1 `Account.logo`; Task 2's report (slugs omitted from the manifest).
- Produces: `LOGO_BY_IMAGE: dict[str, str]` = design §8 table minus any slug Task 2 could not obtain (drop the row, note it in `tasks.md`).

- [ ] **Step 1: Write the failing tests**

```python
# test_backup_settings_import.py
def test_new_accounts_get_the_logo_of_their_moze_picture(db_session, backup):
    _import_backup(db_session, _settings_backup(backup, accounts=[
        backup.account("A-1", "國泰", imageName="AccountCollection10_05"),
        backup.account("A-2", "錢包", imageName="Account_24"),
    ]))
    assert (_account(db_session, "國泰").logo, _account(db_session, "錢包").logo) == ("cathay-united", None)


def test_reimport_fills_a_missing_logo_but_never_overwrites_one(db_session, backup):
    data = _settings_backup(backup, accounts=[backup.account("A-1", "台新", imageName="AccountCollection01_11")])
    _import_backup(db_session, data)
    a = _account(db_session, "台新"); a.logo = "richart"; db_session.commit()
    _import_backup(db_session, data)
    db_session.refresh(a); assert a.logo == "richart"
    a.logo = None; a.settings_locally_edited = True; db_session.commit()
    _import_backup(db_session, data)
    db_session.refresh(a); assert a.logo is None
    a.settings_locally_edited = False; db_session.commit()
    _import_backup(db_session, data)
    db_session.refresh(a); assert a.logo == "taishin"

# test_moze_backup_json.py
LOGOS_DIR = Path(__file__).resolve().parents[4] / "frontend" / "src" / "assets" / "logos"

@pytest.mark.skipif(not LOGOS_DIR.is_dir(), reason="frontend tree absent")
def test_every_logo_slug_has_a_bundled_file():
    files = {p.stem for p in LOGOS_DIR.iterdir() if p.suffix in {".svg", ".png"}}
    assert set(LOGO_BY_IMAGE.values()) <= files

def test_generic_moze_pictures_have_no_logo():
    assert not {"Account_05", "Account_24", "Payable"} & LOGO_BY_IMAGE.keys()
```

- [ ] **Step 2: Run to verify they fail** — `pytest tests/integration/test_backup_settings_import.py -k logo tests/unit/test_moze_backup_json.py -k logo -q` → FAIL (ImportError / AttributeError `logo`).

- [ ] **Step 3: Implement** — `LOGO_BY_IMAGE` in `moze_backup_json.py` next to `ICON_BY_IMAGE` with a one-line docstring ("MOZE account picture → SPA brand-logo slug; generic pictures absent"); import it in the import service; in `_upsert_accounts` right after the `icon` rule (:301-302): `if account.logo is None and not account.settings_locally_edited: account.logo = LOGO_BY_IMAGE.get(record["imageName"])` (covers new accounts too, since they are created with `logo` unset — confirm the new-account path reaches this line; if the icon assignment is inside the "existing" branch only, hoist both).

- [ ] **Step 4: Run** `pytest tests/integration/test_backup_settings_import.py tests/unit/test_moze_backup_json.py -q` → PASS.

- [ ] **Step 5: Spec delta** `specs/accounting-moze-backup-import/spec.md`: `## MODIFIED Requirements` / `### Requirement: Accounts, groups, categories, projects, rules and preferences` — copy the whole current requirement (`openspec/specs/accounting-moze-backup-import/spec.md:118…`) and in the **Accounts** bullet change "icon by group per the design's table when the account has none" to "icon by group and `imageName` → `logo` per the design's tables, each only when the account has none and its settings are not locally edited (generic MOZE pictures give no logo)". Scenario:

```
#### Scenario: Logo from the MOZE picture
- **WHEN** a backup account with `imageName` `AccountCollection10_05` is created by the import
- **THEN** its `logo` SHALL be `cathay-united`; an account with `Account_24` SHALL have no logo
- **AND** a re-import SHALL NOT change a logo that is already set or belongs to a locally edited account
```

Run `npx -y @fission-ai/openspec@latest validate account-brand-logos --strict` → valid.

- [ ] **Step 6: Commit**

```bash
git add services/accounting-service/app/services/moze_backup_json.py services/accounting-service/app/services/moze_backup_import_service.py services/accounting-service/tests/integration/test_backup_settings_import.py services/accounting-service/tests/unit/test_moze_backup_json.py openspec/changes/account-brand-logos
git commit -m "feat(accounting): MOZE imageName → account logo on import (#175)"
```

---

### Task 4: `<app-account-icon>` component + model fields + tile token

**Files:**
- Create: `frontend/src/app/components/accounting/account-icon/account-icon.ts` (inline template + styles), `account-icon.spec.ts`
- Modify: `frontend/src/app/models/accounting.model.ts:68` (`LedgerAccount.logo`), `:400…` (`AccountInput.logo`), `frontend/src/styles.scss` (token `--app-logo-tile: #fff` in both the light and the dark token blocks, next to `--app-surface-soft`)
- Test: `frontend/src/app/theme/contrast.spec.ts` or a new `theme/logo-tile.spec.ts` (stylesheet-text test, same pattern as `kpi-values.spec.ts`)

**Interfaces:**
- Consumes: Task 2 `accountLogo()`.
- Produces: `AccountIconComponent` selector `app-account-icon`, standalone, `ChangeDetectionStrategy.OnPush`, inputs `logo = input<string | null>(null)`, `icon = input<string | null>(null)`, `fallback = input<string>('')`, `size = input<'row' | 'big'>('row')`; `host: { '[class.has-logo]': 'entry() !== undefined', '[class.big]': "size() === 'big'" }`. `LedgerAccount.logo: string | null`, `AccountInput.logo: string | null`.

- [ ] **Step 1: Write the failing tests** `account-icon.spec.ts` (TestBed host template `<app-account-icon class="ico" [logo]="logo()" [icon]="icon()" fallback="📁" />`):

```ts
it('renders the manifest image for a known slug', …)        // img[src="assets/logos/cathay-united.svg"] (use ACCOUNT_LOGOS[0] to stay file-agnostic), alt="", aria-hidden="true", no .emoji
it('renders the emoji for an unknown slug', …)              // logo 'nope', icon '💳' → .emoji text '💳', no img
it('renders the fallback when neither logo nor icon', …)    // → '📁'
it('falls back to the emoji after the image errors and recovers on a new slug', …)  // dispatch new Event('error') on the img → .emoji; set another known slug → img again
it('host carries has-logo only while an image renders', …)
```

- [ ] **Step 2: Run** `npx ng test --watch=false --include 'src/app/components/accounting/account-icon/*.spec.ts'` → FAIL (cannot find module).

- [ ] **Step 3: Implement** `account-icon.ts`:
  `failedSlug = signal<string | null>(null)`; `entry = computed(() => { const s = this.logo(); return s && this.failedSlug() !== s ? accountLogo(s) : undefined; })`; template:

```html
@if (entry(); as e) {
  <img class="logo" [src]="'assets/logos/' + e.file" alt="" aria-hidden="true" loading="lazy" decoding="async" (error)="failedSlug.set(e.slug)" />
} @else {
  <span class="emoji">{{ icon() || fallback() }}</span>
}
```

  styles: `:host { display: inline-flex; align-items: center; justify-content: center; overflow: hidden; }`, `:host.has-logo { background: var(--app-logo-tile); }`, `.logo { width: 100%; height: 100%; object-fit: contain; padding: 10%; box-sizing: border-box; display: block; }`, `.emoji { line-height: 1; }`. The host inherits size, radius and the default background from the slot class it is given (`.ico` / `.acct-ico`), so slots keep their geometry. `styles.scss`: `--app-logo-tile: #fff;` in the light block and the dark block.

- [ ] **Step 4: Stylesheet test** (new `theme/logo-tile.spec.ts`, reading `src/styles.scss` as text like `kpi-values.spec.ts`): `--app-logo-tile: #fff` appears at least twice (light + dark blocks). Model: add `logo: string | null;` to `LedgerAccount` after `icon` and to `AccountInput` after `icon`. Fix the compiler's complaints (object literals typed `AccountInput`, e.g. `accountToInput` — set `logo: account.logo` there now; Task 6 owns the form).

- [ ] **Step 5: Run** `npx ng test --watch=false --include 'src/app/components/accounting/account-icon/*.spec.ts' --include 'src/app/theme/*.spec.ts' --include 'src/app/components/accounting/account-settings/*.spec.ts'` → PASS; `npx ng build` compiles (or `npx tsc -p tsconfig.app.json --noEmit`).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/components/accounting/account-icon frontend/src/app/models/accounting.model.ts frontend/src/styles.scss frontend/src/app/theme/logo-tile.spec.ts frontend/src/app/components/accounting/account-settings/account-settings.ts
git commit -m "feat(frontend): app-account-icon renders a bundled logo or the emoji (#175)"
```

---

### Task 5: Swap the slots — accounts list, account picker, 🔔 card rows

**Files:**
- Modify: `frontend/src/app/components/accounting/accounts/accounts.html:21` (+ `accounts.ts` imports), `account-picker/account-picker.html:15, 51, 71` (+ `account-picker.ts` imports, `account-picker.scss` for the chip), `reminders/reminders.ts:68-75, 155-169` + `reminders.html:45`
- Create: `openspec/changes/account-brand-logos/specs/frontend-accounting-ledger/spec.md` (accounts list part; Task 6 appends the settings part)
- Test: `accounts.spec.ts`, `account-picker.spec.ts`, `reminders.spec.ts`

**Interfaces:**
- Consumes: Task 4 component. `cardReminders(bills, accounts: Pick<LedgerAccount, 'id' | 'icon' | 'logo'>[], today)` now returns `CardReminder` with `logo: string | null` beside `icon`.

- [ ] **Step 1: Write the failing tests**

```ts
// accounts.spec.ts
it('shows the brand logo instead of the emoji when the account has one', …)   // fixture account({ id: 3, name: '玉山銀行', logo: ACCOUNT_LOGOS[0].slug }) → row 3 has app-account-icon img, no emoji text; row 1 (no logo) still shows its emoji
// account-picker.spec.ts
it('renders the logo in the trigger, the recent chip and the option row', …)  // one account with logo chosen + in recents → 3 img elements with the same src
// reminders.spec.ts (pure function)
it('cardReminders carries the account logo', () => { expect(cardReminders([bill], [{ id: 10, icon: '🏔', logo: 'cathay-united' }], today)[0].logo).toBe('cathay-united'); });
```

- [ ] **Step 2: Run** the three specs → FAIL.

- [ ] **Step 3: Implement** — each slot becomes `<app-account-icon class="<same class>" [logo]="account.logo" [icon]="account.icon" fallback="<slot's current emoji>" />` (`aria-hidden` moves inside the component; keep any `aria-hidden` the slot had). The recent chip (`:51`): `<app-account-icon class="acct-ico" … /> {{ account.name }}` with `.acct-recent-chip .acct-ico { --ico-size: 1.25rem; }` or the chip's existing font size. `reminders.html:45`: `<app-account-icon class="ico" [logo]="card.logo" [icon]="card.icon" fallback="💳" />`; `cardReminders` keeps the `icons` map and adds `logos = new Map(accounts.map(a => [a.id, a.logo]))`, `logo: logos.get(bill.accountId) ?? null`; fix the call site at `reminders.ts:307` if its `Pick` type is spelled there.

- [ ] **Step 4: Run** `npx ng test --watch=false --include 'src/app/components/accounting/accounts/*.spec.ts' --include 'src/app/components/accounting/account-picker/*.spec.ts' --include 'src/app/components/accounting/reminders/*.spec.ts' --include 'src/app/theme/line-icons.spec.ts'` → PASS.

- [ ] **Step 5: Spec delta** `specs/frontend-accounting-ledger/spec.md`: `## MODIFIED Requirements` for the accounts-list requirement that reads "icon and name" (`openspec/specs/frontend-accounting-ledger/spec.md:~99-12`; copy it whole) → "its brand logo when set, else its icon, and name"; scenario:

```
#### Scenario: Brand logo in the list
- **GIVEN** an account with `logo` `cathay-united` and one with none
- **THEN** the first row SHALL render the bundled image (`assets/logos/…`) and no emoji; the second SHALL render its emoji
- **AND** an unknown slug or a failed image load SHALL render the emoji
```

Validate `--strict` → valid.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/components/accounting/accounts frontend/src/app/components/accounting/account-picker frontend/src/app/components/accounting/reminders openspec/changes/account-brand-logos
git commit -m "feat(frontend): account logos in the accounts list, picker and card reminders (#175)"
```

---

### Task 6: Account settings — form field, 品牌 logo picker, big icon

**Files:**
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts` (`AccountForm`, `EMPTY_FORM`, `formFrom`, `formToInput`, `accountToInput` (done in Task 4), new `logoPickerOpen`, `logoLabel`, `pickLogo`), `account-settings.html:17-18, 58-64`, `account-settings.scss`
- Modify: `openspec/changes/account-brand-logos/specs/frontend-accounting-ledger/spec.md` (append), create `specs/frontend-ui-primitives/spec.md`
- Test: `account-settings.spec.ts`

**Interfaces:**
- Consumes: Task 2 `ACCOUNT_LOGOS`, `accountLogo`; Task 4 component.
- Produces: `AccountForm.logo: string | null`; `logoPickerOpen = signal(false)`; `logoLabel = computed(() => accountLogo(this.form().logo)?.label ?? '無')`; `pickLogo(slug: string | null): void` (sets `form.logo`, closes the grid); `protected readonly logos = ACCOUNT_LOGOS`.

- [ ] **Step 1: Write the failing tests**

```ts
it('shows the current logo label and lists the set with 無 first', …)      // DETAIL with logo 'cathay-united' → button.logo-current text contains '國泰世華'; click → .logo-grid buttons[0] text '無', count === ACCOUNT_LOGOS.length + 1, the cathay cell aria-pressed="true"
it('picking a logo updates the big icon and the saved payload', …)         // click a cell → .bigicon app-account-icon img src ends with that file; save → updateAccount payload.logo === slug; grid closed
it('無 clears the logo and keeps the emoji', …)                              // click 無 → payload.logo null, payload.icon unchanged ('💳'), .bigicon shows the emoji
it('a 圖示-only edit keeps the stored logo', …)                              // set icon → save → payload.logo === DETAIL.logo (Review Focus 3)
```

Existing fixtures: add `logo: null` to the `account()` helper defaults and `logo: 'cathay-united'` (any manifest slug) to `DETAIL`.

- [ ] **Step 2: Run** `npx ng test --watch=false --include 'src/app/components/accounting/account-settings/*.spec.ts'` → FAIL.

- [ ] **Step 3: Implement** — form plumbing (`logo: account.logo ?? null` in `formFrom`, `logo: form.logo` in `formToInput`, `null` in `EMPTY_FORM`); template: the `.bigicon .ico` span becomes `<app-account-icon class="ico" size="big" [logo]="form().logo" [icon]="form().icon" fallback="📁" />`; new row above the 圖示 row:

```html
<div class="rw">
  <span class="k">品牌 logo</span>
  <span class="v">
    <button type="button" class="logo-current" [attr.aria-expanded]="logoPickerOpen()" (click)="logoPickerOpen.set(!logoPickerOpen())">
      <app-account-icon class="ico-sm" [logo]="form().logo" /> {{ logoLabel() }}
    </button>
  </span>
</div>
@if (logoPickerOpen()) {
  <div class="logo-grid" role="group" aria-label="品牌 logo">
    <button type="button" class="logo-opt" [attr.aria-pressed]="form().logo === null" (click)="pickLogo(null)">無</button>
    @for (l of logos; track l.slug) {
      <button type="button" class="logo-opt" [attr.aria-pressed]="form().logo === l.slug" [attr.aria-label]="l.label" (click)="pickLogo(l.slug)">
        <app-account-icon class="ico-sm" [logo]="l.slug" /><small>{{ l.label }}</small>
      </button>
    }
  </div>
}
```

  scss: `.logo-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(72px, 1fr)); gap: 0.5rem; }`, `.logo-opt` 44 px tile + label, `[aria-pressed="true"]` outline `--app-primary`; `.ico-sm { --ico-size: 1.5rem; width/height: var(--ico-size); border-radius: 50%; }` (reuse the file's `.ico` rules where they exist).

- [ ] **Step 4: Run** the settings spec + `line-icons.spec.ts` → PASS.

- [ ] **Step 5: Spec deltas** — append to `specs/frontend-accounting-ledger/spec.md` a `### Requirement: Account settings page` MODIFIED block (copy from `openspec/specs/frontend-accounting-ledger/spec.md:144…`): header shows "the account's brand logo when set, else its icon"; a `品牌 logo` row with a button showing the current choice that opens a grid of the bundled set with `無` first; scenarios "Pick a logo" (cell → header image + saved `logo`), "Clear the logo" (`無` → `logo` null, emoji unchanged). `specs/frontend-ui-primitives/spec.md`: MODIFIED `### Requirement: Line icons for interface chrome` (copy `openspec/specs/frontend-ui-primitives/spec.md:126-136` whole) + sentence "An owner-chosen account logo image from the app's bundled brand-logo set MAY render in the same slots." Validate `--strict` → valid.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/components/accounting/account-settings openspec/changes/account-brand-logos
git commit -m "feat(frontend): 品牌 logo picker on the account settings page (#175)"
```

---

### Task 7: Whole-suite verification and browser evidence

**Files:**
- Modify: `openspec/changes/account-brand-logos/tasks.md` (tick 1–7)
- Evidence dir (outside the repo): `~/reports/home-hub-account-logos-<TS>/` — `NOTES.md`, PNGs

- [ ] **Step 1: Suites** — `cd services/accounting-service && pytest -q` → all green; `cd frontend && npx ng test --watch=false` → all green (report files/tests counts); `npx ng build` → clean apart from the pre-existing protobufjs warning; `npx -y @fission-ai/openspec@latest validate account-brand-logos --strict` → valid.
- [ ] **Step 2: Browser evidence** (Chromium via the existing probe tooling from `~/reports/home-hub-polish-pr-e-20261025T034833Z/`, fictional stubbed fixtures with `logo` set on two accounts): `/accounting/accounts` list, the picker open in the entry form, `/accounting/accounts/<id>` settings with the grid open, 🔔 card rows; light + dark; 1440 × 900 and 390 × 844; assert `img[src^="assets/logos/"]` count, `naturalWidth > 0`, no console errors, white tile `getComputedStyle(...).backgroundColor === 'rgb(255, 255, 255)'` in dark.
- [ ] **Step 3: Commit** `tasks.md` ticks: `git commit -m "docs(openspec): account-brand-logos tasks complete (#175)"`.

Controller follow-ups (not tasks): PR with the evidence summary and the list of slugs without a logo; Multica review; after merge deploy API (alembic upgrade) then SPA; one-off logo apply from the private table; 三信薪轉戶 archive.
