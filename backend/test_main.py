import json
import os
import time
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from backend.main import app, _load_cache, _save_cache

client = TestClient(app)


# ── Root / Health ─────────────────────────────────────────────────────────────

def test_root_get():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Stock Screener" in resp.json()["message"]


def test_root_head():
    resp = client.head("/")
    assert resp.status_code == 200


def test_get_filters_returns_11_items():
    resp = client.get("/api/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert "filters" in data
    assert len(data["filters"]) == 11


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


def test_load_cache_valid_returns_and_deletes(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"timestamp": time.time(), "results": [{"ticker": "A"}], "total": 500}))
    monkeypatch.setattr("backend.main.CACHE_FILE", cache)
    result = _load_cache()
    assert result is not None
    results, total = result
    assert results == [{"ticker": "A"}]
    assert total == 500
    assert not cache.exists()


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


# ── History endpoint ──────────────────────────────────────────────────────────

def test_get_history_404_when_empty():
    with patch("yfinance.download") as mock_dl:
        mock_dl.return_value = pd.DataFrame()
        resp = client.get("/api/history/INVALID")
    assert resp.status_code == 404
    assert "No data found" in resp.json()["detail"]


def test_get_history_422_when_too_short():
    with patch("yfinance.download") as mock_dl:
        dates = pd.date_range(end="2024-01-01", periods=100)
        mock_dl.return_value = pd.DataFrame(
            {"Open": [100.0]*100, "High": [105.0]*100,
             "Low": [95.0]*100, "Close": [102.0]*100, "Volume": [1000]*100},
            index=dates,
        )
        resp = client.get("/api/history/AAPL")
    assert resp.status_code == 422
    assert "Insufficient data" in resp.json()["detail"]


def test_get_history_success():
    with patch("yfinance.download") as mock_dl:
        dates = pd.date_range(end="2024-01-01", periods=250)
        mock_dl.return_value = pd.DataFrame(
            {"Open": [100.0]*250, "High": [105.0]*250,
             "Low": [95.0]*250, "Close": [102.0]*250, "Volume": [1000]*250},
            index=dates,
        )
        resp = client.get("/api/history/ADBE")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 250
    assert "time" in data[0]
    assert "close" in data[0]
    assert "ema8" in data[0]
    assert "sma200" in data[0]


def test_ticker_validation_rejects_lowercase():
    resp = client.get("/api/history/aapl")
    assert resp.status_code == 422


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
def test_scan_market_serves_from_cache(mock_load):
    mock_load.return_value = ([{"ticker": "AAPL", "price": 150.0}], 500)

    with client.stream("GET", "/api/scan") as resp:
        assert resp.status_code == 200
        events = _collect_sse_events(resp)

    statuses = [e.get("status") for e in events]
    assert "result" in statuses
    assert events[-1]["status"] == "complete"
    assert events[-1].get("from_cache") is True

