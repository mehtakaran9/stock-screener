import json
import time
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.main import (
    app,
    _load_cache, _save_cache,
    _load_cache_v2, _save_cache_v2,
    _load_cache_v3, _save_cache_v3,
)

client = TestClient(app)


# ── Root / Health ─────────────────────────────────────────────────────────────

def test_root_get():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Stock Screener" in resp.json()["message"]


def test_root_head():
    resp = client.head("/")
    assert resp.status_code == 200


def test_get_filters_returns_10_items():
    resp = client.get("/api/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert "filters" in data
    assert len(data["filters"]) == 10


# ── Cache helpers ─────────────────────────────────────────────────────────────

def test_load_cache_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.main.CACHE_FILE", tmp_path / "missing.json")
    assert _load_cache() is None


def test_load_cache_expired(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"timestamp": time.time() - 700, "results": [], "total": 10}))
    monkeypatch.setattr("backend.main.CACHE_FILE", cache)
    assert _load_cache() is None
    assert not cache.exists()


def test_load_cache_valid_returns_and_keeps_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"timestamp": time.time(), "results": [{"ticker": "A"}], "total": 500}))
    monkeypatch.setattr("backend.main.CACHE_FILE", cache)
    result = _load_cache()
    assert result is not None
    results, total = result
    assert results == [{"ticker": "A"}]
    assert total == 500
    assert cache.exists()  # file persists for subsequent reads within TTL


def test_load_cache_corrupt_returns_none_and_deletes(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text("not {{valid json")
    monkeypatch.setattr("backend.main.CACHE_FILE", cache)
    assert _load_cache() is None
    assert not cache.exists()


def test_save_cache_writes_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    monkeypatch.setattr("backend.main.CACHE_FILE", cache)
    _save_cache([{"ticker": "AAPL"}], 500)
    data = json.loads(cache.read_text())
    assert data["total"] == 500
    assert data["results"] == [{"ticker": "AAPL"}]
    assert "timestamp" in data


def test_save_cache_handles_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.main.CACHE_FILE", tmp_path)  # directory → write fails
    _save_cache([], 10)  # must not raise


# ── Scan endpoint: full scan ──────────────────────────────────────────────────

def _collect_sse_events(response) -> list:
    events = []
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode()
        if line.startswith("data:"):
            events.append(json.loads(line[6:]))
    return events


@patch("backend.main.get_full_market_tickers")
@patch("backend.main.screen_stocks")
@patch("backend.main._save_cache")
def test_scan_market_full_scan_streams_events(mock_save, mock_screen, mock_tickers):
    mock_tickers.return_value = (["AAPL"], True)

    async def fake_screen(tickers):
        yield {"status": "progress", "current": 1, "ticker": "AAPL"}
        yield {"ticker": "AAPL", "price": 150.0, "change": 5.0}

    mock_screen.side_effect = fake_screen

    with client.stream("GET", "/api/scan") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e["status"] for e in events]
    assert "progress" in statuses
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    mock_save.assert_called_once()


@patch("backend.main.get_full_market_tickers")
@patch("backend.main.screen_stocks")
@patch("backend.main._save_cache")
def test_scan_market_fallback_tickers_emits_warning(mock_save, mock_screen, mock_tickers):
    mock_tickers.return_value = (["AAPL"], False)  # is_full=False

    async def fake_screen(tickers):
        yield {"status": "progress", "current": 1}

    mock_screen.side_effect = fake_screen

    with client.stream("GET", "/api/scan") as resp:
        events = _collect_sse_events(resp)

    assert any(e.get("status") == "warning" for e in events)


# ── Scan endpoint: cache hit ──────────────────────────────────────────────────

@patch("backend.main._load_cache")
def test_scan_post_lock_cache_hit(mock_load):
    """Cache miss before lock, then cache hit inside lock → serves cached results (lines 103-109)."""
    mock_load.side_effect = [None, ([{"ticker": "AAPL", "price": 150.0}], 500)]

    with client.stream("GET", "/api/scan") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    assert any(e.get("status") == "result" for e in events)
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True


@patch("backend.main._load_cache")
def test_scan_market_serves_from_cache(mock_load):
    mock_load.return_value = ([{"ticker": "AAPL", "price": 150.0}], 500)

    with client.stream("GET", "/api/scan") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e.get("status") for e in events]
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True


