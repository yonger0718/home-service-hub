# Reward terms check: hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 11 follow-ups of #286 in the notifier's terms check.

**Architecture:** Independent small changes in `services/accounting-service/notify`: the Hermes runner streams through capped pipes; the parser attributes failed fetches by `account_id`; file errors become failure notices; texts and config checks are corrected.

**Tech Stack:** Python 3.13, the notifier package, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-terms-check-hardening-design.md` (base: `docs/superpowers/specs/2026-11-03-reward-terms-check-design.md`).

## Global Constraints

- Public repository: fixtures use fictional ids, banks and quotes.
- No model or config text reaches a message without `_safe_text`; no full URL without `source_ok`.
- Errors and failure lines carry a class, a reason and a path, never output or config values.
- Archive files 0600, directories 0700.
- Test command (from `services/accounting-service`, DB and OTEL variables of the test recipe): `PYTHONPATH=$PWD/../shared-python-lib .venv/bin/python -m pytest tests/notify -q -p no:cacheprovider`.
- Baseline before Task 1: 866 passed.

## Review Focus

- A child that writes far past the cap while the parent waits must not grow the file past the cap and must be killed; a child that fills the pipe must not block forever. Test in Task 1 with a real subprocess.
- A grandchild holding the pipe open after the child exits must not hang the run. Test in Task 1.
- A `failed` object naming one card with another card's URL (same bank) must show under the named card only; a string entry on a domain two cards share must show under neither. Test in Task 2.
- A full disk during `message.md` or the failure file must end in exit code 1 and a one-line notice, never a traceback. Test in Task 3.
- The stricter `hub_base_url` and `bank_domains` checks must accept the shapes in the example TOML and in the existing tests' configs. Test in Task 6.

---

### Task 1: Streaming output cap

**Files:** Modify `notify/hermes_run.py`; Test `tests/notify/test_hermes_run.py`.

**Interfaces:** `run_hermes(...)` signature and the `runner(argv, env, cwd, out_fd, err_fd)` contract unchanged; `_default_runner` unchanged.

- [ ] Tests: `test_overflowing_child_is_killed_and_file_stays_at_the_cap` (real `sh -c` child writing ~5 MB with a 1000-byte cap: `HermesError`, `hermes.out` is cap + marker, the run returns in under 5 s); `test_timed_out_run_leaves_capped_files`; `test_grandchild_holding_the_pipe_does_not_hang`; `test_unopenable_output_file_is_hermes_error`; the existing cap, timeout and stderr tests keep passing with pipe ends.
- [ ] Run: FAIL. Implement (pipes, two reader threads, wait loop polling an `over` event and killing only an unreaped child, join with timeout). Run file, then suite: PASS.
- [ ] Commit `fix(notify): cap the Hermes output while it runs (#286)`.

### Task 2: `fetch_log.failed` by account

**Files:** Modify `notify/terms_parse.py`, `notify/terms_render.py`, `notify/terms_prompt.py`, `notify/terms_prompt.md`; Test `tests/notify/test_terms_parse.py`, `test_terms_render.py`, `test_terms_prompt.py`.

**Interfaces:** `CardResult.failed: tuple[str, ...] = ()` (each `"<url>"` or `"<url> (status)"` text, URL already through `source_ok`); cleaned `fetch_log["failed"]` stays a list of those texts (Ruling during execution: the attribution lives in `CardResult.failed`, so the archived log keeps its shape and the existing parser tests stand; costs nothing, the card field is in `result.json`); `terms_render._block(card, d, head, rows)` reads `card.failed`.

- [ ] Tests: object entry shown under its card only (two cards, one bank); object with a URL off the card's domains dropped; object naming a card outside the run dropped; non-int `account_id` dropped; bad `status` left out; string entry with one owner attributed; string entry with two owners under neither and still in the archived log; non-list `failed` still a parse error; the prompt and contract name the object form.
- [ ] Run: FAIL. Implement. Suite: PASS.
- [ ] Commit `fix(notify): attribute failed fetches to their card (#286)`.

### Task 3: File errors become failure notices

**Files:** Modify `notify/terms.py`, `notify/cli.py`; Test `tests/notify/test_terms_cli.py`, `tests/notify/test_cli.py`.

- [ ] Tests: `OSError` from an archive write gives exit 1, one failure line `TermsArchiveError archive <path>`, no traceback; `report_dir` that cannot be created the same; `OSError` in a `daily` run gives exit 1 and a line with the class and errno name only; a failure file that cannot be written still exits 1.
- [ ] Run: FAIL. Implement. Suite: PASS.
- [ ] Commit `fix(notify): file errors end in a failure notice (#286)`.

### Task 4: Archive parents 0700, `_split_lines`

**Files:** Modify `notify/terms.py` (`_make_archive`), `notify/render.py`; Test `tests/notify/test_terms_cli.py`, `tests/notify/test_render.py`.

- [ ] Tests: missing parents of `report_dir` are created 0700 and an existing parent keeps its mode; `_split_lines` on a line of exactly 2 × limit, on a blank line after a hard cut, and on a `<…>` token across the cut point (cut moves before `<`); a token longer than the limit is cut hard.
- [ ] Run: FAIL (modes, token). Implement; remove the dead branch. Suite: PASS.
- [ ] Commit `fix(notify): 0700 archive parents; never split a link across messages (#286)`.

### Task 5: Digest, demotion reason, names, numbers

**Files:** Modify `notify/terms_parse.py`, `notify/terms_render.py`; Test `tests/notify/test_terms_parse.py`, `test_terms_render.py`.

**Interfaces:** `terms_parse.NO_QUOTE = "無引文"`.

- [ ] Tests: two unverified findings differing only in `system` give one digest, two verified ones give two; an on-domain finding with an empty quote is unverified with reason 「無引文」 and renders it; an off-domain one keeps the default text; unregistered names and `official` names with markdown or `@` are defanged in every line that prints them; `0.2000` / `60000.0000` print as `0.2` / `60000` on both sides, `9%（合計）` is untouched, a non-number field is untouched.
- [ ] Run: FAIL. Implement. Suite: PASS.
- [ ] Commit `fix(notify): terms message texts and the unverified digest (#286)`.

### Task 6: Config hygiene and spec wording

**Files:** Modify `notify/config.py`, `notify/terms_config.py`, `notify/terms_parse.py` (import the shared rule), `docs/superpowers/specs/2026-11-03-reward-terms-check-design.md` (§4, §5 digest line), `services/accounting-service/README.md` if it lists the checks; Test `tests/notify/test_notify_config.py`, `test_terms_config.py`.

**Interfaces:** `config.PRINTABLE_URL: re.Pattern` (moved from `terms_parse._PRINTABLE`).

- [ ] Tests: `hub_base_url` refusals (no scheme, `ftp`, userinfo, a space, `<`, `*`, empty host) each raise `ConfigError("hub_base_url")`; `http://127.0.0.1:18080` and an `https` host pass; `bank_domains` `10.0.0.1` and `example.123` refused, `1800flowers.com` and `example.co` pass; the example TOML loads.
- [ ] Run: FAIL. Implement. Suite: PASS.
- [ ] Check the production configuration loads with this branch's code (pass / fail only).
- [ ] Commit `fix(notify): check hub_base_url and numeric bank domains (#286)`.

### Task 7: Final review fixes

Whole-branch review on `bdf6c944`: no Critical; one Important (a link longer than a message was still cut inside its host: `_cut` moved only for tokens that fit). Fixed test-first, together with these minors, re-graded by effect:

- The overflow kill ran in a reader thread and could fire after the child was reaped (pid reuse). The wait loop now polls and kills only an unreaped child, as the spec said.
- A lone surrogate escape in the model's JSON raised `UnicodeEncodeError` outside the failure classes: now a parse error.
- `_make_archive` tolerates a directory made between its check and its `mkdir`; the untested `chmod` is gone.
- `hub_base_url` hosts: a host name or IPv4 only; an empty port is refused.
- The `OSError` notice says the source file and line on stderr; the errno name only for built-in classes.
- Tests: real child over the pipe buffer, real child timing out, the reader's write-failure branch; the grandchild test no longer depends on a 1 s margin.

Deferred minors: a reader still alive after the drain time is taken as done (theoretical short read); `report_dir` prints raw in the message head (owner config, predates the branch); `_split_lines` can return an empty piece for a block ending in a blank line (unreachable from today's renderers, predates the branch).

### Task 8: PR round

Multica review on `b7c2546a`: PASS-with-minor. The reader thread's `_stop(marker)` redefined `threading.Thread._stop`, which `join()` calls on Python 3.11 and 3.12: every run raised `TypeError` there (reproduced with both interpreters). The production venv is 3.13, where the name is free, so nothing was broken in use. Renamed to `_mark_over`; a test refuses any reader attribute that a `Thread` has on those versions.
