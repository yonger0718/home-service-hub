# Account Logo Upload — PR 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An account's logo can be uploaded through `PUT /accounts/{id}/logo` (raw image body, server-normalised PNG in Postgres) and shown in every SPA account-icon slot, with upload entry points on the account page avatar and the account settings page.

**Architecture:** The accounting API gains one side table (`account_logo_image`), one pure normaliser (`account_logo_service.normalise_logo`, Pillow), three routes on the existing accounts router and a `logo_hash` scalar on every account read. The SPA gains an `AccountLogoStore` that fetches the image as a blob through HttpClient (so the bearer token applies) and hands object URLs to `<app-account-icon>`, which now prefers the uploaded image over the bundled slug. PR 2 (crop sheet) is out of scope.

**Tech Stack:** FastAPI 0.129 (one `async def` route), SQLAlchemy 2.0 + Alembic, Pillow 12.3.0, Postgres `bytea`; Angular 21 standalone/signals, Vitest; OpenSpec 1.15.

**Spec:** `docs/superpowers/specs/2026-11-02-account-logo-upload-design.md` rev 4.1 (`c8cf5e94`). Section numbers below (§) refer to it.

**Plan rev 2** (2026-11-02): folds Multica AGENT-386 — null hashes reach the store so deletion invalidates (Task 8);
RGBA paste without a self-mask (Task 2); no transpose-before-thumbnail fallback, call order asserted (Task 2);
no-load / corrupt-pixel / envelope / worker-thread / stored-bytes / mismatch-then-reload tests pinned (Tasks 2, 4, 7);
timestamps `NOT NULL` (Task 1); Pillow and the venv install command precede their first use (Task 1).

**Environment prerequisite (run once before Task 1):**
`cd services/accounting-service && uv pip install --python .venv/bin/python -r requirements.txt` — this is the
install command the service README uses; Task 1 step 0 adds `pillow==12.3.0` to `requirements.txt` first.

**Worktree:** `/home/opc/workspace/home-hub-logo-upload` on `feat/account-logo-upload`. Backend tests:
`cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q <path>`.
Frontend tests: `cd frontend && ./node_modules/.bin/ng test --watch=false --include='<glob>'` (`npm run config` first, then `git checkout -- angular.json`).

## Global Constraints

- Upload body cap `MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024`; `Content-Length` pre-check then streamed budget; 413 `logo_too_large` both ways (§5).
- Allowed sniffed formats exactly `{"PNG", "JPEG", "WEBP"}`; SVG never parsed (§2, §5).
- Sides `16 ≤ side ≤ 8192` and `width × height ≤ MAX_LOGO_PIXELS = 25_000_000`, checked from the header before `load()`; Pillow bomb warning and error both → 422 `logo_dimensions` (§5 steps 1–3).
- Shrink first: `thumbnail((256, 256), LANCZOS, reducing_gap=3.0)` in native mode, then `exif_transpose`, `convert("RGBA")`, paste centred on a fresh `Image.new("RGBA", (256, 256), (0, 0, 0, 0))`; PNG `optimize=True`, no metadata (§5 step 4).
- Error bodies are the shared `{"code", "message", "trace_id"}` envelope; tokens `logo_empty`, `logo_too_large`, `logo_unreadable`, `logo_format`, `logo_dimensions`; 404 text `account not found` (§5 Errors summary).
- `logo_hash` = `sha256[:16]`, a labelled scalar subquery on the `_account_rows` select, written from the row, **never** in `ACCOUNT_FIELDS` (§5).
- GET image: `ETag: "<sha256>"`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline; filename="logo.png"`; `Cache-Control: private, max-age=31536000, immutable` only when `?v` equals the current `logo_hash`, else `Cache-Control: no-store`; `If-None-Match` match → 304 (§5 GET).
- Only the `PUT` route is `async def`; after the stream, normalise + upsert + commit + detail build run in **one** `run_in_threadpool` call holding the session (§5 limits step 3).
- Owner data never enters the repo: fixtures are Pillow-drawn; fictional names/ids only.
- SPA: no token in URLs; images fetched via HttpClient blob; `blob:` object URLs; no new emoji in templates (line-icons gate); PrimeIcons `pi-upload` / `pi-image` for glyphs.
- Settings page: image writes patch only `logoHash`, never call `apply()` (§6.3).
- OpenSpec: archive `account-brand-logos` **before** writing the new deltas; new change `account-logo-upload` touches `accounting-ledger`, `frontend-accounting-ledger`, `frontend-ui-primitives`; `openspec validate account-logo-upload --strict` must pass (§9 step 0, §10).
- Commits: conventional subject, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

Inputs the spec implies but a careless build would get wrong; each has a pinned test in the owning task.

1. **A JPEG whose EXIF says "rotate 90°"** must come out upright, and `draft()` must not break that — Task 2 (`test_exif_orientation_is_applied`).
2. **A palette PNG with transparency (mode `P` + `tRNS`)** must keep its transparent pixels, not turn them black — Task 2 (`test_palette_transparency_survives`).
3. **A chunked upload with no `Content-Length` that exceeds 5 MiB** must be cut off while streaming, not buffered whole — Task 4 (`test_streamed_body_over_cap_stops_reading`).
4. **A `?v` that matches an older image after a re-upload** must get the current bytes with `no-store`, and the SPA store must file them under the real hash — Task 4 (`test_get_with_stale_v_is_no_store`) and Task 7 (`etag mismatch is cached under the actual hash and refreshes accounts`).
5. **Deleting an account that has an uploaded logo** must not fail on the FK and must take the image with it — Task 1 (`test_account_delete_cascades_logo_image`).

---

## File structure

Backend (`services/accounting-service/`):
- Modify `app/models/ledger.py` — add `AccountLogoImage` after `Account`; export it from `app/models/__init__.py`.
- Create `alembic/versions/e9b1c3d7a5f2_account_logo_image.py`.
- Create `app/services/account_logo_service.py` — constants, `LogoError`, `NormalisedLogo`, `normalise_logo`, `logo_hash`, `put_logo`, `delete_logo`, `get_logo`.
- Modify `app/services/ledger_service.py` — `logo_hash` column in `_account_rows`.
- Modify `app/schemas/ledger.py` — `AccountOut.logo_hash`.
- Modify `app/routers/accounts.py` — three routes + `_read_body_capped`.
- Modify `requirements.txt` — `pillow==12.3.0`.
- Tests: `tests/unit/test_account_logo_service.py` (new), `tests/integration/test_account_logo_api.py` (new), `tests/integration/test_migration.py`, `tests/integration/test_accounts_api.py`.

Frontend (`frontend/src/app/`):
- Modify `models/accounting.model.ts` — `LedgerAccount.logo_hash`.
- Modify `services/accounting.service.ts` — `uploadAccountLogo`, `deleteAccountLogo`, `logoImageUrl`, `refreshAccounts`.
- Create `services/account-logo-store.ts` (+ `.spec.ts`).
- Create `components/accounting/logo-upload-errors.ts` (+ `.spec.ts`) — `MAX_LOGO_FILE_BYTES`, `LOGO_ACCEPT`, `logoUploadErrorMessage`.
- Modify `components/accounting/account-icon/account-icon.ts` (+ spec) — `accountId`, `logoHash`, uploaded-first precedence.
- Modify call sites: `accounts/accounts.html`, `account-picker/account-picker.html`, `reminders/reminders.{ts,html}`, `account-settings/account-settings.html`.
- Modify `account-entries/account-entries.{ts,html,scss}` — avatar upload.
- Modify `account-settings/account-settings.{ts,html}` (+ spec) — Logo block.

OpenSpec: archive `openspec/changes/account-brand-logos`; create `openspec/changes/account-logo-upload/{proposal.md,tasks.md,specs/accounting-ledger/spec.md,specs/frontend-accounting-ledger/spec.md,specs/frontend-ui-primitives/spec.md}`.

---

### Task 0: Archive the finished `account-brand-logos` OpenSpec change

**Files:**
- Move: `openspec/changes/account-brand-logos/` → `openspec/changes/archive/2026-11-02-account-brand-logos/` (CLI does it)
- Modify (by the CLI): `openspec/specs/accounting-ledger/spec.md`, `openspec/specs/accounting-moze-backup-import/spec.md`, `openspec/specs/frontend-accounting-ledger/spec.md`, `openspec/specs/frontend-ui-primitives/spec.md`

**Interfaces:**
- Produces: merged spec text that mentions `logo` in `### Requirement: Account model`, `### Requirement: Read-only ledger endpoints`, `### Requirement: Accounts list page`, `### Requirement: Account settings page`, `### Requirement: Line icons for interface chrome` — Task 5 writes its deltas against this text.

