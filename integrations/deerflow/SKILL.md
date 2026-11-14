---
name: vela-research
description: Start and inspect VELA verifier-guided research experiments through an independent research service.
---

# VELA Research Skill

Use this skill when the user requests deep iterative research, algorithm optimization, or verification-guided scientific exploration.

## Capabilities

VELA delegates candidate generation to a small model and escalates to a strong
model when the controller's policy requires it. The current demo uses API
endpoints for both models; local vLLM deployment is another supported setup.

1. **Start Research**: Initiates a background run on tasks such as math optimization, code performance acceleration, or quote-grounded paper QA.
2. **Check Status**: Polls the research loop progress and current verification score.
3. **Inspect Report**: Fetches the interactive HTML dashboard detailing the Cost-Time-Score frontier and decision ledger replay.

## Workflow

1. Accept an ordinary-language task. Map it to a supported fixed benchmark and
   call `start_research` to obtain a `run_id`; do not ask users to write tool
   instructions. Supported definitions are 26-circle unit-square packing,
   bundled cosine-similarity Top-K optimization, and bundled synthetic paper QA.
   Arbitrary uploaded code, new papers, and changed problem definitions are not
   passed through this API. Disclose mismatches rather than silently substituting.
2. Inform the user that the experiment is running asynchronously.
3. Call `get_research_status(run_id, wait_seconds=90)` for bounded waiting (at most
   120 seconds per call). Repeat while running and stop on completed, failed, or
   interrupted. The native collaboration card displays progress during the wait;
   short repeated queries waste conversational model calls and can trigger the
   upstream loop detector. Do not end a requested experiment after one wait.
4. On completion call `get_research_report(run_id)`, then `present_files(filepaths=[artifact_path])` to open the native Artifacts panel. Summarize score, cloud spend, elapsed time, and stopping reason from returned evidence.

## Notes & Verification
- DeerFlow connects to TierLab via the lightweight HTTP API (`tierlab serve`), avoiding core dependency conflicts.
- Ordinary supported tasks use real models by default: `backend=real`,
  `models_profile=siliconflow`, `weak_backend=cloud_small`, `policy=tierlab`.
  Operator demo limits are 6 steps, 240 seconds, and CNY 0.5 research budget.
  Use mock only when explicitly requested. Mock scores and charges are synthetic.
- A skill supplies instructions; tool registration is separate. Merge the tools listed in README.md into DeerFlow config.yaml.
- Tool HTTP transport does not import or execute the VELA controller inside DeerFlow.
