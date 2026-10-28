"""#194: test databases carry their creation time; leftovers of killed runs are chosen for dropping."""
from tests.conftest import STALE_TEST_DB_SECONDS, stale_test_dbs, make_test_db_name

NOW = 1_800_000_000


def test_name_carries_the_creation_time():
    name = make_test_db_name(NOW)
    assert name.startswith(f"accounting_test_{NOW}_") and len(name.rsplit("_", 1)[1]) == 8


def test_only_old_unconnected_stamped_databases_are_stale():
    old = f"accounting_test_{NOW - STALE_TEST_DB_SECONDS - 1}_0123abcd"
    old_connected = f"accounting_test_{NOW - STALE_TEST_DB_SECONDS - 60}_89abcdef"
    fresh = f"accounting_test_{NOW - 60}_deadbeef"
    unstamped = "accounting_test_0123456789ab"
    other = "accounting_db"
    names = [fresh, old, old_connected, unstamped, other]
    assert stale_test_dbs(names, {old_connected}, NOW) == [old]


def test_boundary_is_strictly_older():
    edge = f"accounting_test_{NOW - STALE_TEST_DB_SECONDS}_00000000"
    assert stale_test_dbs([edge], set(), NOW) == []