- [ ] **Step 1: Validate the old change still passes strict validation**

Run: `cd /home/opc/workspace/home-hub-logo-upload && openspec validate account-brand-logos --strict`
Expected: `valid` (if it fails on 1.15 rules — see memory note about `MODIFIED` dropping scenarios — fix the delta text minimally so the archive carries complete requirements, and say so in the commit body).

- [ ] **Step 2: Archive**

Run: `openspec archive account-brand-logos --yes`
Expected: the change directory moves under `openspec/changes/archive/2026-11-02-account-brand-logos/` and the four main specs gain the logo text.

- [ ] **Step 3: Verify the merged text**

Run: `grep -n "logo" openspec/specs/accounting-ledger/spec.md openspec/specs/frontend-accounting-ledger/spec.md openspec/specs/frontend-ui-primitives/spec.md | wc -l`
Expected: ≥ 6 lines; `openspec validate --specs --strict` (or `openspec validate --all --strict`) reports every spec valid.

- [ ] **Step 4: Commit**

```bash
git add openspec
git commit -m "chore(openspec): archive account-brand-logos (#175 shipped)"
```

---

### Task 1: `account_logo_image` table — model, migration, cascade test

**Files:**
- Modify: `services/accounting-service/requirements.txt` (add `pillow==12.3.0` after `python-multipart==0.0.28`)
- Modify: `services/accounting-service/app/models/ledger.py` (after the `Account` class, line ~145)
- Modify: `services/accounting-service/app/models/__init__.py` (export)
- Create: `services/accounting-service/alembic/versions/e9b1c3d7a5f2_account_logo_image.py`
- Modify: `services/accounting-service/tests/integration/test_migration.py:23-33` + the 13 `REPORTS_HEAD` asserts
- Test: `services/accounting-service/tests/integration/test_account_logo_api.py` (new; this task adds the cascade test only)

**Interfaces:**
- Produces: `class AccountLogoImage(Base)` with `__tablename__ = "account_logo_image"`, columns `account_id: Integer PK, ForeignKey("account.id", ondelete="CASCADE")`, `png: LargeBinary not null`, `sha256: String(64) not null`, `source_media_type: String(32) not null`, `source_bytes: Integer not null`, `created_at`/`updated_at: DateTime(timezone=True), nullable=False, server_default=func.now()` (updated_at also `onupdate=func.now()`) — declared explicitly, not via `TimestampMixin`, because the mixin's columns are nullable and §4 says not null. Migration revision `e9b1c3d7a5f2`, down `c7a2e9f4b1d8`.

- [ ] **Step 0: Dependency first**

Add `pillow==12.3.0` to `requirements.txt` and run the environment prerequisite command from the header. Verify: `.venv/bin/python -c "import PIL; print(PIL.__version__)"` prints `12.3.0`.

- [ ] **Step 1: Write the failing migration tests**

In `tests/integration/test_migration.py`: add `ACCOUNT_LOGO_IMAGE_HEAD = "e9b1c3d7a5f2"` after `REPORTS_HEAD`, replace every `assert _version(url) == REPORTS_HEAD` with `ACCOUNT_LOGO_IMAGE_HEAD`, and add, following the shape of the `account.logo` round-trip test already in the file:

Copy the shape of `test_reports_round_trip` (fixtures `database_factory`, `alembic_config`; helpers `_version`,
`_table_present`, `_column`, `command.upgrade/downgrade`):

```python
def test_account_logo_image_round_trip(database_factory, alembic_config):
    url = database_factory(); cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    assert _version(url) == ACCOUNT_LOGO_IMAGE_HEAD
    assert _table_present(url, "account_logo_image")
    assert {c for c in ("account_id", "png", "sha256", "source_media_type", "source_bytes", "created_at", "updated_at")
            if _column(url, "account_logo_image", c) is not None} == {"account_id", "png", "sha256", "source_media_type", "source_bytes", "created_at", "updated_at"}
    assert _foreign_key_ondelete(url, "account_logo_image") == "CASCADE"   # new helper beside _column: inspect(...).get_foreign_keys(table)[0]["options"].get("ondelete")
    for c in ("png", "sha256", "source_media_type", "source_bytes", "created_at", "updated_at"):
        assert _column(url, "account_logo_image", c)["nullable"] is False, c
    command.downgrade(cfg, REPORTS_HEAD)
    assert _version(url) == REPORTS_HEAD and not _table_present(url, "account_logo_image")
    command.upgrade(cfg, "head")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/integration/test_migration.py -k "head or logo_image"`
Expected: FAIL — head is still `c7a2e9f4b1d8`, table missing.

- [ ] **Step 3: Add the model**

In `app/models/ledger.py` import `LargeBinary` from `sqlalchemy` and append after `Account`:

```python
class AccountLogoImage(Base):
    """Uploaded account logo, already normalised to a 256×256 PNG by account_logo_service. One row per account."""
    __tablename__ = "account_logo_image"
    account_id = Column(Integer, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True)
    png = Column(LargeBinary, nullable=False)
    sha256 = Column(String(64), nullable=False)
    source_media_type = Column(String(32), nullable=False)
    source_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

Export `AccountLogoImage` from `app/models/__init__.py` in the alphabetical position of the existing import list.

- [ ] **Step 4: Write the migration**

`alembic/versions/e9b1c3d7a5f2_account_logo_image.py`, header copied from `b8d4f2a1c6e3_account_logo.py` with `revision = "e9b1c3d7a5f2"`, `down_revision = "c7a2e9f4b1d8"`, docstring "account_logo_image: uploaded, server-normalised account logos (bytea)". `upgrade()` = `op.create_table("account_logo_image", sa.Column("account_id", sa.Integer(), sa.ForeignKey("account.id", ondelete="CASCADE"), primary_key=True), sa.Column("png", sa.LargeBinary(), nullable=False), sa.Column("sha256", sa.String(64), nullable=False), sa.Column("source_media_type", sa.String(32), nullable=False), sa.Column("source_bytes", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))`; `downgrade()` = `op.drop_table("account_logo_image")`.

- [ ] **Step 5: Run migration tests**

Run: `pytest -q tests/integration/test_migration.py`
Expected: all pass (the disposable DB migrates to the new head).

- [ ] **Step 6: Write the cascade test (Review Focus 5)**

Create `tests/integration/test_account_logo_api.py` with a module-level helper used by Task 4 too:

```python
from io import BytesIO
from PIL import Image

