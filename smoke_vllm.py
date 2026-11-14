"""vLLM 冒烟测试：用 ProbeLab 自带的提示词样例和验证器检查本地模型。

用法（在仓库根目录，vLLM 已在 8000 端口启动）:
    python smoke_vllm.py --model local-vllm-model --n 8

测三件事:
  1. 格式/有效率：每个任务的 local 提示词采样 n 次，过 extract_candidate + 验证阶梯
  2. 是否真的关掉了思考：输出里是否还出现 <think>
  3. 吞吐：单请求生成速度，以及 K=1/2/4 并发时的总吞吐和单次延迟
"""
import argparse
import asyncio
import json
import statistics
import time
import math
import os
from pathlib import Path

from openai import AsyncOpenAI

from tierlab.sandbox import clear_sandbox_cache
from tierlab.tasks import get_task
from tierlab.types import Candidate

SAMPLES = Path(__file__).resolve().parent / "docs/prompt_samples"
OUTPUTS = Path("smoke_outputs")


def save_json(name, value):
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def latency_stats(results):
    latencies = sorted(r["latency_s"] for r in results)
    return {"median_s": statistics.median(latencies),
            "p95_s": latencies[max(0, math.ceil(.95 * len(latencies)) - 1)],
            "max_s": max(latencies),
            "over_30s": sum(t >= 30 for t in latencies)}


async def one_call(client, model, messages, max_tokens, temperature, extra_body):
    t0 = time.perf_counter()
    try:
        r = await client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens,
            temperature=temperature, extra_body=extra_body,
        )
    except Exception as error:
        return {"content": "", "finish": None, "out_tokens": 0, "in_tokens": 0,
                "latency_s": time.perf_counter() - t0,
                "error_type": type(error).__name__, "error": str(error)[:1000]}
    dt = time.perf_counter() - t0
    c = r.choices[0]
    return {
        "content": c.message.content or "",
        "reasoning_content": getattr(c.message, "reasoning_content", None) or getattr(c.message, "reasoning", None),
        "finish": c.finish_reason,
        "out_tokens": r.usage.completion_tokens if r.usage else 0,
        "in_tokens": r.usage.prompt_tokens if r.usage else 0,
        "latency_s": dt,
    }


async def format_check(client, model, task_name, n):
    sample = json.loads((SAMPLES / f"{task_name}_local.json").read_text(encoding="utf-8"))
    task = get_task(task_name)
    extra = sample.get("extra_body") or {"chat_template_kwargs": {"enable_thinking": False}}
    res = await asyncio.gather(*[
        one_call(client, model, sample["messages"], sample.get("max_tokens", 2048),
                 0.6 + 0.1 * (i % 4), extra)
        for i in range(n)
    ])
    # Save raw responses before verification, so a verifier interruption loses no generations.
    save_json(f"{task_name}.json", res)
    stats = {"valid": 0, "think_leak": 0, "truncated": 0, "empty": 0, "api_errors": 0, "sigs": {}, "scores": []}
    for i, r in enumerate(res):
        raw = r["content"]
        if "<think>" in raw.lower() or "</think>" in raw.lower() or r.get("reasoning_content"):
            stats["think_leak"] += 1
        if r["finish"] == "length":
            stats["truncated"] += 1
        if not raw.strip():
            stats["empty"] += 1
        clear_sandbox_cache()
        cand = Candidate(id=f"smoke_{task_name}_{i}", content=task.extract_candidate(raw))
        if r.get("error_type"):
            stats["api_errors"] += 1
            sig = "api/" + r["error_type"]
            stats["sigs"][sig] = stats["sigs"].get(sig, 0) + 1
            continue
        try:
            ladder = task.run_ladder(cand)
        except Exception as error:
            r["verification_error"] = f"{type(error).__name__}: {error}"
            sig = "verifier/exception"
            stats["sigs"][sig] = stats["sigs"].get(sig, 0) + 1
            save_json(f"{task_name}.json", res)
            continue
        r["verification"] = [v.model_dump() for v in ladder]
        last = ladder[-1] if ladder else None
        ok = bool(ladder) and all(v.valid for v in ladder)
        stats["valid"] += int(ok)
        sig = "ok" if ok else (last.failure_signature if last else "no_result")
        stats["sigs"][sig] = stats["sigs"].get(sig, 0) + 1
        if ok:
            stats["scores"].append(last.score)
        save_json(f"{task_name}.json", res)
    lat = [r["latency_s"] for r in res]
    toks = [r["out_tokens"] for r in res]
    print(f"\n[{task_name}] valid {stats['valid']}/{n}  think_leak={stats['think_leak']}  "
          f"truncated={stats['truncated']}  empty={stats['empty']}")
    print(f"   failure signatures: {stats['sigs']}")
    if stats["scores"]:
        print(f"   valid scores: min={min(stats['scores']):.4f} max={max(stats['scores']):.4f}")
    print(f"   out tokens median={statistics.median(toks):.0f}  latency median={statistics.median(lat):.1f}s "
          f"(concurrency {n})")
    stats.update(latency_stats(res))
    stats.update({"n": n, "valid_rate": stats["valid"] / n})
    print(f"   latency p95={stats['p95_s']:.1f}s max={stats['max_s']:.1f}s over_30s={stats['over_30s']} api_errors={stats['api_errors']}")
    save_json(f"{task_name}_summary.json", stats)
    return stats


