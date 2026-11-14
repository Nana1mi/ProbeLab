"""Durable research events, independent of billing routes and model summaries."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ResearchTrace:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seq = len(read_trace(self.path))

    def record(self, kind: str, **data: Any) -> dict:
        with self._lock:
            self._seq += 1
            event = dict(data, id=self._seq, kind=kind,
                         timestamp=datetime.now(timezone.utc).isoformat())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')
        return event


def read_trace(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            continue  # A reader can observe an unfinished final line during a write.
    return events


def trace_stats(events: list[dict]) -> dict:
    starts = [e for e in events if e['kind'] == 'model_started']
    ends = {e['call_id']: e for e in events if e['kind'] == 'model_finished'}
    stats = {}
    for role in ('small', 'strong'):
        calls = [e for e in starts if e['role'] == role]
        stats[role] = {'calls': len(calls),
                       'cost_cny': round(sum(ends.get(e['call_id'], {}).get('cost_cny', 0) for e in calls), 6),
                       'completed': sum(e['call_id'] in ends for e in calls),
                       'models': sorted({e['model'] for e in calls})}
    return stats


def event_preview(event: dict) -> dict:
    """The list stays light; full prompts, output and evidence load on expansion."""
    view = {k: v for k, v in event.items() if k not in ('messages', 'output', 'code', 'result')}
    if 'output' in event:
        view['excerpt'] = event['output'][:240]
    if 'result' in event:
        view['result'] = {k: v for k, v in event['result'].items() if k != 'evidence'}
    return view
