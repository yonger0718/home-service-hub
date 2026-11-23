# Reward terms check: dismiss list Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner mark a terms-check item as accepted in the notifier config so it stops repeating, without hiding a later change.

**Architecture:** The card registry gains `dismiss` entries. A new pure module filters each parsed card (verified findings and the announced block) before the digest and the show rules run; the run archives what it hid and the message reports counts and stale entries. Hermes and its prompt are untouched.

**Tech Stack:** Python 3.13, the notifier package `services/accounting-service/notify`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-terms-dismiss-list-design.md` (extends `docs/superpowers/specs/2026-11-03-reward-terms-check-design.md`).

## Global Constraints

- Public repository: fixtures, docs and examples use fictional ids (101, 162, …), banks and quotes.
- Loader errors name the key only, never a value.
- Config text printed in a message passes `terms_render._safe_text`.
- `result.json` stays the full joined result; archive files are 0600.
- Test command (from `services/accounting-service`, with the DB and OTEL variables of the test recipe): `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/notify -q -p no:cacheprovider`.
- Baseline before Task 1: 596 passed.

## Review Focus

- A site value that is not the leading token the pin expects (`約 9%`, `2026/07/01`, a range such as `1.5-3%`) must show the finding, never hide it; an empty site value matches only a `none` pin. Test in Task 2.
- A dismissal on its last day (`today == until`) applies; the day after it is stale and hides nothing. Test in Task 2.
- A card whose findings are all dismissed must not print 「找不到官網來源」 (the `changed` + no findings rule of `decide`). Test in Task 2 (status) and Task 4 (message).
- A `--dry-run` must save no state, and a `--send` must save the digest of the filtered card. Test in Task 4.
- A year-end run must not call a dismissal stale for a card that is not in the run. Test in Task 4.

---

### Task 1: Config

**Files:**
- Modify: `services/accounting-service/notify/terms_config.py`
- Test: `services/accounting-service/tests/notify/test_terms_config.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Dismissal:
      field: str                      # a compared rule field, "missing_rule" or "next_terms"
      rule: int | None = None
      value: str | None = None        # normalised pin; "none" = the bank gives no value
      contains: tuple[str, ...] = ()
      until: date | None = None
      note: str = ""
  ```
  `CardEntry.dismiss: tuple[Dismissal, ...] = ()`.
  `DISMISS_RULE_FIELDS`, `DATE_FIELDS = ("starts_on", "ends_on")`, `NUMBER_FIELDS = ("rate", "fixed_amount", "total_cap")`, `TOKEN_FIELDS = ("method", "window")`.
  Stored pin forms: dates ISO; numbers `str(Decimal.normalize())` via `format(d, "f")`; `is_basic` `"true"` / `"false"`; tokens as given; `"none"`.

- [ ] **Step 1:** Tests for each key rule of spec §1: valid entries of the four shapes load with normalised pins (`value = 9.0` and `"9.0000"` both store `"9"`; `value = true` stores `"true"`); refusals (unknown `field`, unknown key, missing / non-positive / bool `rule`, `rule` on `missing_rule` or `next_terms`, `value` on `name` / `description` / `missing_rule`, bad date, bad number, bad token, missing `value` on `next_terms`, `"none"` on `is_basic` / `method` / `next_terms`, empty or 1-character `contains`, `contains` on a rule field, missing `until` on `missing_rule`, bad `until`, `note` over 200 or not a string, `dismiss` not a list of tables, `dismiss` on a signal card) each raise `ConfigError` whose text is the key path (`terms.cards.101.dismiss[0].value`). A card without `dismiss` loads with `()`.
- [ ] **Step 2:** Run: FAIL (no `Dismissal`).
- [ ] **Step 3:** Implement `_dismissal(raw, path) -> Dismissal` and call it from `_card`.
- [ ] **Step 4:** Run the file, then the suite: PASS.
- [ ] **Step 5:** Commit `feat(notify): dismiss entries in the terms card registry (#287)`.

### Task 2: Matching

**Files:**
- Create: `services/accounting-service/notify/terms_dismiss.py`
- Test: `services/accounting-service/tests/notify/test_terms_dismiss.py`

**Interfaces:**
- Consumes: `Dismissal`, `CardEntry` (Task 1); `CardResult`, `Finding` from `terms_parse`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class Applied:
      card: CardResult                               # what is shown and digested
      hidden: tuple[Finding, ...] = ()
      hidden_next_terms: bool = False
      stale: tuple[tuple[Dismissal, str], ...] = ()  # reason: "expired" | "rule"

      @property
      def hidden_count(self) -> int: ...             # len(hidden) + hidden_next_terms

  def site_key(field: str, site: str | None) -> str | None
  def apply_dismissals(card: CardResult, entry: CardEntry, live_rule_ids: frozenset[int], today: date) -> Applied
  ```

- [ ] **Step 1:** Tests from spec "Tests" (matching, status recompute, stale), including the Review Focus cases.
- [ ] **Step 2:** Run: FAIL (no module).
- [ ] **Step 3:** Implement. `expired` wins over `rule`. A stale dismissal is skipped before matching. With no `dismiss` entries return `Applied(card)` with the same object.
- [ ] **Step 4:** Run the file, then the suite: PASS.
- [ ] **Step 5:** Commit `feat(notify): match terms findings against the dismiss list (#287)`.

### Task 3: Digest by period-end date

**Files:**
- Modify: `services/accounting-service/notify/terms_parse.py` (`digest`)
- Test: `services/accounting-service/tests/notify/test_terms_parse.py`

- [ ] **Step 1:** Test `test_digest_ignores_period_end_source_and_quote`: same date with another `source` and `quote` gives the same digest; another date does not.
- [ ] **Step 2:** Run: FAIL (digests differ).
- [ ] **Step 3:** `"current_period_end": None if pe is None else pe.date`.
- [ ] **Step 4:** Run the suite; fix any test that pinned the old shape: PASS.
- [ ] **Step 5:** Commit `fix(notify): terms digest takes the period end's date only (#287)`.

### Task 4: Run, archive and message

**Files:**
- Modify: `services/accounting-service/notify/terms.py`, `services/accounting-service/notify/terms_render.py`
- Test: `services/accounting-service/tests/notify/test_terms_cli.py`, `services/accounting-service/tests/notify/test_terms_render.py`

**Interfaces:**
- Consumes: `apply_dismissals`, `Applied` (Task 2).
- Produces: `render_terms(inp, result, decisions, cfg, archive, applied: dict[int, Applied] | None = None)`; `terms.py` writes `dismissed.json`:
  ```json
  {"101": {"hidden": [<finding dict>], "hidden_next_terms": false,
           "stale": [{"field": "starts_on", "rule": 162, "value": null, "contains": [], "reason": "rule"}]}}
  ```
  (only cards with something; `{}` otherwise).

- [ ] **Step 1:** Render tests: the 「已忽略」 line (count per card, announced block counts one), the 「忽略清單失效」 line with the three label forms and both reasons, `contains` text defanged, the single-line form with and without the suffix, stale alone forces the long form.
- [ ] **Step 2:** CLI tests: a dismissed finding is absent from `message.md`, present in `result.json` and in `dismissed.json` (0600); a fully dismissed card prints no 「找不到官網來源」; `--send` saves the digest of the filtered card and a second run with the same output folds the card into 「無變動」; `--dry-run` saves nothing; a year-end run reports nothing for a card outside the window.
- [ ] **Step 3:** Run: FAIL.
- [ ] **Step 4:** Implement. In `run_terms`, after `join_rules` and the `result.json` write: apply per card with `live_rule_ids` from the frozen input rows, build the filtered `TermsResult`, digest and decide on it, write `dismissed.json`, pass `applied` to `render_terms`.
- [ ] **Step 5:** Run the suite: PASS.
- [ ] **Step 6:** Commit `feat(notify): terms run hides dismissed items and reports them (#287)`.

### Task 5: Docs

**Files:**
- Modify: `services/accounting-service/deploy/notify/homehub-notify.example.toml`, `services/accounting-service/README.md`, `docs/superpowers/specs/2026-11-03-reward-terms-check-design.md` (§1 sample, §5 digest and message lines, §6 archive file list: one pointer line each to the new spec)

- [ ] **Step 1:** Example TOML: the four entry shapes, commented, on the fictional card 101. A test loads the example file through `load_terms` with the comments stripped from the dismiss block (or the block left active) so the documented shape cannot drift.
- [ ] **Step 2:** README: what a dismissal is, the keys, the upkeep recipe (read `result.json`, add the entry, `--dry-run`), the known limits.
- [ ] **Step 3:** Run the suite: PASS. Commit `docs(notify): the terms dismiss list (#287)`.

### Task 6: Pin an announced-terms entry to its rates and caps

Added during execution (a reviewer's note: the period end alone hides a revision within the same period).

**Files:**
- Modify: `services/accounting-service/notify/terms_config.py`, `services/accounting-service/notify/terms_dismiss.py`, `services/accounting-service/notify/terms.py`
- Test: `services/accounting-service/tests/notify/test_terms_config.py`, `test_terms_dismiss.py`, `test_terms_cli.py`

**Interfaces:**
- Produces: `Dismissal.rates: tuple[tuple[str | None, str | None], ...] | None`; `terms_config.sort_rates(pairs)`; `terms_dismiss.terms_rates(nt: NextTerms)`; `Applied.hidden_rates`; `dismissed.json` key `hidden_next_terms_rates` (`"rate/cap"` strings).

- [ ] **Step 1:** Tests: the loader (normalised, sorted, refusals name `rates`), matching (same numbers in another order and spelling, a moved rate, a moved cap, a row gone, a row added, repeated rows, no pin), the run (block back when the numbers moved, hidden when they match).
- [ ] **Step 2:** Run: FAIL. Implement. Run the suite: PASS.
- [ ] **Step 3:** Commit `feat(notify): pin an announced-terms dismissal to its rates and caps (#287)`.

### Task 7: Final review fixes

Whole-branch review on `f9a0d150`: one Critical (a number pin matched the first digits of a range or fraction), one Important (hiding an item silenced the 「找不到官網來源」 line of a `changed` card without verified findings, or of a `not_found` card), minors (ISO dates stricter than `date.fromisoformat`, signed `rates` sides, `until` and `rates` in the stale records, the token regex stopping in front of `_`). Each fixed test-first in one pass.

### Task 8: Multica PR round fixes

AGENT-572 on `8b415a41`, CHANGES-REQUESTED, one P1: a separator outside ASCII (`1.5～3%`, `1.5－3%`, `1.5／2`, `9，000`) passed the lookahead, so a number pin matched the first number of a range. `site_key` now reads the text NFKC-normalised and accepts one value followed only by the end of the text or an opening bracket (a whitelist instead of a list of separators). Tests: the ranges through `parse_result → join_rules → apply_dismissals`, a matrix of every pinnable field with and without a pin, a state file from before the digest change. Also: a `rates` pin whose rows hold no number is refused (a miles card's announced rows have neither rate nor cap).

### Task 9: Multica re-check fixes

AGENT-574 on `9ed24edf`, CHANGES-REQUESTED: P1, NFKC fused a superscript or enclosed digit into the number (`1.5²%` read as 1.52); P2, `<` counted as a remark opener (`1.5 < 3%` matched a pin on 1.5). `site_key` now folds only the full-width forms, one for one, and `<` left the opener set. Test gap: the state-file test now starts from a digest in the shape `main` saved and asserts the first and the second run's messages. The owner then allowed one extra re-check on `97ec9cfd`.

### Task 10: An end date for text-field entries

A `name` or `description` entry cannot be pinned, so it hid every later wording difference of that rule for good (a bank changing its exclusions is usually reported as a description difference). The loader now requires `until` on these entries, as it does for `missing_rule`.
