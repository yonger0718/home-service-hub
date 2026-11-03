import json
from dataclasses import replace
from pathlib import Path

import pytest

from notify.terms_config import CardEntry
from notify.terms_parse import (CardResult, TermsParseError, TruncatedSource, digest, last_json_block,
                                parse_result, source_ok)
from tests.notify import terms_fixtures as fx

ON = "https://www.example-bank.com/cards/terms"
CARDS = (
    CardEntry(account_id=101, official="Card A", bank_domains=("example-bank.com",), tier="precise"),
    CardEntry(account_id=102, official="Card B", bank_domains=("example-bank.com",), tier="signal"),
    CardEntry(account_id=301, official="Card C", bank_domains=("us-example.com",), tier="precise"),
    CardEntry(account_id=103, official="Card D", bank_domains=("example-bank.com",), tier="precise"),
)
ONE = CARDS[:1]


def by_id(result, account_id):
    return next(c for c in result.cards if c.account_id == account_id)


def test_last_json_block_picks_last():
    assert json.loads(last_json_block(fx.TWO_BLOCKS_STDOUT))["cards"][0]["status"] == "unchanged"


def test_no_json_block_raises():
    with pytest.raises(TermsParseError) as ei:
        last_json_block(fx.NO_JSON_STDOUT)
    assert ei.value.archive is None and ei.value.reason == "parse"
    with pytest.raises(TermsParseError):
        parse_result("{not even a block", CARDS)


def test_error_str_reads_archive_at_call_time_and_carries_no_text():
    secret = "SECRET-MODEL-TEXT"
    with pytest.raises(TermsParseError) as ei:
        parse_result("```json\n{\"cards\": \"" + secret + "\"\n```", CARDS)
    exc = ei.value
    assert str(exc) == "parse None" and secret not in repr(exc) and exc.__cause__ is None
    exc.archive = Path("/r/x")
    assert str(exc) == "parse /r/x"


def test_host_rule_normalises_case_and_trailing_dot():
    doms = ("Example-Bank.com.",)
    assert source_ok("HTTPS://Www.Example-Bank.com./terms", doms)
    assert source_ok("https://www.example-bank.com/x", doms)
    assert source_ok("https://example-bank.com", doms)
    assert not source_ok("http://www.example-bank.com/x", doms)
    assert not source_ok("https://user@example-bank.com", doms)
    assert not source_ok("https://user:pw@example-bank.com", doms)
    assert not source_ok("https://evil-example-bank.com", doms)
    assert not source_ok("https://example-bank.com.evil.com/", doms)
    assert not source_ok("https://evil.com/example-bank.com", doms)
    assert not source_ok("https://evil.com/?u=example-bank.com", doms)
    assert not source_ok("https://example-bank.com:8443/x", doms)
    assert source_ok("https://example-bank.com:443/x", doms)
    assert not source_ok("https:///x", doms)
    assert not source_ok("", doms)
    assert not source_ok("https://[::1/x", doms)


def test_off_domain_finding_downgrades_to_unverified():
    r = by_id(parse_result(fx.FULL_STDOUT, CARDS), 101)
    assert r.status == "changed"
    assert [f.rule_id for f in r.findings] == [162]
    assert [f.field for f in r.unverified] == ["missing_rule"]
    assert r.current_period_end.date == "2027-03-31"
    assert r.sources == (ON,)  # off-domain source never survives


def test_next_terms_off_domain_dropped_and_url_kept():
    card = {"account_id": 301, "status": "announced",
            "next_terms": {"announced": True, "period": [None, None], "rules": [],
                           "source": "https://evil.com/n", "quote": "q"}}
    r = by_id(parse_result(fx.one_card(card), CARDS[2:3]), 301)
    assert r.next_terms is None and r.next_terms_unverified_url == "https://evil.com/n"


def test_period_end_off_domain_or_empty_quote_is_null():
    def run(pe):
        return parse_result(fx.one_card({"account_id": 101, "status": "unchanged",
                                         "current_period_end": pe}), ONE).cards[0]
    assert run({"date": "2027-03-31", "source": "https://evil.com/x", "quote": "q"}).current_period_end is None
    assert run({"date": "2027-03-31", "source": ON, "quote": ""}).current_period_end is None
    assert run({"date": "2027-03-31"}).current_period_end is None
    assert run(None).current_period_end is None
    assert run({"date": "2027-03-31", "source": ON, "quote": "q"}).current_period_end.date == "2027-03-31"


