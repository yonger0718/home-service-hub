# Reward terms check: dismiss list — design

Issue #287 (its comment of 2026-11-03). Extends
`docs/superpowers/specs/2026-11-03-reward-terms-check-design.md` (the "base spec").

## Intent

The reward terms check repeats every verified finding and every announced block on every run
until the system side changes (base spec §5). Some of them the owner has looked at and
decided to leave: the bank prints only the current period's start date, a promotion the owner
is not eligible for, a wording difference in a description, announced terms that the rules
already cover. During the year-end window the check runs weekly, so the same lines come back
every Monday and bury what is new.

The owner wants to mark such an item as accepted, once, and stop seeing it, without losing
the notice when the bank later changes it.

## Goals

- An owner-maintained list of accepted items per card, in the notifier TOML.
- The notifier matches the list itself. Hermes never sees it and its prompt does not change.
- A dismissal is as narrow as the data allows: it can be pinned to the value the bank
  publishes, it can carry an end date, and it lapses with the rule it names.
- Nothing is dropped silently: hidden items stay in the run's archive and the message says
  how many were hidden.
- A card whose items are all dismissed is quiet, and stays quiet while nothing changes.

## Non-goals

- No UI and no API. The list is edited in the config file (by the owner or an agent acting
  for the owner).
- Signal-tier text, the 「來源文件過長」 line and unverified items are not dismissable. The
  first two describe coverage, not a difference; the third already shows once per digest.
- No fuzzy or model-side matching.

## Anti-goals

- A dismissal that keeps hiding an item after the bank changed the value it was pinned to.
- A hidden item that leaves no trace in the archive.

## Approaches

1. **List in the config, matched by the notifier (chosen by the owner, 2026-11-23).**
   Deterministic, testable, and the hidden items stay on disk. Costs a config edit per
   dismissal, and free-text fields can only be dismissed whole.
2. List passed to Hermes as known exceptions. Smallest change, but the model may skip more
   or less than the list says and a skipped item leaves no trace.
3. No list: report each finding once (digest only). No upkeep, but a real difference the
   owner has not handled goes quiet after one message. That is the case base spec §5
   refuses.

## Design

### 1. Config

A card table may hold `dismiss` entries (TOML array of tables):

```toml
[[terms.cards."101".dismiss]]      # a rule field, pinned to the value the bank publishes
field = "starts_on"
rule = 162
value = "2026-07-01"
note = "the bank prints the current period only"

[[terms.cards."101".dismiss]]      # a rule field, any value
field = "description"
rule = 163

[[terms.cards."101".dismiss]]      # a programme the owner does not want as a rule
field = "missing_rule"
contains = ["LINE Pay", "10%"]
until = "2026-12-31"

[[terms.cards."101".dismiss]]      # announced terms the rules already cover
field = "next_terms"
value = "2027-03-31"
rates = ["0.88/none", "3/888", "1/200", "1/200"]
```

Keys:

- `field` (required): a compared rule field (`name`, `method`, `rate`, `fixed_amount`,
  `is_basic`, `total_cap`, `window`, `starts_on`, `ends_on`, `description`), or
  `missing_rule`, or `next_terms`.
- `rule` (int > 0): required for a rule field, refused otherwise.
- `value`: the pin.
  - Rule fields: optional. Absent = any value of that field of that rule is dismissed.
    Allowed for `starts_on` / `ends_on` (ISO date), `rate` / `fixed_amount` / `total_cap`
    (a number, or a numeric string), `is_basic` (a bool, or `"true"` / `"false"`),
    `method` / `window` (a lower-case token). The string `"none"` pins "the bank gives no
    value" for the date and number fields. Refused for `name` and `description`: free text
    has no stable value to pin.
  - `next_terms`: required, the ISO end date of the announced period.
  - `missing_rule`: refused.
- `contains` (non-empty list of strings, each at least 2 characters): required for
  `missing_rule`, refused otherwise.
- `rates` (non-empty list of `"rate/cap"` strings, `none` for a missing side): `next_terms`
  only, optional. One string per announced row; order does not matter, repeated rows count.
  With it the entry applies only while the announced rows carry exactly these numbers. A
  list in which no row has a rate or a cap is refused: it would pin nothing but the number
  of rows.
