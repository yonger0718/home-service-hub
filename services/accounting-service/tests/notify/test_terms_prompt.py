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


TW = CardInput(CardEntry(101, "Example Alpha Card", ("example-bank.com", "cards.example-bank.com"),
                         "precise", aliases=("Alpha",)), (_rule(11), _rule(12)))
US = CardInput(CardEntry(102, "Example Beta Card", ("example-us.com",), "precise",
                         country="US", language="en", currency="USD"), ())
SIG = CardInput(CardEntry(301, "Example Travel Rewards Card", ("example-sig.com",), "signal"),
                (_rule(31, name="SIGRULE"),))


def test_prompt_contains_each_card_block():
    p = build_prompt(_inp(TW, US))
    for s in ("Example Alpha Card", "example-bank.com", "cards.example-bank.com",
              "tier: precise", "language: zh-TW", "currency: TWD", "Alpha", "2026-11-03",
              "Example Beta Card", "language: en", "currency: USD", "country: US"):
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
    p = build_prompt(_inp(TW, SIG))
    start = p.index("### Card (account_id 301)")
    block = p[start:p.index("\n## ", start)]
    assert "Tier: signal" in block and "3 lines" in block
    assert "SIGRULE" not in p
    for s in ("compare", "missing_rule", "Live rules"):
        assert s not in block
    assert "aliases: []" in block


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


def test_schema_sample_is_illustrative_and_ids_constrained():
    p = build_prompt(_inp(TW))
    assert "illustrative only" in p
    assert "exactly one `cards[]` entry for each `account_id` listed in the card blocks" in p
    assert "94" not in CONTRACT_JSON


def test_stage_one_mentions_official_name_and_aliases():
    p = build_prompt(_inp(TW))
    assert "product name" in p and "`aliases`" in p
    assert 'aliases: ["Alpha"]' in p


def test_multiple_domains_listed_and_empty_aliases_pinned():
    p = build_prompt(_inp(TW, US))
    assert 'bank_domains: ["example-bank.com","cards.example-bank.com"]' in p
    assert "aliases: []" in p