async def throughput(client, model):
    msgs = [{"role": "user", "content": "Write a long, detailed Python tutorial about numpy broadcasting."}]
    extra = {"chat_template_kwargs": {"enable_thinking": False}}
    print("\n[throughput] fixed 2048-token generations (ignore_eos)")
    measurements = []
    for k in (1, 2, 4):
        t0 = time.perf_counter()
        res = await asyncio.gather(*[
            one_call(client, model, msgs, 2048, 0.7, {**extra, "ignore_eos": True}) for _ in range(k)
        ])
        wall = time.perf_counter() - t0
        tot = sum(r["out_tokens"] for r in res)
        per = statistics.median(r["out_tokens"] / r["latency_s"] for r in res)
        print(f"   K={k}: wall={wall:.1f}s  total={tot/wall:.0f} tok/s  per-request={per:.0f} tok/s")
        measurement = {"k": k, "wall_s": wall, "total_tokens": tot, "total_tokens_per_s": tot / wall,
                       "api_errors": sum(bool(r.get('error_type')) for r in res),
                       **latency_stats(res), "responses": res}
        print(f"        latency median={measurement['median_s']:.1f}s p95={measurement['p95_s']:.1f}s max={measurement['max_s']:.1f}s")
        measurements.append(measurement)
        save_json("throughput.json", measurements)
    return measurements


async def main():
    global OUTPUTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--model", default="local-vllm-model")
    ap.add_argument("--api-key", default=os.environ.get("LOCAL_API_KEY", "EMPTY"))
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--tasks", default="math,code,paper")
    ap.add_argument("--skip-throughput", action="store_true")
    ap.add_argument("--output-dir", type=Path, default=Path("smoke_outputs"))
    ap.add_argument("--timeout", type=float, default=300.0, help="Request timeout; SDK retries are disabled")
    a = ap.parse_args()
    if a.n < 1:
        ap.error("--n must be positive")
    tasks = [t.strip() for t in a.tasks.split(",") if t.strip()]
    if not tasks or any(t not in ("math", "code", "paper") for t in tasks):
        ap.error("--tasks must contain math, code and/or paper")
    OUTPUTS = a.output_dir
    async with AsyncOpenAI(base_url=a.base_url, api_key=a.api_key, timeout=a.timeout, max_retries=0) as client:
        for t in tasks:
            await format_check(client, a.model, t, a.n)
        if not a.skip_throughput:
            await throughput(client, a.model)
    print(f"\nOutputs: {OUTPUTS.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