# ── V2 filters endpoint ───────────────────────────────────────────────────────

def test_get_filters_v2_returns_8_items():
    resp = client.get("/api/filters-v2")
    assert resp.status_code == 200
    data = resp.json()
    assert "filters" in data
    assert len(data["filters"]) == 8


# ── V2 scan endpoint: full scan ───────────────────────────────────────────────

@patch("backend.main.get_full_market_tickers")
@patch("backend.main.screen_stocks_v2")
@patch("backend.main._save_cache_v2")
def test_scan_v2_full_scan_streams_events(mock_save, mock_screen, mock_tickers):
    mock_tickers.return_value = (["AAPL"], True)

    async def fake_screen(tickers):
        yield {"status": "progress", "current": 1, "ticker": "AAPL"}
        yield {"ticker": "AAPL", "price": 50.0, "change": -6.0}

    mock_screen.side_effect = fake_screen

    with client.stream("GET", "/api/scan-v2") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e["status"] for e in events]
    assert "progress" in statuses
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    mock_save.assert_called_once()


@patch("backend.main.get_full_market_tickers")
@patch("backend.main.screen_stocks_v2")
@patch("backend.main._save_cache_v2")
def test_scan_v2_fallback_tickers_emits_warning(mock_save, mock_screen, mock_tickers):
    mock_tickers.return_value = (["AAPL"], False)

    async def fake_screen(tickers):
        yield {"status": "progress", "current": 1}

    mock_screen.side_effect = fake_screen

    with client.stream("GET", "/api/scan-v2") as resp:
        events = _collect_sse_events(resp)

    assert any(e.get("status") == "warning" for e in events)


# ── V2 scan endpoint: cache hit ───────────────────────────────────────────────

@patch("backend.main._load_cache_v2")
def test_scan_v2_serves_from_cache(mock_load):
    mock_load.return_value = ([{"ticker": "AAPL", "price": 50.0}], 500)

    with client.stream("GET", "/api/scan-v2") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e.get("status") for e in events]
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True


@patch("backend.main._load_cache_v2")
def test_scan_v2_post_lock_cache_hit(mock_load):
    """Cache miss before lock, then hit inside lock → serves cached results."""
    mock_load.side_effect = [None, ([{"ticker": "AAPL", "price": 50.0}], 500)]

    with client.stream("GET", "/api/scan-v2") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    assert any(e.get("status") == "result" for e in events)
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True


# ── V2 cache helpers ──────────────────────────────────────────────────────────

def test_load_cache_v2_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.main.CACHE_FILE_V2", tmp_path / "missing.json")
    assert _load_cache_v2() is None


