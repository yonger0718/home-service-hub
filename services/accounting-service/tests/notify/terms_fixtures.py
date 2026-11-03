"""Canned Hermes stdout for the terms parser tests (fictional banks, ids and quotes)."""
import json

_Q = "Example promotion rule text"
_ON = "https://www.example-bank.com/cards/terms"
_OFF = "https://evil-example-bank.com/terms"


def _wrap(payload: dict, prose: str = "Report in prose.\n") -> str:
    return prose + "\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"


FULL_PAYLOAD = {
    "cards": [
        {
            "account_id": 101,
            "status": "changed",
            "current_period_end": {"date": "2027-03-31", "source": _ON, "quote": "valid until 2027-03-31"},
            "findings": [
                {"rule_id": 162, "field": "ends_on", "site": "2027-03-31", "system": "2026-12-31",
                 "source": _ON, "quote": _Q},
                {"rule_id": None, "field": "missing_rule", "site": "dining +1% cap 200", "system": None,
                 "source": _OFF, "quote": "invented quote"},
            ],
            "sources": [_ON, _OFF],
            "notes": "changed note",
        },
        {"account_id": 102, "status": "signal", "notes": "points programme looks the same"},
        {
            "account_id": 301,
            "status": "announced",
            "next_terms": {
                "announced": True,
                "period": ["2027-01-01", "2027-03-31"],
                "rules": [{"name": "Overseas dining", "rate": 1.5, "cap": "3000.00", "notes": None}],
                "source": "https://cards.us-example.com/next",
                "quote": "Starting January 2027 the rate is 1.5%",
            },
        },
    ],
    "fetch_log": {"tool_calls": 15, "failed": [],
                  "truncated": [{"account_id": 101, "url": _ON, "read": "first ~40k chars of ~130k"}]},
}

FULL_STDOUT = _wrap(FULL_PAYLOAD)

FOREIGN_ID_STDOUT = _wrap({"cards": [{"account_id": 999, "status": "unchanged"}]})

NO_JSON_STDOUT = "I looked at the pages but forgot the block.\n```\nnot json\n```\n"

TWO_BLOCKS_STDOUT = (
    "```json\n{\"cards\": [{\"account_id\": 101, \"status\": \"changed\"}]}\n```\n"
    "revised:\n```json\n{\"cards\": [{\"account_id\": 101, \"status\": \"unchanged\"}]}\n```\n"
)


def one_card(card: dict, **top) -> str:
    return _wrap({"cards": [card], **top})