def test_empty_quote_finding_is_unverified():
    f = {"rule_id": 1, "field": "rate", "site": "1", "system": "2", "source": ON, "quote": "  "}
    r = parse_result(fx.one_card({"account_id": 101, "status": "changed", "findings": [f]}), ONE).cards[0]
    assert r.findings == () and len(r.unverified) == 1


def test_next_terms_empty_quote_sets_unverified_url():
    card = {"account_id": 301, "status": "announced",
            "next_terms": {"announced": True, "period": [None, None], "rules": [],
                           "source": "https://cards.us-example.com/n", "quote": ""}}
    r = parse_result(fx.one_card(card), CARDS[2:3]).cards[0]
    assert r.next_terms is None and r.next_terms_unverified_url == "https://cards.us-example.com/n"


def test_missing_card_is_not_found_and_foreign_id_fails():
    result = parse_result(fx.FULL_STDOUT, CARDS)
    assert [c.account_id for c in result.cards] == [101, 102, 301, 103]
    miss = by_id(result, 103)
    assert miss.status == "not_found" and miss.findings == () and miss.next_terms is None
    with pytest.raises(TermsParseError):
        parse_result(fx.FOREIGN_ID_STDOUT, CARDS)
    dup = fx.one_card({"account_id": 101, "status": "unchanged"})
    dup = dup.replace('"cards": [', '"cards": [{"account_id": 101, "status": "unchanged"},')
    with pytest.raises(TermsParseError):
        parse_result(dup, ONE)


def test_status_required_fields():
    with pytest.raises(TermsParseError):
        parse_result(fx.one_card({"account_id": 101, "status": "changed"}), ONE)
    with pytest.raises(TermsParseError):
        parse_result(fx.one_card({"account_id": 301, "status": "announced"}), CARDS[2:3])
    with pytest.raises(TermsParseError):
        parse_result(fx.one_card({"account_id": 101, "status": "bogus"}), ONE)
    assert parse_result(fx.one_card({"account_id": 101, "status": "not_found"}), ONE).cards[0].status == "not_found"


def _announced(rate, cap):
    return {"account_id": 301, "status": "announced",
            "next_terms": {"announced": True, "period": ["2027-01-01", None],
                           "rules": [{"name": "R", "rate": rate, "cap": cap, "notes": " a  b "}],
                           "source": "https://cards.us-example.com/n", "quote": "q"}}


def test_numbers_accepted_and_canonicalised():
    r = parse_result(fx.one_card(_announced(3, "3.0000")), CARDS[2:3]).cards[0]
    rule = r.next_terms.rules[0]
    assert rule["rate"] == "3" and rule["cap"] == "3"
    r = parse_result(fx.one_card(_announced("100", None)), CARDS[2:3]).cards[0]
    assert r.next_terms.rules[0]["rate"] == "100" and r.next_terms.rules[0]["cap"] is None
    with pytest.raises(TermsParseError):
        parse_result(fx.one_card(_announced("abc", None)), CARDS[2:3])


def _prec(quote="same quote", rate=3, order=False):
    fs = [{"rule_id": 1, "field": "rate", "site": "3", "system": "2", "source": ON, "quote": quote},
          {"rule_id": 2, "field": "cap", "site": "9", "system": "8", "source": ON, "quote": "q2"}]
    if order:
        fs.reverse()
    card = {"account_id": 101, "status": "changed", "findings": fs,
            "next_terms": {"announced": True, "period": [None, None],
                           "rules": [{"name": "R", "rate": rate, "cap": None, "notes": None}],
                           "source": ON, "quote": "nq"}}
    return parse_result(fx.one_card(card), ONE).cards[0]


def test_digest_stable_under_number_form_and_whitespace():
    base = digest(_prec(), "precise")
    assert len(base) == 64
    assert digest(_prec(quote="  same \n quote\t"), "precise") == base
    assert digest(_prec(rate="3.0000"), "precise") == base
    assert digest(_prec(order=True), "precise") == base
    assert digest(_prec(rate=4), "precise") != base
    assert digest(_prec(quote="other quote"), "precise") != base


def test_signal_digest_hashes_notes_only():
    def sig(notes, status="signal"):
        return parse_result(fx.one_card({"account_id": 102, "status": status, "notes": notes}),
                            CARDS[1:2]).cards[0]
    assert digest(sig("a  b"), "signal") == digest(sig(" a b\n"), "signal")
    assert digest(sig("a b"), "signal") != digest(sig("a c"), "signal")
    assert digest(sig("a b", "unchanged"), "signal") == digest(sig("a b"), "signal")


