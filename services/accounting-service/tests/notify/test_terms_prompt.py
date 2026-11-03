import json
from datetime import date

from notify.terms_config import CardEntry
from notify.terms_input import RULE_FIELDS, CardInput, TermsInput
from notify.terms_prompt import CONTRACT_JSON, build_prompt

TODAY = date(2026, 11, 3)


def _rule(rid, **kw):
    r = {k: None for k in RULE_FIELDS}
    r.update(id=rid, name=f"rule {rid}", method="percent", rate="1.5000", is_basic=False,
             starts_on="2026-01-01", ends_on="2026-12-31", description="desc 回饋")
    r.update(kw)
    return r


def _inp(*cards):
    return TermsInput(TODAY, "scheduled", tuple(cards), ())


TW = CardInput(CardEntry(101, "https://example-bank.com/card", ("example-bank.com", "cards.example-bank.com"),
                         "precise", aliases=("Alpha",)), (_rule(11), _rule(12)))
US = CardInput(CardEntry(102, "https://example-us.com/c", ("example-us.com",), "precise",
                         country="US", language="en", currency="USD"), ())
SIG = CardInput(CardEntry(301, "https://example-sig.com/c", ("example-sig.com",), "signal"),
                (_rule(31, name="SIGRULE"),))


def test_prompt_contains_each_card_block():
    p = build_prompt(_inp(TW, US))
    for s in ("https://example-bank.com/card", "example-bank.com", "cards.example-bank.com",
              "tier: precise", "language: zh-TW", "currency: TWD", "Alpha", "2026-11-03",
              "https://example-us.com/c", "language: en", "currency: USD", "country: US"):
        assert s in p


def test_precise_rules_serialised_with_rule_fields_only():
    rule = _rule(11)
    rule["extra_internal"] = "LEAK"
    p = build_prompt(_inp(CardInput(TW.card, (rule,))))
    line = json.dumps({k: rule[k] for k in RULE_FIELDS}, ensure_ascii=False, separators=(",", ":"))
    assert line in p
    assert "LEAK" not in p
    assert "回饋" in p  # not ascii-escaped
    assert "name, method" not in p and "`name`, `method`" in p
    assert "`id`," not in p  # field list is RULE_FIELDS minus id
    assert "zero live rules" not in p


def test_zero_rule_precise_card_says_missing_rule():
    p = build_prompt(_inp(US))
    assert "zero live rules" in p and "`missing_rule`" in p


def test_signal_card_has_no_rule_comparison_instruction():
    p = build_prompt(_inp(SIG))
    assert "Tier: signal" in p and "3 lines" in p
    assert "SIGRULE" not in p
    assert "compare each live rule" not in p
    assert "missing_rule" not in p.split("## Next period")[0]


def test_contract_block_present_once():
    p = build_prompt(_inp(TW, SIG))
    assert p.count(CONTRACT_JSON) == 1
    assert '"1.5"' in p
    assert p.rstrip().endswith("```")
    assert p.count("```json") == 1


def test_prompt_mentions_truncated_and_missing_rule():
    p = build_prompt(_inp(TW))
    assert "fetch_log.truncated" in p and "missing_rule" in p
    assert "Traditional Chinese" in p and "verbatim" in p and "not_found" in p


def test_rule_text_is_not_treated_as_placeholder():
    r = _rule(11, description="{contract} {today} {cards}")
    p = build_prompt(_inp(CardInput(TW.card, (r,))))
    assert "{contract} {today} {cards}" in p
    assert p.count(CONTRACT_JSON) == 1
