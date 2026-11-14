"""DeerFlow tools with bounded waiting and thread-local report artifacts."""
from pathlib import Path
from langchain.tools import ToolRuntime, tool
from .client import start_research, get_research_status, get_research_report

start_research_tool = tool(start_research)
get_research_status_tool = tool(get_research_status)

@tool('get_research_report')
def get_research_report_tool(run_id: str, runtime: ToolRuntime) -> dict:
    """Save the completed VELA report into this chat's outputs.

    After success call present_files with filepaths=[artifact_path] to open the native Artifacts
    panel. The full report HTML is kept out of the model's token context.
    """
    status = get_research_status(run_id)
    if status.get('status') != 'completed':
        return {'run_id': run_id, 'status': status.get('status'), 'error': 'Research is not completed yet.'}
    outputs = runtime.state.get('thread_data', {}).get('outputs_path')
    if not outputs:
        raise ValueError('Thread output directory is missing from DeerFlow runtime.')
    html = get_research_report(run_id)
    directory = Path(outputs).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    filename = f'vela-{run_id}.html'
    (directory / filename).write_text(html, encoding='utf-8')
    return {'run_id': run_id, 'status': 'completed', 'summary': status.get('summary'),
            'artifact_path': f'/mnt/user-data/outputs/{filename}',
            'next_action': 'Call present_files with filepaths=[artifact_path], then summarize the verified results.'}
