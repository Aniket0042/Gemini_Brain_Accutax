"""TTL store behaviour for short-lived artifacts."""
from gemini_brain.artifacts.store import get, put, reset_for_tests, sweep, was_expired


def setup_function():
    reset_for_tests()


def teardown_function():
    reset_for_tests()


def test_put_and_get_roundtrip():
    rec = put(b"hello", filename="a.csv", mime="text/csv", user_id=14, organization_id=2, ttl=120)
    got = get(rec.id)
    assert got is not None
    assert got.user_id == 14
    assert open(got.path, "rb").read() == b"hello"


def test_expired_record_is_gone_and_flagged():
    rec = put(b"x", filename="a.csv", mime="text/csv", user_id=1, ttl=1, now=1000)
    assert get(rec.id, now=1000.5) is not None
    assert get(rec.id, now=1202) is None
    assert was_expired(rec.id) is True


def test_sweep_removes_expired():
    rec = put(b"x", filename="a.csv", mime="text/csv", user_id=1, ttl=1, now=1)
    removed = sweep(now=200)
    assert removed == 1
    assert get(rec.id, now=200) is None
    assert was_expired(rec.id) is True


def test_other_user_still_stored_separately():
    a = put(b"1", filename="a.csv", mime="text/csv", user_id=1, ttl=120)
    b = put(b"2", filename="b.csv", mime="text/csv", user_id=2, ttl=120)
    assert get(a.id).user_id == 1
    assert get(b.id).user_id == 2