def test_load_cache_v2_expired(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v2.json"
    cache.write_text(json.dumps({"timestamp": time.time() - 700, "results": [], "total": 10}))
    monkeypatch.setattr("backend.main.CACHE_FILE_V2", cache)
    assert _load_cache_v2() is None
    assert not cache.exists()


def test_load_cache_v2_valid_returns_and_keeps_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v2.json"
    cache.write_text(json.dumps({"timestamp": time.time(), "results": [{"ticker": "B"}], "total": 500}))
    monkeypatch.setattr("backend.main.CACHE_FILE_V2", cache)
    result = _load_cache_v2()
    assert result is not None
    results, total = result
    assert results == [{"ticker": "B"}]
    assert total == 500
    assert cache.exists()


def test_load_cache_v2_corrupt_returns_none_and_deletes(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v2.json"
    cache.write_text("not {{valid json")
    monkeypatch.setattr("backend.main.CACHE_FILE_V2", cache)
    assert _load_cache_v2() is None
    assert not cache.exists()


def test_save_cache_v2_writes_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v2.json"
    monkeypatch.setattr("backend.main.CACHE_FILE_V2", cache)
    _save_cache_v2([{"ticker": "B"}], 500)
    data = json.loads(cache.read_text())
    assert data["total"] == 500
    assert data["results"] == [{"ticker": "B"}]
    assert "timestamp" in data


def test_save_cache_v2_handles_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.main.CACHE_FILE_V2", tmp_path)  # directory → write fails
    _save_cache_v2([], 10)  # must not raise


# ── V3 filters endpoint ───────────────────────────────────────────────────────

def test_get_filters_v3_returns_10_items():
    resp = client.get("/api/filters-v3")
    assert resp.status_code == 200
    data = resp.json()
    assert "filters" in data
    assert len(data["filters"]) == 10


# ── V3 scan endpoint: full scan ───────────────────────────────────────────────

@patch("backend.main.get_full_market_tickers")
@patch("backend.main.screen_stocks_v3")
@patch("backend.main._save_cache_v3")
def test_scan_v3_full_scan_streams_events(mock_save, mock_screen, mock_tickers):
    mock_tickers.return_value = (["AAPL"], True)

    async def fake_screen(tickers):
        yield {"status": "progress", "current": 1, "ticker": "AAPL"}
        yield {"ticker": "AAPL", "price": 45.0, "change": -7.0, "conviction_score": 2}

    mock_screen.side_effect = fake_screen

    with client.stream("GET", "/api/scan-v3") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e["status"] for e in events]
    assert "progress" in statuses
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    mock_save.assert_called_once()


@patch("backend.main.get_full_market_tickers")
@patch("backend.main.screen_stocks_v3")
@patch("backend.main._save_cache_v3")
def test_scan_v3_fallback_tickers_emits_warning(mock_save, mock_screen, mock_tickers):
    mock_tickers.return_value = (["AAPL"], False)

    async def fake_screen(tickers):
        yield {"status": "progress", "current": 1}

    mock_screen.side_effect = fake_screen

    with client.stream("GET", "/api/scan-v3") as resp:
        events = _collect_sse_events(resp)

    assert any(e.get("status") == "warning" for e in events)


# ── V3 scan endpoint: cache hit ───────────────────────────────────────────────

@patch("backend.main._load_cache_v3")
def test_scan_v3_serves_from_cache(mock_load):
    mock_load.return_value = ([{"ticker": "AAPL", "price": 45.0, "conviction_score": 1}], 500)

    with client.stream("GET", "/api/scan-v3") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e.get("status") for e in events]
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True


@patch("backend.main._load_cache_v3")
def test_scan_v3_post_lock_cache_hit(mock_load):
    """Cache miss before lock, then hit inside lock → serves cached results."""
    mock_load.side_effect = [None, ([{"ticker": "AAPL", "price": 45.0}], 500)]

    with client.stream("GET", "/api/scan-v3") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    assert any(e.get("status") == "result" for e in events)
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True


# ── V3 cache helpers ──────────────────────────────────────────────────────────

def test_load_cache_v3_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.main.CACHE_FILE_V3", tmp_path / "missing.json")
    assert _load_cache_v3() is None


def test_load_cache_v3_expired(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v3.json"
    cache.write_text(json.dumps({"timestamp": time.time() - 700, "results": [], "total": 10}))
    monkeypatch.setattr("backend.main.CACHE_FILE_V3", cache)
    assert _load_cache_v3() is None
    assert not cache.exists()


def test_load_cache_v3_valid_returns_and_keeps_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v3.json"
    cache.write_text(json.dumps({"timestamp": time.time(), "results": [{"ticker": "C"}], "total": 500}))
    monkeypatch.setattr("backend.main.CACHE_FILE_V3", cache)
    result = _load_cache_v3()
    assert result is not None
    results, total = result
    assert results == [{"ticker": "C"}]
    assert total == 500
    assert cache.exists()


def test_load_cache_v3_corrupt_returns_none_and_deletes(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v3.json"
    cache.write_text("not {{valid json")
    monkeypatch.setattr("backend.main.CACHE_FILE_V3", cache)
    assert _load_cache_v3() is None
    assert not cache.exists()


def test_save_cache_v3_writes_file(tmp_path, monkeypatch):
    cache = tmp_path / "cache_v3.json"
    monkeypatch.setattr("backend.main.CACHE_FILE_V3", cache)
    _save_cache_v3([{"ticker": "C", "conviction_score": 2}], 500)
    data = json.loads(cache.read_text())
    assert data["total"] == 500
    assert data["results"][0]["ticker"] == "C"
    assert "timestamp" in data


def test_save_cache_v3_handles_write_error(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.main.CACHE_FILE_V3", tmp_path)  # directory → write fails
    _save_cache_v3([], 10)  # must not raise
