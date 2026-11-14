"""Prepare a reproducible Docker build context; never extract upstream Python on the host."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
import uuid
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '3e2489429ff3061489537d7303240fef3f199f83'
ARCHIVE_SHA256 = '0fd319b5bda948f93ac32ea026ff519dad0fdc0ca213b6593e98eb7c1f779a0e'
IMAGE = 'vela-deerflow-demo:3e248942'


def prepare_context(archive: Path, destination: Path) -> Path:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise ValueError('Upstream archive checksum differs from the validated source; no build context created.')
    with zipfile.ZipFile(archive) as source:
        if not all(n.startswith(f'deer-flow-{COMMIT}/') for n in source.namelist()):
            raise ValueError('Unexpected archive root')
    integration = ROOT/'integrations/deerflow'
    config = yaml.safe_load((integration/'config.demo.yaml').read_text(encoding='utf-8'))
    if not all(str(m.get('api_key', '')).startswith('$') for m in config['models']):
        raise ValueError('Model keys must be environment references, never literal credentials.')
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(archive, destination/'source.zip')
    shutil.copy2(integration/'Dockerfile.demo', destination/'Dockerfile')
    shutil.copy2(integration/'config.demo.yaml', destination/'config.yaml')
    (destination/'integration').mkdir()
    for name in ('tools.py', 'registered_tools.py', 'SKILL.md'):
        shutil.copy2(integration/name, destination/'integration'/name)
    for name in ('src', 'configs', 'data'):
        shutil.copytree(ROOT/name, destination/'research'/name,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.env', '.env.*'))
    (destination/'build-manifest.json').write_text(json.dumps(
        {'deerflow_commit':COMMIT, 'archive_sha256':digest, 'image':IMAGE}, indent=2)+'\n', encoding='utf-8')
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use an already downloaded pinned source.zip.')
    parser.add_argument('--build', action='store_true', help='Also invoke docker build.')
    args = parser.parse_args()
    cache = ROOT/'runs/deerflow-build'
    cache.mkdir(parents=True, exist_ok=True)
    archive = args.archive or cache/f'{COMMIT}.zip'
    if not archive.is_file():
        if args.archive:
            raise FileNotFoundError(archive)
        temporary = cache/f'download-{uuid.uuid4().hex}.part'
        request = urllib.request.Request(f'https://codeload.github.com/bytedance/deer-flow/zip/{COMMIT}',
                                         headers={'User-Agent':'VELA-demo-build'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        temporary.replace(archive)
    destination = prepare_context(archive, cache/f'context-{uuid.uuid4().hex[:10]}')
    print('Prepared:', destination)
    if args.build:
        subprocess.run(['docker','build','-t',IMAGE,str(destination)],check=True)
    else:
        print(f'docker build -t {IMAGE} "{destination}"')


if __name__ == '__main__':
    main()