def test_truncated_entries_attached_to_card():
    r = parse_result(fx.FULL_STDOUT, CARDS)
    assert by_id(r, 101).truncated == (TruncatedSource(ON, True, "first ~40k chars of ~130k"),)
    assert by_id(r, 102).truncated == ()
    assert isinstance(r.fetch_log, dict) and r.fetch_log["tool_calls"] == 15
    assert isinstance(by_id(r, 101), CardResult)


HOSTILE = [
    "https://evil.com\\.example-bank.com/",
    "https://evil.com%2f.example-bank.com/",
    "https://evil.com\x00.example-bank.com/",
    "https://exam\tple-bank.com/",
    "https://example-bank.com/x https://evil.com",
    "https://example-bank.com/x\nhttps://evil.com",
    "https://.example-bank.com/",
    "https://example-bank.com/" + "a" * 2100,
]


@pytest.mark.parametrize("url", HOSTILE)
def test_source_rule_rejects_hostile_urls(url):
    assert not source_ok(url, ("example-bank.com",))


def test_source_rule_accepts_good_urls():
    d = ("example-bank.com",)
    assert source_ok("https://www.example-bank.com/x", d)
    assert source_ok("HTTPS://Www.Example-Bank.com./terms", d)
    assert source_ok("https://EXAMPLE-BANK.COM:443/x", d)


def test_deeply_nested_json_is_parse_error():
    text = "```json\n" + "[" * 100000 + "\n```"
    with pytest.raises(TermsParseError):
        parse_result(text, ONE)


def test_decimal_bounds():
    def rate(v):
        return parse_result(fx.one_card(_announced(v, None)), CARDS[2:3]).cards[0].next_terms.rules[0]["rate"]
    assert rate("1E+1") == "10" and rate("-0") == "0"
    for bad in ("1e50000000", "1" * 41, "1e31", "NaN"):
        with pytest.raises(TermsParseError):
            rate(bad)


def test_unverified_finding_source_is_sanitised():
    def run(src):
        f = {"rule_id": 1, "field": "rate", "site": "1", "system": "2", "source": src, "quote": "q"}
        return parse_result(fx.one_card({"account_id": 101, "status": "changed", "findings": [f]}),
                            ONE).cards[0].unverified[0].source
    assert run("https://evil.com/p") == "https://evil.com/p"
    assert run("https://evil.com/p x\nhttps://a") == ""
    assert run("https://evil.com\\x") == ""


def test_fetch_log_sanitised():
    log = {"tool_calls": 3,
           "failed": [ON + " (403)", "https://evil.com/x (403)", "junk url"],
           "truncated": [{"account_id": 101, "url": "https://us-example.com/x", "read": "r1"},
                         {"account_id": 101, "url": ON, "read": "r2"}]}
    r = parse_result(fx.one_card({"account_id": 101, "status": "unchanged"}, fetch_log=log), ONE)
    assert r.fetch_log["tool_calls"] == 3
    assert r.fetch_log["failed"] == [ON + " (403)"]  # failing entries are dropped (final fix A1)
    assert r.cards[0].truncated == (TruncatedSource("https://us-example.com/x", False, "r1"),
                                    TruncatedSource(ON, True, "r2"))
    assert [e["url"] for e in r.fetch_log["truncated"]] == ["[unverified source]", ON]
    bad = fx.one_card({"account_id": 101, "status": "unchanged"}, fetch_log={"tool_calls": "3"})
    with pytest.raises(TermsParseError):
        parse_result(bad, ONE)


def test_iso_date_normalised_and_period_end_type_errors():
    nt = _announced(1, None)
    nt["next_terms"]["period"] = ["20270331", None]
    assert parse_result(fx.one_card(nt), CARDS[2:3]).cards[0].next_terms.period == ("2027-03-31", None)
    pe = {"date": "2027-03-31", "source": 5, "quote": "q"}
    with pytest.raises(TermsParseError):
        parse_result(fx.one_card({"account_id": 101, "status": "unchanged", "current_period_end": pe}), ONE)


