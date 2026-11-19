# Inbox: name the settlement entry an unmatched bank line may be part of (#392)

Status: short design (small change). The owner approved the behaviour on 2026-11-19; the details marked "call" are the author's.

## Why

After #389 the policy and owner record rules leave a bank line alone when it may be one transfer of a repayment the owner recorded as one larger settlement entry. The owner then has to answer the line, but the inbox does not say why it was left:

- `GET /reconciliation/inbox` carries no twin information. Only the case detail names the entry (`owner_twins`).
- The SPA card reads 銀行有、你沒記 and offers 記為. Tapping it books the duplicate #389 stopped, by hand.
- Lines of the same amount merge into one card (`group_key`), so one tap can book several duplicates, and 以後都這樣 can turn the answer into a rule.

## Goals

- The inbox item of such a line names the settlement entry (title, amount, date).
- The card says so in place of the plain question text.
- Such an item is its own card and offers no 以後都這樣.
- The buttons stay. The owner may still record or dismiss the line on purpose.

## Non-goals

- No action that splits the receipt per transfer. The owner (or the assistant) re-records it.
- No change to the case detail, the guard of #389, or any other question.
- No server-side refusal of a `learn` sent for such a case by another client (call: the SPA is the only client that offers it).

## Approaches

1. **A `part_of` list on the inbox item, worded by the SPA** (picked). The server already knows the shape (`stand_ins._partial_settlement_twins`); the SPA only words it.
2. **Put the text into the item's `note`.** Rejected: `note` is shown for the `other` question only, and a server-worded sentence cannot format money in the account's currency the way the SPA does.
3. **List the entry in `candidates`.** Rejected: candidates feed the 對到記錄 sheet, and matching one transfer to the whole receipt is wrong.

## Design

### API

- `InboxItem` gains `part_of: list[InboxEntry] = []` (same shape as `entry`: `id`, `date`, `title`, `amount`, `account_id`).
- It is filled for a `bank_only` question on a bank statement: the entries `stand_ins._partial_settlement_twins` finds for the line, ordered by id. Every other item has `[]`.
- When it is not empty, the item's `group_key` and `rule_hint` are `null` (call): the card stands alone and offers no rule.
- Cost: one claimed-entry snapshot per inbox build and one `owner_population` read per statement that has a `bank_only` bank line, both kept in the existing per-build cache. New public helper `stand_ins.settlement_part_twins(db, statement, line, population, rules) -> list[int]` wraps the private predicate for this caller.

### SPA

- `InboxItem.part_of?: InboxEntry[]` in `inbox.model.ts` (optional: an older API omits it).
- `questionText` for `bank_only`: with a non-empty `part_of`, the text is `可能是「{title} {amount}」的一部分 — 先把那筆改成分筆，別直接記為收入` for an inflow and `…別直接記為支出` for an outflow, using the first entry and `formatMoney` with the account currency; with more than one entry, `等 {n} 筆` follows the amount.
- Nothing else changes: the card stands alone and has no learn row because the server sent no `group_key` and no `rule_hint`.

## Tests

- API (`tests/integration/test_inbox_api.py`): two equal deposit lines beside one larger unclaimed settlement receipt give two `bank_only` items, each with `part_of == [receipt]`, `group_key is None`, `rule_hint is None`, and `record` still among the actions; `counts.ask == 2`. Without the receipt the same lines merge as before (one group key, a rule hint, `part_of == []`). A card statement's `bank_only` item has `part_of == []`.
- SPA (`inbox-view.spec.ts`): `questionText` words the inflow hint, the outflow hint and the several-entries form; a `bank_only` item without `part_of` (field absent) keeps 銀行有、你沒記.

## Rollout

- After #389 (this branch is stacked on it). API deploy, then an SPA build and publish.
- The assistant skill gets one line: for a deposit question, read `part_of` before recording.

## Left to the builder

- The exact cache keys.
