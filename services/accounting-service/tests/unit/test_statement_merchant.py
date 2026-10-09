from app.services.statements import merchant


def test_normalise_collapses_case_space_and_prefixes():
    assert merchant.normalise("  paypal *Spotify  ") == "PAYPAL SPOTIFY"
    assert merchant.normalise("AMZN Mktp JP*AB12") == "AMAZON JP AB12"
    assert merchant.normalise("全聯福利中心-大安") == "全聯福利中心 大安"


def test_tokens_drop_single_characters():
    assert merchant.tokens("PAYPAL SPOTIFY 7") == frozenset({"PAYPAL", "SPOTIFY"})
