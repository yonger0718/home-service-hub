"""SplitIn / SplitKeepIn / SplitOut (split rework §1.3, §1.6): schema-level rules, no database."""

import pytest
from pydantic import ValidationError

from app.schemas.writes import SplitIn, SplitKeepIn, SplitMemberIn, SplitOut

BASE = {"entry_date": "2026-09-01"}
FULL = {"account_id": 1, "kind": "expense", "amount": "100"}


def test_keep_and_full_members_are_told_apart_by_keep():
    payload = SplitIn(**BASE, members=[{"id": 7, "keep": True, "name": "改名"}, {**FULL, "id": 8, "client_key": "b"}, FULL])

    assert [type(member) for member in payload.members] == [SplitKeepIn, SplitMemberIn, SplitMemberIn]
    assert payload.members[0].model_fields_set == {"id", "keep", "name"}
    assert (payload.members[1].id, payload.members[1].client_key, payload.members[2].id) == (8, "b", None)


@pytest.mark.parametrize(
    "field",
    ["amount", "kind", "account_id", "category_id", "counterparty_id", "original_currency", "fx_rate", "fee",
     "discount", "reward_rule_ids", "entry_date", "entry_time", "posted_date", "merchant", "invoice_number"],
)
def test_a_keep_member_refuses_every_non_metadata_field(field):
    with pytest.raises(ValidationError):
        SplitIn(**BASE, members=[{"id": 7, "keep": True, field: None}])


@pytest.mark.parametrize(
    "members",
    [
        [{"id": 7, "keep": True}, {**FULL, "id": 7}],  # duplicate id across forms
        [{**FULL, "client_key": "x"}, {**FULL, "client_key": "x"}],  # duplicate client_key
        [{**FULL, "client_key": "x" * 65}],  # client_key longer than 64
        [{**FULL, "client_key": ""}],
        [],  # min 1
    ],
    ids=["duplicate-id", "duplicate-client-key", "long-client-key", "empty-client-key", "no-members"],
)
def test_schema_refusals(members):
    with pytest.raises(ValidationError):
        SplitIn(**BASE, members=members)


def test_null_and_empty_list_count_as_sent_on_a_keep_member():
    keep = SplitIn(**BASE, members=[{"id": 7, "keep": True, "project_id": None, "tags": []}]).members[0]

    assert keep.model_fields_set == {"id", "keep", "project_id", "tags"}


def test_split_out_allows_a_dissolved_group():
    out = SplitOut(group_id=None, member_ids=[5], members=[{"id": 5, "client_key": None}])

    assert out.model_dump() == {"group_id": None, "member_ids": [5], "members": [{"id": 5, "client_key": None}]}