- `until` (ISO date): the last day the dismissal applies. Required for `missing_rule`,
  optional otherwise.
- `note` (string, at most 200 characters): the owner's reason. Kept in the config only.

A `dismiss` list on a signal-tier card is refused (it has no findings). Unknown keys are
refused. Loader errors name the key only (`terms.cards.101.dismiss[2].value`).

### 2. Matching

Runs after `join_rules` (the system side is the frozen input) and before the digest. Only
**verified** findings and the announced block are candidates.

A dismissal is **active** when `until` is absent or `today <= until`; for `next_terms` it
also needs `today <= value` (the announced period has not ended).

- Rule field: the finding has the same `field` and `rule_id`, and either the dismissal has
  no `value` or the finding's site value normalises to it.
- `missing_rule`: the finding's field is `missing_rule` and every `contains` string occurs in
  the finding's site text plus quote (both sides NFKC-normalised and case-folded).
- `next_terms`: the card's `next_terms` is announced, its period end equals `value`, and,
  when the entry has `rates`, the block's rows give the same (rate, cap) pairs as a multiset
  (numbers normalised).

The announced rows' names and notes are the model's wording and differ on every run; their
rates and caps were the same on every production run looked at (4 runs of one card). So the
numbers are what an entry can be pinned to. A bank that revises the announced terms within
the same period changes a rate or a cap, and the block shows again.

Site values are the model's text, so the pin compares the **leading token** only:

| field | value | examples that match the pin |
|---|---|---|
| `starts_on`, `ends_on` | `YYYY-MM-DD`, a valid date | `2026-07-01`, `2026-07-01（本期活動）` |
| `rate`, `fixed_amount`, `total_cap` | one decimal number (commas only as thousands separators), with an optional `%`, 元, 點 or 哩, compared as a number | `9`, `9.0000`, `9%`, `60,000`, `500 點` |
| `is_basic` | `true` / `false`, any case | `false（基本為每滿 25 元）` |
| `method`, `window` | `[a-z_]+` | `calendar_month` |

The text is read with its full-width forms folded to ASCII one for one (digits, commas,
percent signs, brackets) and nothing else: a general compatibility fold would make numbers
the text does not spell (`5¹` as 51). The value must be followed by the end of the text or
by an opening bracket, i.e. a remark (`<` is a comparison, not a bracket). Anything else after it means the text is not that one value:
a range or a fraction in any script (`1.5-3%`, `1.5～3%`, `1.5 - 3`, `1.5／2`, `1.5至3`), a
list, a second value, a comparison (`1.5 < 3%`), a footnote mark or another digit form
(`5¹`, `①`, `9½`), loose words (`9% 起`). Such a site value has no key, matches no pin, and
the finding is shown. This is a whitelist on purpose: a new way of writing a range is
shown, not hidden.

For the date and number fields a missing or blank site value normalises to `none`, and so
does one the model spells out (`null`, `none`, 「無」 or 「無上限」, alone or before a remark,
as in `null（上限僅適用加碼哩）`).

### 3. Effect on the card

The matched findings are removed from the card and a matched announced block is dropped
(`next_terms` becomes absent). When anything was removed the card's status is recomputed:
`changed` if findings remain, else `announced` if an announced block remains, else
`unchanged`. Two statuses are never recomputed, because they are the every-run
「找不到官網來源」 line of base spec §5 and an accepted item must not silence it: `not_found`,
and `changed` with no verified finding before the filter (hiding that card's announced block
leaves it `changed`). A card nothing was removed from is left exactly as parsed.

The digest and the show rules then work on that card. So a card whose items are all
dismissed renders like an unchanged one: the 「無差異，條款期間至 …」 line when its digest is
new, otherwise it joins the 「無變動」 line.

**Digest change.** The digest took the period end's `source` and `quote`. The model picks a
different sentence or URL form on most runs (seen on 4 of 5 production runs for one card), so
an unchanged card looked new almost every run. The digest now takes the period end's **date**
only. The first run after the deploy sees new digests for every card and re-shows the
once-only items once.

### 4. Stale dismissals

A dismissal that can no longer apply is reported so the list does not rot:

