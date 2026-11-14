"""Start an isolated DeerFlow Gateway and VELA engine for a local Agent demo.

Requires the vela-deerflow-demo:3e248942 Docker image and frontend dependencies.
The key is supplied through the environment or a hidden prompt, never a config file.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    parser.add_argument('--gateway-only', action='store_true', help='Reuse the already running VELA research service.')
    parser.add_argument('--engine-port', type=int, default=18766, help='Local published port for the research service.')
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    inspected = subprocess.run(['docker','image','inspect','vela-deerflow-demo:3e248942'],capture_output=True)
    if inspected.returncode:
        raise SystemExit('Build the isolated DeerFlow image before starting this demo.')
    env = os.environ.copy()
    if not env.get('SILICONFLOW_API_KEY'):
        env['SILICONFLOW_API_KEY'] = getpass.getpass('SiliconFlow API key: ')
    env.update(DEER_FLOW_PROJECT_ROOT=str(checkout), DEER_FLOW_CONFIG_PATH=str(checkout/'config.yaml'),
        DEER_FLOW_HOME=str(checkout/'.deer-flow'), DEER_FLOW_AUTH_DISABLED='1',
        TIERLAB_SERVER_URL=f'http://127.0.0.1:{args.engine_port}', PYTHONIOENCODING='utf-8', PYTHONUTF8='1',
        DEER_FLOW_DATE_TIMEZONE='Asia/Hong_Kong')
    logs = ROOT/'runs/agent-demo'
    logs.mkdir(parents=True,exist_ok=True)
    # Keep operator behavior in a reviewable source file; keys stay as env references.
    base_config = subprocess.run(
        ['docker', 'run', '--rm', '--network=none', '--read-only', '--cap-drop=ALL',
         '--security-opt=no-new-privileges', '--entrypoint', '/bin/cat',
         'vela-deerflow-demo:3e248942', '/app/config.yaml'],
        check=True, capture_output=True, encoding='utf-8')
    config = yaml.safe_load(base_config.stdout)
    config.setdefault('lead_prompt_overlay', {})['append'] = (
        ROOT/'integrations/deerflow/lead_prompt.md').read_text(encoding='utf-8')
    demo_config = logs/'config.yaml'
    demo_config.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    processes = []
    if args.gateway_only and (logs/'processes.json').is_file():
        processes = [p for p in json.loads((logs/'processes.json').read_text()) if p['name']=='engine']
    network = 'vela-agent-demo-local'
    if subprocess.run(['docker','network','inspect',network],capture_output=True).returncode:
        subprocess.run(['docker','network','create',network],check=True,capture_output=True)
    commands = [
        ('engine',['docker','run','--rm','--name','vela-research-engine','--network',network,
            '--mount',f'type=bind,source={ROOT / "src"},target=/research/src,readonly',
            '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=256',
            '--memory=2g','--cpus=2','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=512m',
            '-p',f'127.0.0.1:{args.engine_port}:8766','-v','vela-research-state:/research/runs',
            '-w','/research','-e','SILICONFLOW_API_KEY','-e','MPLCONFIGDIR=/tmp/matplotlib',
            '--entrypoint','/app/backend/.venv/bin/python','vela-deerflow-demo:3e248942',
            '-m','uvicorn','tierlab.server:app','--app-dir','/research/src','--host','0.0.0.0','--port','8766'],ROOT),
        ('gateway',['docker','run','--rm','--name','vela-agent-gateway',
            '--mount',f'type=bind,source={demo_config.resolve()},target=/app/config.yaml,readonly',
            '--mount',f'type=bind,source={ROOT / "integrations/deerflow/SKILL.md"},target=/app/skills/custom/vela-research/SKILL.md,readonly',
            '--mount',f'type=bind,source={ROOT / "integrations/deerflow/tools.py"},target=/app/backend/vela_integration/client.py,readonly',
            '--network',network,'-e','TIERLAB_SERVER_URL=http://vela-research-engine:8766',
            '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=256',
            '--memory=2g','--cpus=2','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=256m',
            '-p','127.0.0.1:8001:8001','-v','vela-agent-state:/state',
            '-e','SILICONFLOW_API_KEY','vela-deerflow-demo:3e248942'],ROOT),
    ]
    for name,cmd,cwd in (commands[1:] if args.gateway_only else commands):
        with (logs/f'{name}.log').open('w',encoding='utf-8') as log:
            process = subprocess.Popen(cmd,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
        processes.append({'name':name,'pid':process.pid})
    (logs/'processes.json').write_text(json.dumps(processes,indent=2),encoding='utf-8')
    print('Started engine and Gateway. Logs:',logs)
    print(f'Start the DeerFlow frontend with TIERLAB_SERVER_URL=http://127.0.0.1:{args.engine_port} and Gateway at port 8001.')
    print('Demo mode bypasses login on loopback only; the Agent model uses real API calls.')


if __name__ == '__main__':
    main()
