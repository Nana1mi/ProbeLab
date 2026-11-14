import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest


def load_builder():
    path = Path(__file__).parents[1]/'scripts/prepare_deerflow_demo.py'
    spec = importlib.util.spec_from_file_location('demo_builder',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_context_uses_pinned_source_and_excludes_local_environment(tmp_path,monkeypatch):
    builder = load_builder()
    archive = tmp_path/'source.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr(f'deer-flow-{builder.COMMIT}/README.md','pinned source')
    monkeypatch.setattr(builder,'ARCHIVE_SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    root = tmp_path/'project'
    integration=root/'integrations/deerflow'; integration.mkdir(parents=True)
    for name in ['tools.py','registered_tools.py','SKILL.md','Dockerfile.demo']:
        (integration/name).write_text(name)
    (integration/'config.demo.yaml').write_text('models:\n- api_key: $SILICONFLOW_API_KEY\n')
    for name in ['src','configs','data']:
        folder=root/name; folder.mkdir(); (folder/'example.txt').write_text(name)
        (folder/'.env').write_text('private-environment')
    monkeypatch.setattr(builder,'ROOT',root)
    result=builder.prepare_context(archive,tmp_path/'context')
    assert (result/'source.zip').read_bytes()==archive.read_bytes()
    assert (result/'research/src/example.txt').is_file()
    assert not (result/'research/src/.env').exists()
    assert not (result/f'deer-flow-{builder.COMMIT}').exists()  # Never extracted on host.


def test_build_rejects_unverified_archive_before_creating_output(tmp_path):
    builder=load_builder()
    archive=tmp_path/'bad.zip'; archive.write_bytes(b'unverified')
    with pytest.raises(ValueError,match='checksum'):
        builder.prepare_context(archive,tmp_path/'context')
    assert not (tmp_path/'context').exists()