def _png(w: int, h: int, mode: str = "RGBA", color=(200, 30, 30, 255)) -> bytes:
    buf = BytesIO(); Image.new(mode, (w, h), color).save(buf, format="PNG"); return buf.getvalue()

def _account(client, **fields) -> dict:
    body = {"name": "測試帳戶", "currency": "TWD"}; body.update(fields)
    return client.post("/accounts", json=body).json()

def test_account_delete_cascades_logo_image(client, db_session):
    acct = _account(client)
    db_session.add(AccountLogoImage(account_id=acct["id"], png=_png(256, 256), sha256="a" * 64,
                                    source_media_type="image/png", source_bytes=1)); db_session.commit()
    assert client.delete(f"/accounts/{acct['id']}").status_code == 204
    assert db_session.get(AccountLogoImage, acct["id"]) is None
```

- [ ] **Step 7: Run it**

Run: `pytest -q tests/integration/test_account_logo_api.py`
Expected: PASS (an account with no entries hard-deletes; the FK cascade removes the row).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt app/models alembic/versions/e9b1c3d7a5f2_account_logo_image.py tests/integration/test_migration.py tests/integration/test_account_logo_api.py
git commit -m "feat(accounting): account_logo_image table and migration e9b1c3d7a5f2"
```

---

### Task 2: `normalise_logo` — pure Pillow normaliser with the full gate

**Files:**
- Create: `services/accounting-service/app/services/account_logo_service.py`
- Test: `services/accounting-service/tests/unit/test_account_logo_service.py`

**Interfaces:**
- Produces:
  - constants `MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024`, `MAX_LOGO_PIXELS = 25_000_000`, `MIN_SIDE = 16`, `MAX_SIDE = 8192`, `OUTPUT_SIDE = 256`, `ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})`, `HASH_PREFIX = 16`
  - `class LogoError(Exception)` with `code: str` and `status: int` (`logo_too_large` → 413, everything else 422)
  - `@dataclass(frozen=True) class NormalisedLogo: png: bytes; sha256: str; source_media_type: str; source_bytes: int`
  - `def normalise_logo(data: bytes, *, declared_type: str | None = None) -> NormalisedLogo`
  - `def logo_hash(sha256: str) -> str` (first 16 hex chars)
  - (Task 3/4 add `put_logo`, `delete_logo`, `get_logo` to this module.)

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/test_account_logo_service.py` — fixtures are generated with Pillow in the test (no files in the repo). Helper `_img(w, h, fmt="PNG", mode="RGBA", **save_kw) -> bytes` plus:

```python
def test_rejects_empty():            # LogoError.code == "logo_empty", status 422
def test_rejects_non_image_text():   # b"<svg xmlns='http://www.w3.org/2000/svg'/>" → "logo_unreadable"
@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_rejects_other_formats(fmt): # valid image in fmt → "logo_format"
@pytest.mark.parametrize("w,h", [(15, 100), (100, 15), (8193, 20), (20, 8193)])
def test_rejects_sides(w, h):        # → "logo_dimensions"
def test_rejects_pixel_count_in_pillow_warning_band():  # 6000×6000 PNG (36 M) → "logo_dimensions"
def test_rejects_pixel_count_in_pillow_error_band():    # 9000×9000 PNG → "logo_dimensions" (DecompressionBombError path)
def test_output_is_256_rgba_png_with_transparent_padding():
    out = normalise_logo(_img(300, 100, color=(255, 0, 0, 255)))
    im = Image.open(BytesIO(out.png)); assert (im.format, im.mode, im.size) == ("PNG", "RGBA", (256, 256))
    assert im.getpixel((128, 2))[3] == 0 and im.getpixel((128, 128))[:3] == (255, 0, 0)
    assert "exif" not in im.info and out.sha256 == hashlib.sha256(out.png).hexdigest()
@pytest.mark.parametrize("w,h,expect", [(8192, 16, (256, 1)), (16, 8192, (1, 256))])
def test_extreme_aspect_shrinks_first(w, h, expect):  # bbox of alpha>0 has size `expect`, centred
def test_exif_orientation_is_applied():  # JPEG 200×100 red-left/blue-right with EXIF orientation 6 → after normalise the red is on top
def test_palette_transparency_survives():  # mode "P" PNG with transparency index → corner pixel alpha 0 after normalise
def test_webp_accepted_and_jpeg_draft_used(monkeypatch):  # WEBP 300×300 ok; for a 3000×3000 JPEG, spy Image.Image.draft is called before load
def test_same_bytes_same_hash():
def test_declared_type_is_recorded_not_trusted():  # declared "image/svg+xml" with real PNG bytes → ok, source_media_type == "image/png"
def test_translucent_pixels_keep_their_alpha_and_colour():
    out = normalise_logo(_img(256, 256, color=(0, 0, 255, 128)))        # half-transparent blue square
    px = Image.open(BytesIO(out.png)).getpixel((128, 128))
    assert px[:3] == (0, 0, 255) and 124 <= px[3] <= 132                 # alpha applied once, RGB untouched
def test_rejected_inputs_never_decode_pixels(monkeypatch):
    calls = []; monkeypatch.setattr(Image.Image, "load", lambda self: calls.append("load") or None)
    for bad in (_img(100, 100, fmt="GIF"), _img(15, 15), _img(6000, 6000)):
        with pytest.raises(LogoError): normalise_logo(bad)
    assert calls == []                                                   # format/dimension gates run before load()
