"""Install native collaboration cards and patch DeerFlow's chat message renderer."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install(checkout: Path) -> list[Path]:
    frontend = checkout.resolve()/'frontend'
    renderer = frontend/'src/components/workspace/messages/message-group.tsx'
    original = renderer.read_text(encoding='utf-8')
    import_line = 'import { ResearchCollaborationRuns } from "./research-collaboration";\n'
    element = '      <ResearchCollaborationRuns messages={messages} />'
    changed = original
    if import_line.strip() not in changed:
        anchor = 'import { ToolCallDetails } from "./tool-call-details";'
        if changed.count(anchor) != 1:
            raise ValueError('Unsupported DeerFlow message renderer; merge the native component manually.')
        changed = changed.replace(anchor, import_line+anchor)
    if element.strip() not in changed:
        anchor = '      {processingPanel}'
        if changed.count(anchor) != 1:
            raise ValueError('Could not locate the native processing panel.')
        changed = changed.replace(anchor, anchor+'\n'+element)
    source = ROOT/'integrations/deerflow/frontend'
    files = {
        frontend/'src/components/workspace/messages/research-collaboration.tsx':source/'research-collaboration.tsx',
        frontend/'src/core/messages/vela-research.ts':source/'research-events.ts',
        frontend/'src/core/i18n/locales/vela-research.ts':source/'research-copy.ts',
        frontend/'src/app/vela-proxy/[[...path]]/route.ts':source/'route.ts',
        frontend/'tests/unit/vela-research.test.ts':source/'research-events.test.ts',
        frontend/'tests/unit/vela-research.dom.test.tsx':source/'research-collaboration.dom.test.tsx',
    }
    for target, src in files.items():
        if not target.resolve().is_relative_to(frontend.resolve()):
            raise ValueError(f'Destination escapes frontend: {target}')
        if not src.is_file():
            raise FileNotFoundError(src)
    # These files are maintained by this adapter; never replace the upstream renderer wholesale.
    for target, src in files.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
    renderer.write_text(changed, encoding='utf-8')
    return [renderer, *files]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout',type=Path)
    for path in install(parser.parse_args().checkout):
        print(path)
