"""Install the VELA page, proxy, and optional tools into a DeerFlow checkout."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(checkout: Path) -> list[Path]:
    checkout = checkout.resolve()
    app = checkout / 'frontend/src/app'
    if not (app/'workspace/layout.tsx').is_file() or not (checkout/'backend').is_dir():
        raise ValueError('Expected a DeerFlow 2.x checkout with frontend/src/app/workspace/layout.tsx and backend/.')
    source = ROOT/'integrations/deerflow'
    files = {
        app/'workspace/vela/page.tsx': (source/'frontend/page.tsx').read_bytes(),
        app/'vela-proxy/[[...path]]/route.ts': (source/'frontend/route.ts').read_bytes(),
        checkout/'backend/vela_integration/__init__.py': b'',
        checkout/'backend/vela_integration/client.py': (source/'tools.py').read_bytes(),
        checkout/'backend/vela_integration/tools.py': (source/'registered_tools.py').read_bytes(),
        checkout/'skills/custom/vela-research/SKILL.md': (source/'SKILL.md').read_bytes(),
    }
    # Preflight all paths before copying; preserve pre-existing different files.
    for path, content in files.items():
        if not path.resolve().is_relative_to(checkout):
            raise ValueError(f'Destination escapes checkout: {path}')
        if path.exists() and path.read_bytes() != content:
            raise FileExistsError(f'Existing customized file needs manual merge: {path}')
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return list(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    args = parser.parse_args()
    for file in install(args.checkout):
        print(file)
    print('Set TIERLAB_SERVER_URL in DeerFlow, restart it, then open /workspace/vela.')
