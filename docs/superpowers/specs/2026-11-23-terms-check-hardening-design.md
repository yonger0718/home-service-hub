# Reward terms check: hardening (#286) — design

Issue #286 lists the follow-ups of the reward terms check review (#285), plus one comment from
the first production dry-run. None changes what the check is for. This document fixes the
decision for each item. Base spec:
`docs/superpowers/specs/2026-11-03-reward-terms-check-design.md`.

## Intent

Close the known holes of the terms check before they bite: a runaway model process that fills
the disk, a failure line shown under the wrong card, a traceback instead of a failure notice,
and several small wrong or misleading texts.

## Goals

- Each of the 11 items below is fixed with a test that fails before the fix.
- The model contract changes in one place only (`fetch_log.failed`), and the parser still
  accepts the old form.

## Non-goals

- No change to which findings are reported, to the dismiss list, or to the timers.
- No API, migration or SPA change. Notifier only.

## Anti-goals

- A fix that prints model or config text unescaped, or a URL that has not passed the source
  rule.
- A stricter config check that refuses the configuration in production today.

## Approaches

The items are independent, so the choice is per item (below). For the two with a real
alternative:

- **Output cap.** (a) Pipes drained by reader threads that stop writing at the cap (chosen:
  bounds the files while the process runs, and a timed-out run's files are bounded too).
  (b) `RLIMIT_FSIZE` on the child: one line, but it also limits Hermes's own state database,
  so a normal run could fail. (c) A watchdog that polls the file sizes: bounded only to the
  poll interval, and the file is still over the cap when it fires.
- **Failed-fetch attribution.** (a) `account_id` on each `failed` entry, as `truncated` has
  (chosen: the model knows which card it was fetching for). (b) Keep domain matching and show
  a shared-domain failure under no card: loses the hint for every bank with two registered
  cards. (c) Match by URL path heuristics: guesswork.

## Design

### 1. Streaming output cap (`hermes_run`)

`run_hermes` gives the child the write ends of two pipes. Two reader threads copy each pipe
into `hermes.out` / `hermes.err` and stop writing at `max_output_bytes`; the first byte over
the cap appends the truncated marker, marks the stream as over, and the rest is read and
dropped (so the child never blocks on a full pipe). The wait loop notices an over stream
within a fraction of a second, kills the process group and raises `HermesError`. A timeout
kills the group as before; the files are already bounded. After the process ends the readers
are joined with a short timeout, so a grandchild that keeps a pipe open cannot hang the run.

The injected `runner(argv, env, cwd, out_fd, err_fd)` interface does not change: the fds are
now pipe ends instead of files.

### 2. `fetch_log.failed` attribution

Contract: `"failed": [{"account_id": 101, "url": "https://… ", "status": "403"}]`. The prompt
text and the contract sample say so.

Parser, per entry:

- An object: `account_id` must be an int (else the entry is dropped). A card of the run whose
  source rule the URL passes gets the entry; `status` is kept when it is 3 digits or up to 40
  characters of letters, digits, space, `_`, `-`, else left out. An entry whose URL fails that
  card's source rule, or that names a card outside the run, is dropped from the message and
  from the archived log (as today for a failing URL).
- A string (the old form, `"https://… (403)"`): attributed by domain only when exactly one
  card of the run owns the URL's domain. With several, it is shown under none (my call: a
  hint under the wrong card is worse than no hint; the entry stays in the archived log).

The cleaned log keeps each surviving entry as the text `url` or `url (status)`, as before.
The attribution lives in the card: `CardResult` gains `failed: tuple[str, ...]`, which is in
`result.json`. The renderer prints the card's own entries and no longer matches failures by
domain (it still applies the source rule before printing a link).

### 3. OSError on archive writes

- `run_terms`: an `OSError` while creating the archive directory or writing an archive file
  becomes `TermsArchiveError` (reason `archive`, the path only). The class now means "the
  run's archive could not be created or written"; the timestamp collision is one case.
- `run_hermes`: an `OSError` opening `hermes.out` / `hermes.err` or the pipes becomes
  `HermesError`.
- `cli.FAILURES` gains `OSError` as the backstop for the remaining file work (state files of
  `daily`, `weekly` and `terms`): the failure line carries the exception class and no path or
  message text. `_fail` writing the failure file is guarded, so a full disk cannot turn the
  notice into a traceback.

### 4. Parent directories of `report_dir`

Every directory the run creates on the way to `report_dir` is created 0700 (not only the
leaf). Existing directories are left as they are.

### 5. `render._split_lines`

The `if not line and out and not cur` branch has no effect (the assignment below it gives the
same result) and its comment describes a case that cannot happen. It is removed. A hard cut no
longer falls inside a `<…>` token: when the cut point is inside one that fits in a message,
the cut moves to just before its `<`. So a URL is never sent in two halves (the first half
would be a URL of another host).

### 6. Digest of unverified findings

An unverified finding's `system` is the model's claim. It is never shown, so it leaves the
digest (`unverified` entries are digested without `system`). Verified findings keep it: there
`system` is the value joined from the frozen input, and its change is news. The first run
after the deploy re-shows cards with unverified items once.

### 7. Reason of a demoted finding

A finding whose source passes the source rule but whose quote is empty is demoted with the
reason 「無引文」 instead of the default 「來源不在銀行網域」.

### 8. Names in the message

Unregistered account names and the cards' `official` names (block head, 「無變動」,
「已忽略」, 「忽略清單失效」) pass `_safe_text`.

### 9. Base spec §4 wording

"reported as `· 下期條款：來源未驗證` with its URL" becomes "with its host, and only when the
block says `announced`", which is what the code does.

### 10. Config hygiene

- `hub_base_url` must be an `http` / `https` URL with a host, made of the printable-URL
  characters only (the rule every printed source URL passes), without userinfo. It is printed
  in every message as part of the settings link.
- A `bank_domains` entry whose last label is all digits is refused (`10.0.0.1`, `example.123`).

Both checks are verified against the production configuration before the release (pass /
fail only).

### 11. Numbers in the message

For `rate`, `fixed_amount` and `total_cap` findings, a side (官網 or 系統) that is a plain
decimal prints without trailing zeros (`0.2000` as `0.2`, `60000.0000` as `60000`). Any other
text prints as it is.

## Known limits

- The output cap is enforced by the reader, so a file can exceed the cap by nothing, but the
  child may have written up to one pipe buffer more before it is killed; that data is dropped.
- A model that keeps the old string form for `failed` loses the hint for banks with two
  registered cards.
- A token longer than a whole message is still cut hard.

## Tests

One or more per item, named in the plan. Each is watched failing before the fix.

## Rollout

Notifier only: a new release checkout, `current` moved, no unit change. The Hermes prompt
changes (item 2), so the first run is read against the archive (`fetch_log` shape). Digest
shape change (item 6): cards with unverified items re-show once.