def test_digest_covers_unverified_and_unverified_url():
    base = _prec()
    assert digest(replace(base, next_terms_unverified_url="https://evil.com/a"), "precise") != digest(base, "precise")
    assert digest(replace(base, next_terms_unverified_url="  "), "precise") == digest(base, "precise")
    extra = replace(base, unverified=base.unverified + (replace(base.findings[0], source="https://evil.com"),))
    assert digest(extra, "precise") != digest(base, "precise")


def test_unverified_url_filtered_and_blank_is_none():
    def run(src):
        card = _announced(1, None)
        card["next_terms"]["source"] = src
        card["next_terms"]["quote"] = ""
        return parse_result(fx.one_card(card), CARDS[2:3]).cards[0].next_terms_unverified_url
    assert run("https://evil.com/a") == "https://evil.com/a"
    assert run("https://evil.com/a b") is None
    assert run("   ") is None


@pytest.mark.parametrize("suffix", [" (403) https://evil.com", " https://evil.com", " <https://evil.com>",
                                    " (see evil.com/x)"])
def test_failed_entry_hostile_suffix_dropped(suffix):
    log = {"failed": [ON + suffix, ON + " (403)", ON + " (timeout)"]}
    r = parse_result(fx.one_card({"account_id": 101, "status": "unchanged"}, fetch_log=log), ONE)
    assert r.fetch_log["failed"] == [ON, ON + " (403)", ON + " (timeout)"]


def test_truncated_read_whitelisted_and_log_keys_dropped():
    def run(read):
        log = {"note": "https://evil.com",
               "truncated": [{"account_id": 101, "url": ON, "read": read}]}
        r = parse_result(fx.one_card({"account_id": 101, "status": "unchanged"}, fetch_log=log), ONE)
        assert "note" not in r.fetch_log
        return r.fetch_log["truncated"][0]["read"], r.cards[0].truncated[0]
    assert run("x https://evil.com y") == ("", TruncatedSource(ON, True, ""))
    assert run("first ~40k chars of ~130k") == ("first ~40k chars of ~130k",
                                                TruncatedSource(ON, True, "first ~40k chars of ~130k"))
    assert run("前 4 萬字")[0] == "前 4 萬字"


@pytest.mark.parametrize("bad", ["1_0", "\u0661\u0662", "1 0", "0x10"])
def test_numeric_strings_ascii_only(bad):
    with pytest.raises(TermsParseError):
        parse_result(fx.one_card(_announced(bad, None)), CARDS[2:3])


# --- C1: printable-URL rule (final fix A1) ---------------------------------------------------------------------

SMUGGLE = ["#<https://evil.example/x>", "/<@&1>", "/@everyone", "/a`b`", "/a*b*", "/[x](y)", "/a'b", "/a\"b",
           "/a{b}", "/a|b", "/a^b", "/a[b]"]


@pytest.mark.parametrize("tail", SMUGGLE)
def test_source_rule_rejects_unprintable_url_chars(tail):
    assert not source_ok(ON + tail, ("example-bank.com",))


def test_source_rule_accepts_rfc3986_printables():
    assert source_ok(ON + "/a-b._~:/?q=1&r=(2)!$+,;%20#frag", ("example-bank.com",))


@pytest.mark.parametrize("tail", SMUGGLE)
def test_smuggled_finding_source_is_unverified(tail):
    f = {"rule_id": 1, "field": "rate", "site": "1", "system": "2", "source": ON + tail, "quote": "q"}
    r = parse_result(fx.one_card({"account_id": 101, "status": "changed", "findings": [f],
                                  "sources": [ON + tail]}), ONE).cards[0]
    assert r.findings == () and len(r.unverified) == 1 and r.sources == ()


@pytest.mark.parametrize("tail", SMUGGLE)
def test_smuggled_truncated_url_keeps_flag_unverified(tail):
    log = {"truncated": [{"account_id": 101, "url": ON + tail, "read": "r1"}]}
    r = parse_result(fx.one_card({"account_id": 101, "status": "unchanged"}, fetch_log=log), ONE)
    (t,) = r.cards[0].truncated
    assert t.verified is False and t.read == "r1"
    assert r.fetch_log["truncated"][0]["url"] == "[unverified source]"


@pytest.mark.parametrize("tail", SMUGGLE)
def test_smuggled_failed_url_dropped(tail):
    log = {"failed": [ON + tail + " (403)", ON + " (500)"]}
    r = parse_result(fx.one_card({"account_id": 101, "status": "unchanged"}, fetch_log=log), ONE)
    assert r.fetch_log["failed"] == [ON + " (500)"]
