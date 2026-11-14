# Cloud API fallback for the weak model

The slow 35B weight download was paused. The already installed 4090 software
environment is retained, but the following tests use SiliconFlow rather than
local GPU inference.

- Weak model: `Qwen/Qwen3.6-35B-A3B`, confirmed in the authenticated model list.
- Strong advisor: `deepseek-ai/DeepSeek-V4.1-Flash`, already verified with one hint.
- Provider-managed Qwen precision is not established by these calls. These
  results are not equivalent to the local W4A16 AWQ deployment.
- Both model endpoints are cloud-billed. The provided API configuration sets
  GPU computation and GPU lease accounting to zero; any separately rented,
  idle GPU instance remains an external expense.

## Run the smoke test

Set `SILICONFLOW_API_KEY` in the process environment, then run from the repository:

```bash
pip install -e ".[dev]"
python smoke_vllm.py --provider siliconflow \
  --model Qwen/Qwen3.6-35B-A3B \
  --n 8 --concurrency 2 --json-code --max-tokens 4096 --timeout 90 \
  --prompt-cny-per-m 1.8 --completion-cny-per-m 10.8 \
  --skip-throughput --output-dir smoke_outputs/siliconflow_qwen35_jsoncode
```

The key is never written into request samples or committed configuration.
Actual task requests are saved as `<task>_request.json`, including messages,
provider parameters, token limit and concurrency. API errors and timeouts are
retained separately from invalid candidates. SDK retries are disabled; real
gateway retries are controlled only by the endpoint configuration.

`--json-code` adds an explicit JSON code-envelope instruction for math/code
and requests `response_format: {type: json_object}`. The model's `code` string
is extracted and scored by the same verifier ladder. Paper keeps its original
answers JSON format. The frozen prompt samples are not edited. This option
is necessary to distinguish the tested request variant from the original
unconstrained prompt.

Without `--skip-throughput`, the script also measures API concurrency K=1/2/4.
The provider is not sent vLLM's `ignore_eos` or `chat_template_kwargs` fields.
These are capped, variable-length API generations, not a 4090 benchmark.

## Run the actual controller

```bash
tierlab run --task math --config configs/api_smoke.yaml --dump-prompts
tierlab run --task code --config configs/api_smoke.yaml --dump-prompts
```

`configs/api_smoke.yaml` selects `models.siliconflow.yaml`, the real backend,
`weak_backend=cloud_small`, K in `[1,2]`, 4096 weak-model output tokens, a
400-token hint cap and a one-CNY per-run budget. It limits runs to four steps.
Use `--models-config` to explicitly select another endpoint/pricing YAML.
The default mock configuration and the placeholder-price startup guard remain intact.

Both strong and weak code-generation requests use the code envelope. Hints,
diagnoses and plans remain free-text advice, and paper questions retain the
grading-field whitelist. Budget reservation includes the formatting instruction.

## Observed issues before tuning

- Original math prompt at 2048 tokens: 0/8 accepted; all eight hit the output
  limit while producing explanations instead of complete code.
- Increasing the original prompt to 4096 tokens: two requests timed out at
  approximately 125 seconds. A larger cap alone did not resolve the problem.
- Concurrency 8: all eight code requests and five paper requests returned
  provider HTTP 503 (`System is too busy now`). These are API errors, not
  verifier failures. Default SiliconFlow smoke concurrency is now 2.
- Low-concurrency code: both candidates passed correctness; one was accepted
  at 1.4156x and a genuine NumPy implementation was rejected by the existing
  50x anomaly threshold (about 93.6x measured speedup). That rejection should
  not be interpreted as evidence of model cheating.
- Low-concurrency paper: 2/2 valid outputs, each with development score 0.5.
  This is synthetic fixture material, not a real paper benchmark.
- A separate 4096-token JSON-envelope math probe passed in 26.69 seconds
  with score 0.87476. This single successful probe is not a success-rate estimate.
- API throughput probe: K=1/2/4 produced approximately 60/113/220 aggregate
  tokens/s, with request latencies approximately 34.0/36.3/37.2 seconds.
  These measurements include provider and network behavior.

Full raw replies are kept under the ignored `smoke_outputs/` directory.
Returned-usage cost estimates exclude unreturned usage from timed-out calls;
their actual billing cannot be inferred as zero.

## Latest measured results

The JSON-envelope run used eight samples per task, concurrency 2 and a 4096-token
cap. Generation used the cloud API; verification ran on the Windows client's CPU.
Full machine-readable results: [api_smoke_results.json](api_smoke_results.json).

| Task | Full development ladder valid | Median / P95 request latency | Notes |
| --- | --- | --- | --- |
| Math | 1/8 | 40.1 / 49.6 s | 3 truncated, 2 overlaps, 2 complete functions returned no list |
| Code | 6/8 | 6.0 / 14.8 s | Correctness 8/8; two candidates rejected by timing guard |
| Paper | 8/8 | 3.1 / 4.6 s | Synthetic dev scores ranged from 0.5 to 1.0 |

This run had zero API errors, zero observed think tags/reasoning fields and
zero reported reasoning tokens. Mathematical validity remains below the
original >50% acceptance criterion, so these data do not establish readiness
for a formal math strategy comparison. Passing the paper ladder does not mean
every answer was correct, as shown by the separate scores.

Two real-controller connectivity runs then used the same endpoint configuration
for two controller iterations each:

| Task | Weak / strong calls | Hint / solve actions | Cloud CNY | Final evaluation score |
| --- | --- | --- | ---: | ---: |
| Math | 2 / 1 | 1 / 0 | 0.076832 | 0.74 |
| Code | 2 / 2 | 1 / 1 | 0.035369 | 0.8807 |

Both finished normally at the step limit. Both recorded zero local model calls,
zero GPU busy seconds and zero model GPU cost. They confirm dispatch, feedback,
advice, extraction, verification and accounting connectivity; they do not show
verified optimization improvements. `cloud_calls` includes both weak and strong
calls in this backend, even for policies whose historical names include "local".

The latest 24-sample run plus the two short controller runs account for about
0.483 CNY of returned-usage cost. Earlier calibration, throughput and timed-out
requests are separate and are not included in that figure. Full code tests:
113 passed, one environment warning.

Pricing source: [SiliconFlow official price page](https://www.siliconflow.cn/pricing).
Normal-period prices used here are 1.8/10.8 CNY per million input/output tokens
for Qwen and 2.0/8.0 for DeepSeek V4.1 Flash, with DeepSeek cache-hit input at
0.04. DeepSeek has time-of-day discounts; recheck pricing before formal experiments.
