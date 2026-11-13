---
name: tierlab-research
description: Trigger and monitor TierLab autonomous research experiments balancing local GPU saturation and cloud model escalation.
---

# TierLab Research Skill

Use this skill when the user requests deep iterative research, algorithm optimization, or verification-guided scientific exploration.

## Capabilities

TierLab delegates candidate generation to high-throughput local GPU instances (vLLM) and only escalates to cloud flagship models when local progress plateaus.

1. **Start Research**: Initiates a background run on tasks such as math optimization, code performance acceleration, or quote-grounded paper QA.
2. **Check Status**: Polls the research loop progress and current verification score.
3. **Inspect Report**: Fetches the interactive HTML dashboard detailing the Cost-Time-Score frontier and decision ledger replay.

## Workflow

1. Call `start_research(task, policy, budget)` to obtain a `run_id`.
2. Inform the user that the experiment is running asynchronously.
3. Periodically call `get_research_status(run_id)` until status is `completed`.
4. Render summary metrics (final score, cloud spend in ¥, BA-AUC) and surface the report artifact.

## Notes & Verification
- DeerFlow connects to TierLab via the lightweight HTTP API (`tierlab serve`), avoiding core dependency conflicts.
<!-- TODO(verify: DeerFlow skill loader and tool decorator format in final deployment environment) -->