- **到期**: `until` is past (or, for `next_terms`, the announced period has ended).
- **規則已不在**: a rule-field dismissal whose `rule` is not among the card's live rules of
  this run.

A stale dismissal hides nothing. Only cards in the run are checked (the year-end run skips
cards outside the year-end window).

### 5. Message and archive

After the 「未登錄」 line:

- `已忽略 5 項：<card> 3、<card> 2` when anything was hidden (the announced block counts
  as one).
- `忽略清單失效：<card> 規則 162 開始日（到期）、<card> 缺規則「LINE Pay 10%」（到期）、<card>
  下期條款 2027-03-31（到期）` when any dismissal is stale. Stale dismissals alone make the
  message the long form.

The single-line form (`📋 回饋條款查核 <date>：M 張卡無變動`) gains `（已忽略 N 項）` when
N > 0.

Config text printed in these lines (`contains` strings) passes the same defanging as model
text.

Archive: `result.json` stays the full joined result, hidden items included. A new
`dismissed.json` (0600) lists, per card that has any, the hidden findings, whether the
announced block was hidden (with its rows as `rates` strings, ready to copy into the entry),
and the stale dismissals.

### 6. Upkeep

To dismiss an item: read the finding in the latest archive's `result.json` (`rule_id`,
`field`, `site`), add the entry, and check it with a `--dry-run`. The service README carries
this recipe.

## Known limits

- A dismissal without `value` hides every later change of that field of that rule. Use the
  pin wherever the field allows one.
- `name` and `description` can only be dismissed whole: a later change in the bank's
  conditions that the model reports as a description difference is hidden too.
- A `missing_rule` dismissal hides any programme whose text holds all its strings, until
  `until`.
- A `next_terms` entry without `rates` looks at the period end only: a bank that revises the
  announced rates or caps within the same period stays hidden until that period starts (the
  revised terms are then the current ones and differ from the rules as ordinary findings).
  With `rates` the revision shows, unless it changes conditions only and no number.
- A `rates` pin shows the block again when the model reports the rows differently (a row
  merged, split or left without its rate). That is noise in the safe direction.
- The model can report the same difference under another field or rule on another run; that
  finding is not matched and shows.
- A pin ignores a bracketed remark after the value: `9%（2027 起 5%）` matches a pin on 9.
- Announced rows of a miles card carry no rate and no cap (「每 15 元 1 哩」 is text), so
  there is nothing for `rates` to pin: a `next_terms` entry for such a card looks at the
  period end only. Leave that announcement visible, or give the entry a short `until`.
- A pin is the bank's value, not the pair of values. If the rule's own value is later edited
  to something else while the bank still publishes the pinned value, the finding stays
  hidden.
- The digest still takes the announced block's source and quote, so a card with a visible
  announced block looks new on most runs. That block shows on every run anyway.
- A pinned date dismissal for a bank that prints only the current period comes back when the
  period rolls over (the date changes). That is the intended notice.

## Tests

- Loader: every key rule above, each refusal names its key; a card without `dismiss` loads
  as before.
- Matching: each field kind with and without a pin; the value forms, with ranges and
  fractions in ASCII and non-ASCII separators through the parser; `none`; a
  non-normalisable site value is shown; `missing_rule` with all / some strings, NFKC and
  case; `until` on the last day and the day after; `next_terms` with the same and another
  period end, after the period ended, and with a `rates` pin (same numbers in another order
  and spelling, a moved rate, a moved cap, a row gone, a row added, repeated rows); unverified findings untouched.
- Status recompute for each combination; an untouched card keeps a `changed` status with no
  findings; a `not_found` card and a `changed` card without verified findings keep their
  status when something is hidden.
- Stale: expired, rule gone, and a live one is not stale.
- Digest: a different period-end quote or source gives the same digest; a different date
  does not.
- Run: `dismissed.json` content and mode; `result.json` still holds the hidden finding; a
  fully dismissed card is saved with the digest of the filtered card; the tail lines; the
  single-line form.

## Rollout

Notifier only (no API, no migration, no SPA): a new release checkout, `current` moved, no
unit change. Then the owner's first dismissals go into `~/.config/homehub-notify.toml`, one
`--dry-run` to read the result.
