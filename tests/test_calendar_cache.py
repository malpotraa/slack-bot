"""Per-user credential/service cache eviction (google_calendar).

Guards the P1 fix: a silent token refresh must not leave a stale
_SERVICE_CACHE entry behind, because that entry pins the old Credentials
object alive forever.
"""

from app.integrations import google_calendar as gcal


def test_invalidate_user_cache_evicts_creds_and_service():
    fake_creds = object()
    user_id = 999_001
    gcal._CREDENTIALS_CACHE[user_id] = fake_creds  # type: ignore[assignment]
    gcal._SERVICE_CACHE[id(fake_creds)] = "fake-service"

    gcal.invalidate_user_cache(user_id)

    assert user_id not in gcal._CREDENTIALS_CACHE
    assert id(fake_creds) not in gcal._SERVICE_CACHE


def test_invalidate_user_cache_is_noop_for_unknown_user():
    # Must not raise when the user was never cached.
    gcal.invalidate_user_cache(424_242)
    assert 424_242 not in gcal._CREDENTIALS_CACHE


def test_invalidate_drops_service_for_stale_creds():
    # Simulates the refresh path: the old creds + its service entry must both
    # be gone once the cache is invalidated, so no service object leaks.
    old_creds = object()
    user_id = 999_002
    gcal._CREDENTIALS_CACHE[user_id] = old_creds  # type: ignore[assignment]
    gcal._SERVICE_CACHE[id(old_creds)] = "old-service"

    gcal.invalidate_user_cache(user_id)

    assert id(old_creds) not in gcal._SERVICE_CACHE
