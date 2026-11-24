"""VAT knowledge base auto-build (vat_kb/refresh.py): when it runs, and that it never runs twice at once."""
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from gemini_brain.vat_kb import refresh
from gemini_brain.vat_kb.index import BUILDS_DIR, CURRENT_FILE


def _with_build(kb: Path, name: str) -> Path:
    (kb / BUILDS_DIR / name).mkdir(parents=True)
    (kb / CURRENT_FILE).write_text(name, encoding="utf-8")
    return kb


NIGHT = datetime(2026, 11, 24, 21, 30, tzinfo=timezone.utc)
DAY = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)


def test_an_empty_folder_is_built_at_once_even_in_the_day(tmp_path):
    assert refresh.build_age_days(tmp_path) is None
    assert refresh.due(tmp_path, 30, 21, now=DAY)


def test_a_fresh_build_is_left_alone(tmp_path):
    kb = _with_build(tmp_path, "20261120T000000Z")
    assert round(refresh.build_age_days(kb, now=NIGHT)) == 5
    assert not refresh.due(kb, 30, 21, now=NIGHT)


def test_an_old_build_is_rebuilt_only_in_the_night_hour(tmp_path):
    kb = _with_build(tmp_path, "20261004T170549Z")
    assert not refresh.due(kb, 30, 21, now=DAY)
    assert refresh.due(kb, 30, 21, now=NIGHT)


def test_current_naming_a_missing_build_counts_as_no_build(tmp_path):
    (tmp_path / CURRENT_FILE).write_text("20261004T170549Z", encoding="utf-8")
    assert refresh.due(tmp_path, 30, 21, now=DAY)


def test_refresh_crawls_downloads_then_builds(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import embedder, fta_crawler, index

    calls = []
    monkeypatch.setattr(fta_crawler, "collect", lambda: calls.append("collect") or [])
    monkeypatch.setattr(fta_crawler, "download", lambda docs, pdf_dir: calls.append("download") or [])
    monkeypatch.setattr(embedder, "BgeSmallEmbedder", lambda cache: "embedder")
    monkeypatch.setattr(index, "build", lambda kb, emb: calls.append(("build", emb)) or kb / "new")

    assert refresh.refresh(tmp_path) == tmp_path / "new"
    assert calls == ["collect", "download", ("build", "embedder")]
    assert not (tmp_path / refresh.LOCK_FILE).exists()


def test_a_second_worker_skips_while_the_first_builds(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import fta_crawler

    (tmp_path / refresh.LOCK_FILE).write_text("123", encoding="utf-8")
    monkeypatch.setattr(fta_crawler, "collect", lambda: (_ for _ in ()).throw(AssertionError("must not crawl")))
    assert refresh.refresh(tmp_path) is None
    assert (tmp_path / refresh.LOCK_FILE).exists()


def test_a_lock_left_by_a_crashed_worker_expires(tmp_path):
    lock = tmp_path / refresh.LOCK_FILE
    lock.write_text("123", encoding="utf-8")
    old = time.time() - refresh.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    assert refresh._take_lock(tmp_path) == lock


def test_a_failed_crawl_releases_the_lock(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import fta_crawler

    def offline():
        raise ConnectionError("FTA site down")
    monkeypatch.setattr(fta_crawler, "collect", offline)
    try:
        refresh.refresh(tmp_path)
    except ConnectionError:
        pass
    assert not (tmp_path / refresh.LOCK_FILE).exists()


def test_start_does_nothing_when_off(tmp_path):
    assert refresh.start(str(tmp_path), enabled=False, max_age_days=30, refresh_hour_utc=21) is None
    assert refresh.start("", enabled=True, max_age_days=30, refresh_hour_utc=21) is None
    assert refresh.start(str(tmp_path), enabled=True, max_age_days=0, refresh_hour_utc=21) is None
