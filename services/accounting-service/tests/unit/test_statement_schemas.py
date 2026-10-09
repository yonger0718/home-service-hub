import pytest
from pydantic import ValidationError

from app.schemas.statements import Lease, LineIn, RevisionIn
from app.services.statements import derive


def _line(**kw):
    base = dict(seq=1, posted_date="2026-09-03", merchant_raw="全聯", printed_amount="580", line_kind="purchase")
    base.update(kw)
    return base


def _rev(**kw):
    base = dict(run_id=1, lease_token="t" * 16, file_id=1, account_id=1, kind="card", parser="claude-cli", parser_version="2.1.295",
                currency="TWD", period_start="2026-09-01", period_end="2026-09-30", statement_total="580", lines=[_line()], raw={})
    base.update(kw)
    return base


def test_revision_in_forbids_extra_and_caps_lines():
    RevisionIn(**_rev())
    with pytest.raises(ValidationError):
        RevisionIn(**_rev(extra=1))
    RevisionIn(**_rev(lines=[_line(seq=i) for i in range(1, 2001)]))
    with pytest.raises(ValidationError):
        RevisionIn(**_rev(lines=[_line(seq=i) for i in range(1, 2002)]))


def test_lease_token_min_length():
    with pytest.raises(ValidationError):
        Lease(run_id=1, lease_token="t")


def test_line_in_field_parity_with_derive():
    derive.LineIn(**LineIn(**_line()).model_dump())


def test_line_in_validates_kind_and_amount_scale():
    with pytest.raises(ValidationError):
        LineIn(**_line(line_kind="snack"))
    with pytest.raises(ValidationError):
        LineIn(**_line(printed_amount="1.00001"))
    assert LineIn(**_line()).printed_amount == 580
