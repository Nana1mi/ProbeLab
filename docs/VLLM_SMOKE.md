# 4090D vLLM smoke test

## Confirmed hardware and current status

2026-11-14: SSH inventory confirmed RTX 4090 D, 24564 MiB VRAM,
driver 550.90.07 (reported CUDA 12.4), 58 GiB RAM, and about 84 GiB free
on the system disk. `/root/autodl-tmp` does not exist on this instance.
There were no running GPU jobs and port 8000 was unused.

The runtime directory is `/root/probelab-runtime/`. Python 3.11.18 and a
separate download environment were installed. A background installation of
vLLM 0.26.0's official CUDA 12.9 wheel was started, with status in
`logs/install-vllm.log` and eventual exit code in `logs/install.exit`.
SSH connectivity was lost before completion could be verified. These are
setup observations, not completed model/GPU smoke results.

## Installation choices

Do not install an unpinned latest CUDA 13 wheel on this driver. The chosen
35B quantization requires vLLM >= 0.26.0; simply downgrading vLLM can remove
support for its `Qwen3_5MoeForConditionalGeneration` architecture.

The official v0.26.0 CUDA 12.9 variant is:

```text
https://github.com/vllm-project/vllm/releases/download/v0.26.0/vllm-0.26.0%2Bcu129-cp38-abi3-manylinux_2_28_x86_64.whl
```

Use its matching PyTorch CUDA 12.9 builds in a fresh Python 3.11 environment.
CUDA 12 minor compatibility permits some newer 12.x binaries on this driver,
but PTX and newer driver features can still fail. Verify both a GPU tensor
operation and actual vLLM startup. An installed wheel alone is not a pass.

If that stack cannot run, the user-approved fallback is `Qwen/Qwen3-14B-AWQ`
with a compatible vLLM >= 0.8.5 CUDA 12.4 build. Check the actual PyTorch
runtime version rather than relying on the image's CUDA label.

Sources: [35B quantization model card](https://huggingface.co/hyperhuzaifa/Qwen3.6-35B-A3B-W4A16-AWQ),
[vLLM GPU installation](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/),
[CUDA minor compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/latest/minor-version-compatibility.html),
[14B model requirements](https://huggingface.co/Qwen/Qwen3-14B-AWQ).

## Start the model

After installing compatible packages and downloading the weights:

```bash
source /root/probelab-runtime/venv/bin/activate
export HF_HOME=/root/probelab-runtime/hf
vllm serve hyperhuzaifa/Qwen3.6-35B-A3B-W4A16-AWQ \
  --served-model-name local-vllm-model \
  --host 127.0.0.1 --port 8000 \
  --max-model-len 16384 --max-num-seqs 8 \
  --kv-cache-dtype fp8 --gpu-memory-utilization 0.92 \
  --enable-prefix-caching
```

For a local weight directory, replace the model repository argument with that
directory. Check `nvidia-smi`, service logs and `/v1/models` after startup.
The localhost-only bind is sufficient for tests executed on the server.

## Run the smoke test

From the ProbeLab checkout, after installing the project in the same environment:

```bash
pip install -e .
python smoke_vllm.py --model local-vllm-model --n 8 \
  --tasks math,code,paper --output-dir smoke_outputs/first_run
```

The script reads actual exported local prompts, extracts each generated
candidate and runs the repository's development verifier ladder. It saves:

- `<task>.json`: raw replies, API failures, and per-level verification results;
- `<task>_summary.json`: valid rate, signatures, reasoning leakage, truncation,
  empty replies, API errors, median/P95/maximum latency and requests >= 30 s;
- `throughput.json`: K=1/2/4 results with raw replies and latency distributions.

SDK retries are disabled to keep failures and timing visible. The request
timeout defaults to 300 s, so the 30 s compatibility target is measured rather
than silently truncating all slow samples. Use `--timeout` to change it.
Raw replies are saved before verification, and one failed API request does not
discard the rest of its batch. Repeated runs should use separate output folders.

Acceptance checks: math/code valid rates above 50%, no reasoning leakage,
and K=4 single-request latency comfortably below the project's 30 s local
timeout. Prefer P95/maximum to the median alone. For slow service, lower output
tokens/concurrency or adjust the local endpoint timeout before real experiments.
Paper currently has synthetic test material and is only a plumbing check.

## Cloud hint probe

The confirmed model ID is `deepseek-ai/DeepSeek-V4.1-Flash` (not the older
V4-Flash ID in the pasted curl example). One real 400-token-cap hint request
with top-level `enable_thinking=false` returned HTTP 200 in 5.3 s, using
539 input and 363 output tokens, with zero reasoning tokens.

The [provider price page](https://www.siliconflow.cn/pricing) on 2026-11-14 lists
daytime input/output/cache-hit prices of 2.00/8.00/0.04 CNY per million tokens,
with discounted prices between 02:00 and 08:00 Beijing time. This probe's
calculated cost was 0.003982 CNY. Recheck prices when configuring formal runs.
No API key is stored in this guide or the repository.
