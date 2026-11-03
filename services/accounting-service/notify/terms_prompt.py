"""Fill the reward-terms prompt template (notify/terms_prompt.md) from a frozen TermsInput."""
from __future__ import annotations

import json
import re
from importlib.resources import files

from notify.terms_input import RULE_FIELDS, CardInput, TermsInput

# Spec section 4 sample, verbatim.
CONTRACT_JSON = """{
  "cards": [
    {
      "account_id": 101,
      "status": "unchanged | changed | announced | not_found | signal",
      "current_period_end": {"date": "2027-03-31", "source": "https://…", "quote": "…"},
      "findings": [
        {"rule_id": 162, "field": "ends_on", "site": "2027-03-31", "system": "2026-12-31",
         "source": "https://…", "quote": "…"},
        {"rule_id": null, "field": "missing_rule", "site": "精選國家餐飲 +1% cap 200", "system": null,
         "source": "https://…", "quote": "…"}
      ],
      "next_terms": {"announced": true, "period": ["2027-01-01", "2027-03-31"],
                     "rules": [{"name": "…", "rate": "1.5", "cap": null, "notes": "…"}],
                     "source": "https://…", "quote": "…"},
      "sources": ["https://…"],
      "notes": "free text, ≤ 500 chars"
    }
  ],
  "fetch_log": {"tool_calls": 15, "failed": ["https://… (403)"],
                "truncated": [{"account_id": 101, "url": "https://…", "read": "first ~40k chars of ~130k"}]}
}"""

_COMPARE_FIELDS = ", ".join(f"`{f}`" for f in RULE_FIELDS if f != "id")
_PLACEHOLDER = re.compile(r"\{(today|cards|contract)\}")


def _compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _precise_block(ci: CardInput) -> str:
    rules = "\n".join(_compact({k: r.get(k) for k in RULE_FIELDS}) for r in ci.rules)
    return (
        "Tier: precise. Run both stages, then compare each live rule below against the "
        f"official terms on exactly these fields: {_COMPARE_FIELDS}. `rate` is a percent "
        "(0 < rate <= 100, 4 decimal places, never a fraction). Report each difference as a "
        "finding with the site value and the system value. Posting and shared-cap fields "
        "(posting, delay_days, post_month_offset, post_day, roundings, shared_cap_id) are out "
        "of scope and are not sent. A programme on the official page with no matching live "
        "rule is a `missing_rule` finding (`rule_id` null).\n"
        + ("Live rules (one JSON object per line):\n" + rules if ci.rules else
           "Live rules: none. This card has zero live rules, so every programme on the "
           "official page is a `missing_rule` finding; do not answer `unchanged` for it "
           "unless the official page publishes no reward programme at all.")
    )


def _signal_block() -> str:
    return ("Tier: signal. Run stage 1 only (signal search). Summarise what you found in at "
            "most 3 lines in `notes`, with status `signal`. Do not look at any HomeHub "
            "rules for this card; there are none to work from.")


def _card_block(ci: CardInput) -> str:
    c = ci.card
    head = (
        f"### Card (account_id {c.account_id})\n"
        f"official: {c.official}\n"
        f"aliases: {_compact(list(c.aliases))}\n"
        f"bank_domains: {_compact(list(c.bank_domains))}\n"
        f"tier: {c.tier}\n"
        f"country: {c.country}\n"
        f"language: {c.language}\n"
        f"currency: {c.currency}\n"
    )
    body = _precise_block(ci) if c.tier == "precise" else _signal_block()
    return head + body


def build_prompt(inp: TermsInput) -> str:
    template = (files("notify") / "terms_prompt.md").read_text(encoding="utf-8")
    values = {
        "today": inp.today.isoformat(),
        "cards": "\n\n".join(_card_block(ci) for ci in inp.cards),
        "contract": CONTRACT_JSON,
    }
    # Single pass: substituted text (rule descriptions etc.) is never re-scanned for placeholders.
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)
