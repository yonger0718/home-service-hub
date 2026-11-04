# Reward oracle (read-only)

Scores the reward engine against historical MOZE reward rows: amount (uncapped percent rules only, within 1 dollar; other uncapped methods are counted apart),
capped / shared window totals, and posting date (exact day, then same statement period). Flagged and refunded purchases and
placeholder rules are excluded and counted.

Run (one read-only transaction, rolled back; no `-I`/`-E`, which drop PYTHONPATH; no `.env` copying):

    PYTHONPATH=<tree>/services/accounting-service python scripts/reward_oracle.py

Output: `~/reports/home-hub-reward-oracle-<UTC ts>/report.md` (directory 700, file 600), counts and keys only.

Bar: amount agreement of at least 95 % on the uncapped percent pairs. The posting section is read per card and
gates each card's posting rules; it is not part of the amount bar.
