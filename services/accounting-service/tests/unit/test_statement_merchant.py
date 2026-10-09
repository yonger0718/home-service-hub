from app.services.statements import merchant


def test_normalise_collapses_case_space_and_prefixes():
    assert merchant.normalise("  paypal *Spotify  ") == "PAYPAL SPOTIFY"
    assert merchant.normalise("AMZN Mktp JP*AB12") == "AMAZON JP AB12"
    assert merchant.normalise("全聯福利中心-大安") == "全聯福利中心 大安"


def test_tokens_keep_every_non_empty_token():
    assert merchant.tokens("PAYPAL SPOTIFY 7") == frozenset({"PAYPAL", "SPOTIFY", "7"})


def test_tokens_keep_kana_accents_and_single_cjk():
    assert merchant.normalise("ユニクロ 新宿") == "ユニクロ 新宿"
    assert merchant.tokens("CAFÉ NÉRO") == frozenset({"CAFÉ", "NÉRO"})
    assert merchant.tokens("STORE A") == frozenset({"STORE", "A"})
