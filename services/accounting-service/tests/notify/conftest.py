import pytest


@pytest.fixture
def fx():
    """The fictional notifier fixture module (builders + report helpers)."""
    import notify_fixtures
    return notify_fixtures
