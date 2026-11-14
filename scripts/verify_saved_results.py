"""Verify archived result bytes without calling models or executing candidates."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    manifest = json.loads((ROOT/'docs/RESULTS_SHA256.json').read_text(encoding='utf-8'))
    failed = []
    for name, expected in manifest['files'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            failed.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failed.append(name)
    if failed:
        raise SystemExit('Missing or modified result files:\n'+'\n'.join(failed))
    print(f"Verified {len(manifest['files'])} archived result files; no model calls made.")


if __name__ == '__main__':
    main()
