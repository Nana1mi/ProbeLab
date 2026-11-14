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


def test_deerflow_bounded_wait_stops_on_completion():
    with patch('httpx.Client.get') as get, patch('integrations.deerflow.tools.time.sleep') as sleep:
        running, completed = MagicMock(), MagicMock()
        running.json.return_value = {'status': 'running'}
        completed.json.return_value = {'status': 'completed', 'summary': {'final_score': 0.9}}
        get.side_effect = [running, completed]
        result = get_research_status('run_math_demo', wait_seconds=20)
        assert result['status'] == 'completed'
        assert get.call_count == 2
        sleep.assert_called_once()


def test_deerflow_long_wait_does_not_stop_after_old_thirty_second_cap():
    with patch('httpx.Client.get') as get, patch('integrations.deerflow.tools.time.sleep') as sleep, \
         patch('integrations.deerflow.tools.time.monotonic', side_effect=[0, 45, 46]):
        running, completed = MagicMock(), MagicMock()
        running.json.return_value = {'status': 'running'}
        completed.json.return_value = {'status': 'completed'}
        get.side_effect = [running, completed]
        assert get_research_status('run_long', wait_seconds=90)['status'] == 'completed'
        assert get.call_count == 2
        sleep.assert_called_once_with(1.0)


@pytest.mark.parametrize('run_id', ['../private', 'run/id', 'run?secret=x'])
def test_deerflow_client_rejects_invalid_identifiers(run_id):
    with patch('httpx.Client.get') as get:
        with pytest.raises(ValueError):
            get_research_status(run_id)
        with pytest.raises(ValueError):
            get_research_report(run_id)
        get.assert_not_called()
