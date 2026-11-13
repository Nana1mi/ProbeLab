"""Tests for DeerFlow integration tools."""

import pytest
from unittest.mock import patch, MagicMock
from integrations.deerflow.tools import start_research, get_research_status, get_research_report


def test_deerflow_start_research_mocked():
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"run_id": "test_run_123", "status": "queued"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        res = start_research(task="math", policy="tierlab", backend="mock")
        assert res["run_id"] == "test_run_123"
        assert res["status"] == "queued"
        mock_post.assert_called_once()


def test_deerflow_get_status_mocked():
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"run_id": "test_run_123", "status": "completed", "score": 0.85}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        res = get_research_status("test_run_123")
        assert res["status"] == "completed"
        assert res["score"] == 0.85


def test_deerflow_get_report_mocked():
    with patch("httpx.Client.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.text = "<html>Report Content</html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        html = get_research_report("test_run_123")
        assert "Report Content" in html
