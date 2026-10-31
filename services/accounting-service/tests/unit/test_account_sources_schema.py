"""AccountSourcesPatch (R7 final review Minor 3): `settlement_source` is a known feed source or null; "" is null."""
import pytest
from pydantic import ValidationError

from app.schemas.writes import SETTLEMENT_SOURCES, AccountSourcesPatch


def test_a_known_settlement_source_is_accepted():
    assert SETTLEMENT_SOURCES == ("portfolio:cathay",)
    assert AccountSourcesPatch(settlement_source="portfolio:cathay").settlement_source == "portfolio:cathay"


@pytest.mark.parametrize("value", ["", None])
def test_an_empty_or_null_settlement_source_clears_it(value):
    patch = AccountSourcesPatch(settlement_source=value)
    assert patch.settlement_source is None and patch.model_fields_set == {"settlement_source"}


@pytest.mark.parametrize("value", ["portfolio:cathy", "cathay", " portfolio:cathay", "portfolio:x"])
def test_an_unknown_settlement_source_is_refused(value):
    with pytest.raises(ValidationError):
        AccountSourcesPatch(settlement_source=value)
