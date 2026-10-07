"""Protected split members (split rework §1.2): the read flag on GET /entries/{id}.group_members."""

import pytest

from tests.helpers import PROTECTED_REASONS, make_protected_member


@pytest.mark.parametrize("case", sorted(PROTECTED_REASONS))
def test_group_members_carry_the_protected_flag(client, db_session, seed, case):
    # Review Focus 3: real kinds and links decide, never a label.
    wallet = seed.account()
    group = seed.group(name="聚餐")
    plain = seed.entry(wallet, "-100", group_id=group.id)
    member = make_protected_member(seed, case, wallet, group.id)
    db_session.commit()
    plain_id, member_id = plain.id, member.id

    rows = {row["id"]: row for row in client.get(f"/entries/{plain_id}").json()["group_members"]}

    assert (rows[plain_id]["protected"], rows[plain_id]["protected_reason"]) == (False, None)
    assert (rows[member_id]["protected"], rows[member_id]["protected_reason"]) == (True, PROTECTED_REASONS[case])


def test_an_ungrouped_entry_has_no_group_members(client, db_session, seed):
    wallet = seed.account()
    entry = seed.entry(wallet, "-100")
    db_session.commit()

    assert client.get(f"/entries/{entry.id}").json()["group_members"] == []