def test_corrupt_pixel_data_is_unreadable():
    good = _img(300, 100); corrupt = good[:len(good) // 2]              # valid header, truncated IDAT
    with pytest.raises(LogoError) as e: normalise_logo(corrupt)
    assert e.value.code == "logo_unreadable"
def test_shrink_happens_before_transpose(monkeypatch):
    order = []
    real_thumb, real_tr = Image.Image.thumbnail, ImageOps.exif_transpose
    monkeypatch.setattr(Image.Image, "thumbnail", lambda self, *a, **k: order.append("thumbnail") or real_thumb(self, *a, **k))
    monkeypatch.setattr(ImageOps, "exif_transpose", lambda im, *a, **k: order.append("transpose") or real_tr(im, *a, **k))
    normalise_logo(_jpeg_with_orientation(6))
    assert order == ["thumbnail", "transpose"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/unit/test_account_logo_service.py`
Expected: FAIL — `ModuleNotFoundError: app.services.account_logo_service`.

- [ ] **Step 3: Implement the module**

`normalise_logo` follows §5 steps 1–5 exactly: empty → `logo_empty`; `len(data) > MAX_LOGO_UPLOAD_BYTES` → `logo_too_large`; module-level `Image.MAX_IMAGE_PIXELS = MAX_LOGO_PIXELS`; one `try` spanning open **and** the later steps that maps `Image.DecompressionBombError`/`DecompressionBombWarning` (raised as error inside `warnings.catch_warnings()` + `simplefilter("error", Image.DecompressionBombWarning)`) → `logo_dimensions`, `UnidentifiedImageError`/`OSError`/`ValueError` → `logo_unreadable`; after `Image.open`: format check, then header dims check (`MIN_SIDE`, `MAX_SIDE`, product), then for `im.format == "JPEG"` call `im.draft("RGB", (OUTPUT_SIDE * 2, OUTPUT_SIDE * 2))`, `im.load()`, `im.thumbnail((OUTPUT_SIDE, OUTPUT_SIDE), Image.LANCZOS, reducing_gap=3.0)`, then `ImageOps.exif_transpose(im)` **on the thumbnail** (EXIF survives `thumbnail` through `im.info["exif"]`; `test_shrink_happens_before_transpose` pins the order), `convert("RGBA")`, then `canvas = Image.new("RGBA", (OUTPUT_SIDE, OUTPUT_SIDE), (0, 0, 0, 0)); canvas.paste(im, (x, y))` **without a mask** — a mask-less RGBA paste copies the pixels including their alpha, whereas passing the image as its own mask multiplies alpha in twice and darkens translucent edges — save PNG `optimize=True` into a `BytesIO`. `source_media_type` = `Image.MIME[im.format]`. Palette transparency: convert `P` with `transparency` in `im.info` via `convert("RGBA")` — Pillow honours `tRNS` in that conversion.

- [ ] **Step 4: Run tests**

Run: `pytest -q tests/unit/test_account_logo_service.py`
Expected: PASS. The order thumbnail → transpose is part of the accepted resource contract (§5 step 4) and is not negotiable: if the EXIF test fails because `thumbnail` dropped the metadata, read `im.getexif().get(0x0112)` **before** `thumbnail`, keep the integer, and apply the matching `Image.Transpose` operation to the thumbnail yourself (a small `_apply_orientation(im, orientation)` helper) — never transpose the full-size image.

- [ ] **Step 5: Memory budget test (§5 step 4, measured not claimed)**

Append to the unit test file:

```python
def test_peak_rss_budget_for_max_input(tmp_path):
    # Fixture written by this process; the subprocess only normalises, so its RSS is the normaliser's.
    for mode in ("RGBA", "LA"):
        (tmp_path / f"{mode}.png").write_bytes(_img(5000, 5000, mode=mode))
    script = "import sys, resource; from app.services.account_logo_service import normalise_logo; " \
             "normalise_logo(open(sys.argv[1],'rb').read()); print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    for mode in ("RGBA", "LA"):
        out = subprocess.run([sys.executable, "-c", script, str(tmp_path / f"{mode}.png")], capture_output=True, text=True,
                             check=True, env={**os.environ, "PYTHONPATH": "../shared-python-lib:."})
        assert int(out.stdout) * 1024 < PEAK_RSS_BUDGET_BYTES, mode
```

Run once with `PEAK_RSS_BUDGET_BYTES = 10**12` and print the two measurements in the commit body; then set `PEAK_RSS_BUDGET_BYTES` in the test to the larger measurement × 1.5 rounded up to the next 64 MiB. (`ru_maxrss` is KiB on Linux.)

- [ ] **Step 6: Add Pillow to requirements and commit**

```bash
git add app/services/account_logo_service.py tests/unit/test_account_logo_service.py
git commit -m "feat(accounting): normalise_logo — Pillow gate and 256px padded PNG"
```

---

### Task 3: `logo_hash` on every account read + logo row helpers

**Files:**
- Modify: `services/accounting-service/app/services/ledger_service.py:151-214` (`_account_rows`)
- Modify: `services/accounting-service/app/schemas/ledger.py:94-129` (`AccountOut`)
- Modify: `services/accounting-service/app/services/account_logo_service.py` (add `put_logo`, `delete_logo`, `get_logo`)
- Test: `services/accounting-service/tests/integration/test_account_logo_api.py`

**Interfaces:**
- Consumes: `AccountLogoImage` (Task 1), `NormalisedLogo`, `normalise_logo`, `logo_hash` (Task 2).
- Produces:
  - `AccountOut.logo_hash: str | None` (list, detail, write responses)
  - `def put_logo(db: Session, account_id: int, logo: NormalisedLogo) -> None` — raises `NotFoundError("account not found")` when the account does not exist; upserts the row (`db.merge` or select-then-set), sets `updated_at` via `func.now()`; no commit.
  - `def delete_logo(db: Session, account_id: int) -> bool` — raises `NotFoundError` when the account does not exist; returns whether a row was deleted; no commit.
  - `def get_logo(db: Session, account_id: int) -> AccountLogoImage | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_logo_hash_is_null_without_image(client):
    acct = _account(client)
    assert client.get("/accounts").json()[0]["logo_hash"] is None
    assert client.get(f"/accounts/{acct['id']}").json()["logo_hash"] is None

def test_logo_hash_is_sha256_prefix_on_list_detail_and_put(client, db_session):
    acct = _account(client)
    put_logo(db_session, acct["id"], normalise_logo(_png(300, 100))); db_session.commit()
    sha = db_session.get(AccountLogoImage, acct["id"]).sha256
    assert client.get("/accounts").json()[0]["logo_hash"] == sha[:16]
    assert client.get(f"/accounts/{acct['id']}").json()["logo_hash"] == sha[:16]
    body = {"name": acct["name"], "currency": "TWD"}
    assert client.put(f"/accounts/{acct['id']}", json=body).json()["logo_hash"] == sha[:16]

def test_list_query_does_not_select_png(client, db_session):
    # capture SQL with sqlalchemy event "before_cursor_execute" on db_session.get_bind(); assert "png" not in any list statement
def test_put_logo_unknown_account_raises(db_session):  # NotFoundError
def test_delete_logo_returns_false_when_absent(client, db_session):
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/integration/test_account_logo_api.py`
Expected: FAIL — `KeyError: 'logo_hash'` / `ImportError: put_logo`.

- [ ] **Step 3: Implement**

In `_account_rows`: build `logo_hash_sq = select(func.substr(AccountLogoImage.sha256, 1, HASH_PREFIX)).where(AccountLogoImage.account_id == Account.id).correlate(Account).scalar_subquery().label("logo_hash")`, add it as the fifth selected column, extend every tuple unpack (lines 168, 170, 175, 180, 183) to `account, group_name, balance, count, logo_hash`, and write `item["logo_hash"] = logo_hash` inside the loop next to `"group_name"`. Do **not** touch `ACCOUNT_FIELDS`. Add `logo_hash: str | None` to `AccountOut` after `logo`. Implement the three helpers in `account_logo_service.py`.

- [ ] **Step 4: Run the accounts suites**

Run: `pytest -q tests/integration/test_account_logo_api.py tests/integration/test_accounts_api.py tests/integration/test_settings_crud_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/ledger_service.py app/schemas/ledger.py app/services/account_logo_service.py tests/integration/test_account_logo_api.py
git commit -m "feat(accounting): logo_hash on account reads; logo row helpers"
```

---

### Task 4: Routes — `PUT`/`GET`/`DELETE /accounts/{id}/logo`

**Files:**
- Modify: `services/accounting-service/app/routers/accounts.py` (imports; new routes after `remove_account`, line ~160)
- Test: `services/accounting-service/tests/integration/test_account_logo_api.py`

**Interfaces:**
- Consumes: Task 2/3 service functions and constants.
- Produces (HTTP contract for the SPA and Hermes):
  - `PUT /accounts/{account_id}/logo` — raw body; 200 `AccountDetailOut`; 413/422/404 as Global Constraints.
  - `GET /accounts/{account_id}/logo.png?v=` — `image/png`; headers per Global Constraints; 304 on `If-None-Match`; 404 when no account or no image.
  - `DELETE /accounts/{account_id}/logo` — 204 (also when no image); 404 unknown account.
  - helper `async def _read_body_capped(request: Request, cap: int) -> bytes` raising `LogoError("logo_too_large")` the moment the running total exceeds `cap`.

- [ ] **Step 1: Write the failing route tests**

```python
def test_put_logo_round_trip(client):
    acct = _account(client)
    r = client.put(f"/accounts/{acct['id']}/logo", content=_png(300, 100), headers={"Content-Type": "image/png"})
    assert r.status_code == 200 and len(r.json()["logo_hash"]) == 16
    img = client.get(f"/accounts/{acct['id']}/logo.png?v={r.json()['logo_hash']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert img.headers["x-content-type-options"] == "nosniff"
    assert img.headers["content-disposition"] == 'inline; filename="logo.png"'
    assert img.headers["etag"].strip('"').startswith(r.json()["logo_hash"])
    assert Image.open(BytesIO(img.content)).size == (256, 256)

def test_get_without_v_or_with_stale_v_is_no_store(client):   # Review Focus 4
    acct = _account(client)
    a = client.put(..., content=_png(300, 100), ...).json()["logo_hash"]
    b = client.put(..., content=_png(100, 300), ...).json()["logo_hash"]
    assert a != b
    stale = client.get(f"/accounts/{acct['id']}/logo.png?v={a}")
    assert stale.status_code == 200 and stale.headers["cache-control"] == "no-store"
    assert stale.headers["etag"].strip('"').startswith(b)      # current bytes, not A
    assert client.get(f"/accounts/{acct['id']}/logo.png").headers["cache-control"] == "no-store"
    # A→B→A: re-upload A and the matching v is immutable again
    assert client.put(..., content=_png(300, 100), ...).json()["logo_hash"] == a
    assert client.get(f"/accounts/{acct['id']}/logo.png?v={a}").headers["cache-control"].endswith("immutable")

def test_get_if_none_match_returns_304(client):
def test_get_404_without_image_and_for_unknown_account(client):
def test_put_rejects_content_length_over_cap_before_reading(client):
    # headers Content-Length = str(MAX_LOGO_UPLOAD_BYTES + 1), content=b"x" → 413, message "logo_too_large"
def test_streamed_body_over_cap_stops_reading(client):     # Review Focus 3
    # generator body (no Content-Length) yielding 1 MiB chunks forever but counting; expect 413 and ≤ 6 chunks consumed
def test_put_empty_body_is_422_logo_empty(client):
@pytest.mark.parametrize("body,token", [(b"<svg/>", "logo_unreadable"), (GIF_BYTES, "logo_format"), (PNG_15x15, "logo_dimensions")])
def test_put_validation_tokens(client, body, token):         # response.json()["message"] == token, status 422
def test_put_unknown_account_404(client):                   # message "account not found"
def test_error_envelope_shape(client):
    acct = _account(client)
    r = client.put(f"/accounts/{acct['id']}/logo", content=b"<svg/>", headers={"Content-Type": "image/svg+xml"})
    assert r.status_code == 422 and set(r.json()) == {"code", "message", "trace_id"}
    assert r.json()["code"] == 422 and r.json()["message"] == "logo_unreadable"
def test_stored_and_served_bytes_are_the_normalised_png_not_the_upload(client, db_session):
    acct = _account(client); upload = _png(300, 100)
    h = client.put(f"/accounts/{acct['id']}/logo", content=upload, headers={"Content-Type": "image/png"}).json()["logo_hash"]
    row = db_session.get(AccountLogoImage, acct["id"]); expected = normalise_logo(upload)
    assert row.png == expected.png and row.sha256 == expected.sha256 and row.png != upload
    assert client.get(f"/accounts/{acct['id']}/logo.png?v={h}").content == expected.png
def test_delete_logo_204_twice_and_falls_back_to_slug(client):
    acct = _account(client, logo="cathay-united"); client.put(... logo ...)
    assert client.delete(f"/accounts/{acct['id']}/logo").status_code == 204
    assert client.delete(f"/accounts/{acct['id']}/logo").status_code == 204
    d = client.get(f"/accounts/{acct['id']}").json(); assert d["logo_hash"] is None and d["logo"] == "cathay-united"
def test_delete_logo_unknown_account_404(client):
def test_put_same_bytes_is_idempotent(client):               # same logo_hash, updated_at moves (read via db_session)
```

And the event-loop test (§5 limits step 3):

```python
@pytest.fixture
def anyio_backend(): return "asyncio"

@pytest.mark.anyio
async def test_slow_worker_does_not_block_health_and_all_sync_work_is_on_one_worker_thread(client, monkeypatch):
    from app.main import app
    import app.routers.accounts as accounts_router
    seen: dict[str, int] = {}
    def spy(name, fn):
        def wrapped(*a, **kw): seen[name] = threading.get_ident(); return fn(*a, **kw)
        return wrapped
    svc = accounts_router.account_logo_service
    monkeypatch.setattr(svc, "normalise_logo", spy("normalise", lambda d, **kw: (time.sleep(1.0), normalise_logo(d, **kw))[1]))
    monkeypatch.setattr(svc, "put_logo", spy("put", svc.put_logo))
    monkeypatch.setattr(accounts_router.ledger_service, "get_account", spy("detail", accounts_router.ledger_service.get_account))
    real_commit = Session.commit; monkeypatch.setattr(Session, "commit", spy("commit", real_commit))
    loop_thread = threading.get_ident()
    acct = _account(client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as ac:
        async def upload(): return await ac.put(f"/accounts/{acct['id']}/logo", content=_png(300, 100), headers={"Content-Type": "image/png"})
        async def health():
            await anyio.sleep(0.1); t0 = time.perf_counter(); r = await ac.get("/health"); return time.perf_counter() - t0, r.status_code
        (up, (elapsed, status)) = await asyncio.gather(upload(), health())  # or anyio task group
    assert up.status_code == 200 and status == 200 and elapsed < 0.5
    assert set(seen) == {"normalise", "put", "commit", "detail"}
    assert len(set(seen.values())) == 1 and loop_thread not in seen.values()   # one worker thread, never the event loop
```

(The `commit` spy is only the final commit of the upload; `_account` runs before the spy is installed. If the
`Session.commit` patch catches commits from other code paths in the test, narrow it by asserting on the commit
that happened between the `put` and `detail` spies — the point is that the upload's commit is on the worker thread.)

`test_scope_enforcement` already enumerates the new PUT/DELETE and expects 403 for the read-only `hermes` token with `json={}`; make sure the 403 fires before the body is read (the router-level dependency runs first — verify by running that suite).

- [ ] **Step 2: Run to verify failure**

Run: `pytest -q tests/integration/test_account_logo_api.py`
Expected: FAIL — 404/405 on the new paths.

- [ ] **Step 3: Implement the routes**

In `routers/accounts.py` add imports `from fastapi import Request`, `from starlette.concurrency import run_in_threadpool`, `from ..services import account_logo_service`, `from ..services.account_logo_service import LogoError, MAX_LOGO_UPLOAD_BYTES, logo_hash`. Add:

- `async def _read_body_capped(request, cap)`: `Content-Length` header parsed as int → raise `LogoError("logo_too_large")` if `> cap`; then `async for chunk in request.stream()` accumulating into a `bytearray`, raising the same error the moment `len(buf) > cap`; return `bytes(buf)`.
- `@router.put("/{account_id}/logo", response_model=AccountDetailOut) async def put_account_logo(account_id: int, request: Request, db: Session = Depends(get_db))`: `data = await _read_body_capped(request, MAX_LOGO_UPLOAD_BYTES)` inside a `try/except LogoError as exc: raise HTTPException(exc.status, exc.code)`; then `return await run_in_threadpool(_store_logo, db, account_id, data, request.headers.get("content-type"))` where `_store_logo` (sync, module-level) does `with service_errors(): logo = normalise_logo(...)` mapping `LogoError` → `HTTPException(exc.status, exc.code)`, `put_logo(db, account_id, logo)`, `db.commit()`, `return ledger_service.get_account(db, account_id)`.
- `@router.get("/{account_id}/logo.png") def get_account_logo(account_id: int, request: Request, v: str | None = Query(default=None), db: Session = Depends(get_db))`: 404 `account not found` when `_require_account` fails; `row = get_logo(...)` → 404 `logo not found` when `None`; `etag = f'"{row.sha256}"'`; if `request.headers.get("if-none-match") == etag` → `Response(status_code=304, headers={"ETag": etag})`; `cache = "private, max-age=31536000, immutable" if v == logo_hash(row.sha256) else "no-store"`; `return Response(content=bytes(row.png), media_type="image/png", headers={"ETag": etag, "Cache-Control": cache, "X-Content-Type-Options": "nosniff", "Content-Disposition": 'inline; filename="logo.png"'})`.
- `@router.delete("/{account_id}/logo", status_code=204) def delete_account_logo(...)`: `with service_errors(): delete_logo(db, account_id)`; `db.commit()`; `return Response(status_code=204)`.

Declare the three routes **before** the `PATCH /{account_id}` route only if a routing conflict shows up in tests; path segments differ, so the default order should be fine.

- [ ] **Step 4: Run the suites**

Run: `pytest -q tests/integration/test_account_logo_api.py tests/integration/test_scope_enforcement.py tests/integration/test_accounts_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routers/accounts.py tests/integration/test_account_logo_api.py
git commit -m "feat(accounting): PUT/GET/DELETE /accounts/{id}/logo with streamed cap and ETag cache policy"
```

---

### Task 5: OpenSpec change `account-logo-upload`

**Files:**
- Create: `openspec/changes/account-logo-upload/proposal.md`, `tasks.md`, `specs/accounting-ledger/spec.md`, `specs/frontend-accounting-ledger/spec.md`, `specs/frontend-ui-primitives/spec.md`

**Interfaces:**
- Consumes: merged spec text from Task 0; contract from Tasks 3–4; SPA behaviour from Tasks 6–9 (write the frontend deltas now; later tasks implement them).

- [ ] **Step 1: Write the deltas**

Rules for 1.15 strict: a `## MODIFIED Requirements` block restates the **whole** requirement with all its scenarios; requirements ≤ 500 chars each (split into `### Requirement:` blocks when longer); every requirement has ≥ 1 `#### Scenario:`.

- `accounting-ledger`: MODIFIED `### Requirement: Account model` (add the `account_logo_image` side table sentence + scenario "Uploaded logo cascades with the account"); MODIFIED `### Requirement: Read-only ledger endpoints` (add `logo_hash` to the field list + scenario "logo_hash is the sha256 prefix or null"); ADDED `### Requirement: Account logo upload` (PUT raw body, caps, format allow-list, normalisation, error tokens; scenarios: valid PNG → 256×256 RGBA + `logo_hash`; 6000×6000 → 422 `logo_dimensions`; body > 5 MiB without Content-Length → 413 cut off; SVG text → 422 `logo_unreadable`); ADDED `### Requirement: Account logo image endpoint` (GET headers, `v` cache policy, 304, 404; scenarios: matching `v` → immutable; stale `v` → no-store with current bytes; If-None-Match → 304); ADDED `### Requirement: Account logo removal` (DELETE 204 twice; 404 unknown account).
- `frontend-accounting-ledger`: MODIFIED `### Requirement: Accounts list page` (precedence uploaded → slug → emoji; scenario "Uploaded logo wins over the bundled slug"); MODIFIED `### Requirement: Account entry history page` (header avatar button 更換 logo, file picker, busy state, toast copy; scenario "Upload from the account page"; scenario "Upload rejected" with the four copy strings); MODIFIED `### Requirement: Account settings page` (Logo block: state line 已上傳的圖片 / 內建：<label> / 無, buttons 上傳圖片 / 移除圖片 / 選擇內建 logo, 上傳後立即生效, disabled on new account with 先建立帳戶，再上傳 logo; scenario "Image write keeps the dirty form").
- `frontend-ui-primitives`: MODIFIED `### Requirement: Line icons for interface chrome` — replace "from the app's bundled brand-logo set" with "from the app's bundled brand-logo set or uploaded through the account logo endpoint (served as a `blob:` object URL)".
- `proposal.md`: why (per-logo PRs), what changes (three bullets), impact (API, SPA, OpenSpec); `tasks.md`: the task list of this plan with `- [ ]`.

- [ ] **Step 2: Validate**

Run: `openspec validate account-logo-upload --strict`
Expected: `valid`.

- [ ] **Step 3: Commit**

```bash
git add openspec/changes/account-logo-upload
git commit -m "docs(openspec): account-logo-upload change (API, SPA, icon slots)"
```

---

### Task 6: SPA model + service + error copy

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts:164` (after `logo`)
- Modify: `frontend/src/app/services/accounting.service.ts:146-152` (after `deleteAccount`)
- Create: `frontend/src/app/components/accounting/logo-upload-errors.ts`
- Test: `frontend/src/app/components/accounting/logo-upload-errors.spec.ts`, `frontend/src/app/services/accounting.service.spec.ts` (extend; if absent create next to the service)

**Interfaces:**
- Produces:
  - `LedgerAccount.logo_hash: string | null` (JSDoc: "sha256 prefix of the uploaded logo image; null = none. Uploaded image beats `logo`.")
  - `AccountingService.uploadAccountLogo(id: number, file: Blob): Observable<AccountDetail>` — `PUT ${apiUrl}/accounts/${id}/logo`, body `file`, `headers: { 'Content-Type': file.type || 'application/octet-stream' }`, wrapped in `bumpAccounts`.
  - `AccountingService.deleteAccountLogo(id: number): Observable<void>` — `DELETE`, wrapped in `bumpAccounts`.
  - `AccountingService.logoImageUrl(id: number, hash: string): string` → `` `${apiUrl}/accounts/${id}/logo.png?v=${encodeURIComponent(hash)}` ``.
  - `AccountingService.refreshAccounts(): void` — bumps `accountChangeCount` (public wrapper, used by the store on an ETag mismatch).
  - `MAX_LOGO_FILE_BYTES = 5 * 1024 * 1024`, `LOGO_ACCEPT = 'image/png,image/jpeg,image/webp'`, `logoUploadErrorMessage(err: unknown): string` mapping: `err instanceof HttpErrorResponse` with status 413 or a `TooLargeError`-like local marker → `圖片太大（上限 5 MB）`; 422 with message `logo_format` → `只接受 PNG、JPEG、WebP`; 422 `logo_dimensions` → `圖片尺寸不符（每邊 16–8192 px）`; 422 `logo_unreadable`/`logo_empty` → `這個檔案無法當作圖片使用`; 404 → `伺服器尚未支援上傳`; else `writeErrorMessage(err)`'s generic text. Also `export function logoFileTooLarge(file: Blob): boolean`.

- [ ] **Step 1: Write failing specs**

`logo-upload-errors.spec.ts`: one `it` per mapping above using `new HttpErrorResponse({ status, error: { code: status, message } })`; `logoFileTooLarge(new Blob([new Uint8Array(MAX_LOGO_FILE_BYTES + 1)]))` is true.
Service spec: with `HttpTestingController`, `uploadAccountLogo(7, new Blob([1], { type: 'image/png' }))` issues `PUT /api/accounting/accounts/7/logo` with `Content-Type: image/png` and a Blob body and bumps `accountsChanged`; `deleteAccountLogo(7)` issues DELETE; `logoImageUrl(7, 'abc')` equals `/api/accounting/accounts/7/logo.png?v=abc`.

- [ ] **Step 2: Run to verify failure**

Run: `./node_modules/.bin/ng test --watch=false --include='**/logo-upload-errors.spec.ts' --include='**/accounting.service.spec.ts'`
Expected: FAIL (missing exports).

- [ ] **Step 3: Implement** the four service members, the model field, and the errors module.

- [ ] **Step 4: Run** the same two specs → PASS; also `--include='**/account-settings.spec.ts'` to make sure the `account()` factory still type-checks (it casts, so no change expected).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/models/accounting.model.ts frontend/src/app/services/accounting.service.ts frontend/src/app/services/accounting.service.spec.ts frontend/src/app/components/accounting/logo-upload-errors.ts frontend/src/app/components/accounting/logo-upload-errors.spec.ts
git commit -m "feat(frontend): logo_hash model field, logo upload/delete service calls, error copy"
```

---

### Task 7: `AccountLogoStore`

**Files:**
- Create: `frontend/src/app/services/account-logo-store.ts`
- Test: `frontend/src/app/services/account-logo-store.spec.ts`

**Interfaces:**
- Consumes: `AccountingService.logoImageUrl`, `AccountingService.refreshAccounts` (Task 6).
- Produces: `@Injectable({ providedIn: 'root' }) export class AccountLogoStore { url(accountId: number, hash: string | null): Signal<string | null>; }` — contract per §6.1: per-account `WritableSignal<string | null>`; state `{ hash: string | null; failedHash: string | null; generation: number }`; in-flight map keyed `` `${id}:${hash}` ``; generation check **before** ETag; ETag (first 16 hex of the unquoted value) decides the cache key; mismatch → store under actual hash, set state.hash = actual, call `refreshAccounts()`; missing ETag → show, do not cache (state.hash stays `hash`, `failedHash` unset, next call with the same hash refetches); error → `failedHash = hash`, signal `null`; `null` hash → signal `null` and revoke; revoke the previous URL via `setTimeout(() => URL.revokeObjectURL(old), 0)` only after the signal has been set and only if it is no longer any account's current value.

- [ ] **Step 1: Write failing specs** (`HttpTestingController`; stub `URL.createObjectURL`/`revokeObjectURL` with `vi.fn()` returning `blob:${n}`):

```ts
it('fetches once for N slots of the same (id, hash) and exposes the object URL')
it('sends the bearer-safe URL with ?v=<hash> and responseType blob')
it('drops a late response from a superseded generation even when its hash matches the current one')  // A slow, B fast, then A again: the first A response must not win
it('etag mismatch is cached under the actual hash and refreshes accounts')  // Review Focus 4
it('mismatch B then model reload A refetches A (A→B→A end to end)')  // ask ?v=A, answer ETag B (no-store) → url is blob B, refreshAccounts called; then url(id, 'A') again → one new GET ?v=A, answer ETag A → url is blob A, blob B revoked next macrotask
it('missing etag shows the image but caches nothing')
it('404 marks the hash failed, exposes null, and does not retry until the hash changes')
it('null hash exposes null and revokes the previous URL on the next macrotask')  // vi.useFakeTimers
it('does not revoke a URL that is still current for the account')
```

- [ ] **Step 2: Run to verify failure** — `--include='**/account-logo-store.spec.ts'` → FAIL (module missing).

- [ ] **Step 3: Implement** per the Interfaces block. Use `http.get(url, { responseType: 'blob', observe: 'response' })`, `shareReplay(1)` on the in-flight observable, `finalize` to drop it from the map.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/services/account-logo-store.ts frontend/src/app/services/account-logo-store.spec.ts
git commit -m "feat(frontend): AccountLogoStore — blob fetch, generation guard, ETag-keyed cache"
```

---

### Task 8: `<app-account-icon>` precedence + all call sites

**Files:**
- Modify: `frontend/src/app/components/accounting/account-icon/account-icon.ts` (+ `account-icon.spec.ts`)
- Modify: `frontend/src/app/components/accounting/accounts/accounts.html:21`
- Modify: `frontend/src/app/components/accounting/account-picker/account-picker.html:15,51,71`
- Modify: `frontend/src/app/components/accounting/reminders/reminders.ts:67-75,160-178` and `reminders.html:45`
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.html:18,65` (line 74 — the grid — stays slug-only)

**Interfaces:**
- Consumes: `AccountLogoStore.url` (Task 7), `LedgerAccount.logo_hash` (Task 6).
- Produces: `AccountIconComponent` inputs `accountId = input<number | null>(null)`, `logoHash = input<string | null>(null)`; `protected readonly uploadedUrl = computed(() => { const id = this.accountId(); return id !== null ? this.store.url(id, this.logoHash())() : null; })` — the hash is forwarded **even when null**, so a deletion reaches the store and runs its reset/revoke/generation invalidation (§6.1); only a missing account id skips the store; template order `@if (uploadedUrl(); as u) { <span class="tile"><img class="logo" [src]="u" alt="" aria-hidden="true" decoding="async" /></span> } @else if (entry(); as e) { …existing… } @else { …emoji… }`; host `[class.has-logo]` true when either path renders. `CardReminder` gains `logoHash: string | null`; `cardReminders(bills, accounts: Pick<LedgerAccount, 'id' | 'icon' | 'logo' | 'logo_hash'>[], today)` fills it.

- [ ] **Step 1: Write failing specs** in `account-icon.spec.ts`: host with `[accountId]="7" [logoHash]="'abc'"` and a stubbed `AccountLogoStore` (`{ url: () => signal('blob:1') }` via `TestBed` provider override) renders `img[src="blob:1"]` and `has-logo`; with the store returning `null` and `[logo]="'cathay-united'"` the bundled image renders; with both null the emoji renders; the settings grid usage `<app-account-icon [logo]="l.slug" />` (no account) still renders the bundled image; **and one integration spec with the real `AccountLogoStore` + `HttpTestingController` + stubbed `URL.createObjectURL/revokeObjectURL` + `vi.useFakeTimers()`**: `it('component + store: hash → null revokes the url and discards a pending response')` — host `[accountId]="7" [logoHash]="hash()"`; set `hash('a')`, flush a 200 blob (ETag a) → `img[src="blob:1"]`; set `hash('b')` (request pending), then `hash(null)` before answering → the img is gone (emoji shown), `revokeObjectURL('blob:1')` is called after `vi.runOnlyPendingTimers()`, and flushing the pending `b` response afterwards creates no new object URL. In `reminders.spec.ts` (existing) extend the `cardReminders` test: an account with `logo_hash: 'abc'` yields a card with `logoHash: 'abc'`.

- [ ] **Step 2: Run** `--include='**/account-icon.spec.ts' --include='**/reminders.spec.ts'` → FAIL.

- [ ] **Step 3: Implement** the component changes; update the five templates to add `[accountId]="account.id" [logoHash]="account.logo_hash"` (picker ×3, accounts list; settings big icon + current button bind `[accountId]="loadedId()" [logoHash]="detail()?.logo_hash ?? null"` for now — Task 10 replaces the hash binding with its `logoHash()` signal), reminders `[accountId]="card.accountId" [logoHash]="card.logoHash"`.

- [ ] **Step 4: Run** the two specs plus `--include='**/accounts.spec.ts' --include='**/account-picker.spec.ts' --include='**/account-settings.spec.ts'` → PASS; run `--include='**/theme/line-icons.spec.ts'` → PASS (no new emoji).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting
git commit -m "feat(frontend): account icon prefers the uploaded logo; wire logo_hash into every slot"
```

---

### Task 9: Account page avatar upload (owner's path)

**Files:**
- Modify: `frontend/src/app/components/accounting/account-entries/account-entries.html:2-8`, `account-entries.ts`, `account-entries.scss`
- Test: `frontend/src/app/components/accounting/account-entries/account-entries.spec.ts` (extend)

**Interfaces:**
- Consumes: `uploadAccountLogo`, `logoUploadErrorMessage`, `logoFileTooLarge`, `LOGO_ACCEPT` (Task 6), `AccountIconComponent` inputs (Task 8), `AccountingToastService.show`.
- Produces: in `AccountingAccountEntriesComponent`: `readonly uploadingLogo = signal(false)`, `readonly logoFile = viewChild.required<ElementRef<HTMLInputElement>>('logoFile')`, `protected readonly LOGO_ACCEPT = LOGO_ACCEPT`, `pickLogoFile(): void` (clicks the input), `onLogoFile(event: Event): void`.

- [ ] **Step 1: Write failing specs**: header contains `button.avatar[aria-label="更換 logo"]` wrapping `app-account-icon.big`; clicking it clicks the hidden `input[type=file][accept="image/png,image/jpeg,image/webp"]`; dispatching `change` with a 1-byte `File` of type `image/png` issues `PUT /api/accounting/accounts/<id>/logo` (HttpTestingController), sets `aria-busy="true"` while pending, and on a 200 with `logo_hash: 'abc'` the `account()` signal has `logo_hash === 'abc'` and `aria-busy` is gone; a 422 `logo_format` response shows toast `只接受 PNG、JPEG、WebP`; a file over `MAX_LOGO_FILE_BYTES` never issues a request and toasts `圖片太大（上限 5 MB）`; the input's `value` is cleared after each attempt so the same file can be chosen again.

- [ ] **Step 2: Run** `--include='**/account-entries.spec.ts'` → FAIL.

- [ ] **Step 3: Implement**: template per §6.2 (`<button type="button" class="avatar" aria-label="更換 logo" [attr.aria-busy]="uploadingLogo() || null" (click)="pickLogoFile()"><app-account-icon size="big" [accountId]="accountId()" [logoHash]="account()?.logo_hash ?? null" [logo]="account()?.logo ?? null" [icon]="account()?.icon ?? null" fallback="folder" /></button><input #logoFile type="file" class="visually-hidden" [accept]="LOGO_ACCEPT" (change)="onLogoFile($event)" />`); `onLogoFile`: read `files[0]`, clear `input.value`, size check → toast, else `uploadingLogo.set(true)` and `uploadAccountLogo(id, file).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ next: d => { if (d.id === this.accountId()) this.account.set(d); }, error: e => toast.show(logoUploadErrorMessage(e)), complete/finalize → uploadingLogo.set(false) })`. SCSS: `.avatar` is a borderless round button sized `--ico-size: 2.25rem` inline with the `h1`, `aria-busy` → reduced opacity; header stays one row at 360 px (check with the existing header flex rules).

- [ ] **Step 4: Run** → PASS; `line-icons.spec.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-entries
git commit -m "feat(frontend): account page avatar uploads a logo"
```

---

### Task 10: Account settings Logo block

**Files:**
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.html:61-78`, `account-settings.ts` (fields near 246–252, `apply()` 472–484, new methods after `pickLogo` 534)
- Test: `frontend/src/app/components/accounting/account-settings/account-settings.spec.ts` (extend)

**Interfaces:**
- Consumes: Task 6 service + copy, Task 8 icon inputs.
- Produces: `readonly logoHash = signal<string | null>(null)` (set in `apply()` from `loaded.detail?.logo_hash ?? null`, reset to `null` in the navigation reset); `readonly uploadingLogo = signal(false)`; `readonly logoState = computed(() => this.logoHash() ? '已上傳的圖片' : this.form().logo ? `內建：${this.logoLabel()}` : '無')`; `uploadLogo(event: Event): void`; `removeLogo(): void`. Both patch **only** `logoHash` (and `detail` is left as is); they never call `apply()`.

- [ ] **Step 1: Write failing specs**: the 品牌 logo row is replaced by a block with the state line (`已上傳的圖片` when the loaded detail has `logo_hash`, `內建：國泰世華` style label when only a slug, `無` otherwise), buttons `上傳圖片`, `移除圖片` (present only with an uploaded image), `選擇內建 logo` (opens the existing grid), hint text `上傳的圖片優先於內建 logo，上傳後立即生效`; on `accounts/new` the upload button is disabled with hint `先建立帳戶，再上傳 logo`; **dirty-form test (§6.3)**: type a new name → upload → PUT answers with `logo_hash: 'abc'` → the name field still holds the typed value, `儲存` is enabled, `logoState()` is `已上傳的圖片`; same for 移除 (DELETE 204 → `logoHash` null → state falls back to the slug label, name still edited).

- [ ] **Step 2: Run** `--include='**/account-settings.spec.ts'` → FAIL.

- [ ] **Step 3: Implement** per §6.3; the big icon at html:18 and the current button at :65 bind `[accountId]="loadedId()" [logoHash]="logoHash()"`.

- [ ] **Step 4: Run** → PASS; `line-icons.spec.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/components/accounting/account-settings
git commit -m "feat(frontend): account settings Logo block — upload, remove, keeps the dirty form"
```

---

### Task 11: Whole-suite verification, build, OpenSpec tasks

**Files:**
- Modify: `openspec/changes/account-logo-upload/tasks.md` (tick), `services/accounting-service/README.md` or `docs/deploy/` note if the deploy script import check is documented in-repo (add `import PIL` to the documented check only if such a doc exists; otherwise nothing).

- [ ] **Step 1: Backend full suite**

Run: `cd services/accounting-service && OTEL_SDK_DISABLED=true PYTHONPATH=../shared-python-lib:. .venv/bin/python -m pytest -q`
Expected: all pass except the known flaky barrier test (#277) — rerun that one alone if it fails.

- [ ] **Step 2: Frontend full suite + build**

Run: `cd frontend && ./node_modules/.bin/ng test --watch=false && npm run build && git checkout -- angular.json`
Expected: all green; build clean; `line-icons.spec.ts` and `account-logos.spec.ts` gates green.

- [ ] **Step 3: OpenSpec**

Run: `openspec validate account-logo-upload --strict`
Expected: `valid`. Tick the tasks in `tasks.md`.

- [ ] **Step 4: Manual probe against a dev server (fictional account)**

With the API running locally against the test DB (or the dev compose), `curl --data-binary` a Pillow-drawn PNG to a scratch account, GET the image with the right/wrong `v`, confirm headers; delete it. Record the commands (no owner data) in the PR body.

- [ ] **Step 5: Commit and open the PR**

PR title `feat(accounting,frontend): uploaded account logos (PR 1 of account-logo-upload)`; body: summary, design rev 4.1 link, verification, deploy order (API with Alembic `e9b1c3d7a5f2` first; Pillow already in the API venv — confirm with the import check; then SPA release once #279 is resolved; Hermes skill text is a host-side rollout step), follow-ups (PR 2 crop sheet), ending with the generated-with line.

---

## After merge (rollout, outside this plan's tasks)

1. API: release checkout → `alembic upgrade head` → import check adds `import PIL` → restart → `GET /health`.
2. Hermes skill: append the "Account logo" section (§7) to the host-side reference; `curl --data-binary` form.
3. First use: upload the Wise logo (account id known to the owner) through Hermes or `curl`; the SPA shows it after the next SPA release.
4. SPA release per the publisher recipe when #279 is unblocked.
