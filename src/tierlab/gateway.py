"""Unified Model Gateway for Local (vLLM) and Cloud (SiliconFlow/OpenAI-compatible) APIs.

Enforces:
  1. Usage tracking (prompt, cached, completion tokens)
  2. Exact cost computation in CNY based on models.yaml pricing
  3. Per-call wall-clock tracking
  4. Exponential backoff retries on transient errors
  5. HARD BUDGET CAP: Cloud requests exceeding remaining budget are rejected with BudgetExceededError
  6. Transparent pluggability between real AsyncOpenAI and MockLLMBackend
  7. Weak model backend can be routed to local vLLM or cloud_small API (#8)
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field

from openai import AsyncOpenAI, APIConnectionError, RateLimitError, InternalServerError

from tierlab.config import load_models_config
from tierlab.exceptions import BudgetExceededError, ConfigurationError
from tierlab.mock_llm import MockLLMBackend
from tierlab.prompts import json_code_request
from tierlab.trace import ResearchTrace
from tierlab.types import (
    Budget,
    ModelEndpointConfig,
    ModelsConfigFile,
    PricingConfig,
)


class GatewayCallRecord(BaseModel):
    target: str  # "local", "cloud", or "cloud_small"
    model_name: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    wall_clock_s: float = 0.0
    content: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class ModelGateway:
    """Central gateway through which all LLM invocations must pass."""

    def __init__(
        self,
        models_config: ModelsConfigFile | None = None,
        budget: Budget | None = None,
        backend: str = "mock",
        weak_target: str = "local",  # "local" or "cloud_small" (#8)
        mock_backend: MockLLMBackend | None = None,
        prompt_dump_dir: str | Path | None = None,
    ) -> None:
        self.config = (models_config or load_models_config()).model_copy(deep=True)
        self.budget = budget or Budget()
        self.backend = backend.lower().strip()
        self.weak_target = weak_target.lower().strip()
        self.mock_backend = mock_backend or MockLLMBackend()
        self.prompt_dump_dir = Path(prompt_dump_dir) if prompt_dump_dir else None
        self.request_count = 0
        self.trace: ResearchTrace | None = None
        self.trace_context: dict[str, Any] = {}
        for label in ('cloud_model', 'local_model', 'cloud_small_model'):
            endpoint = getattr(self.config, label)
            if endpoint is None:
                continue
            missing = any(v is None for v in endpoint.pricing.model_dump().values())
            if missing and self.backend == 'real':
                raise ConfigurationError(f"Configure explicit pricing for {label}; null prices are placeholders.")
            if missing:
                # Mock prices are synthetic, never a claim about a provider's current rates.
                defaults = (0.0, 0.0, 0.0) if label == 'local_model' else (2.0, 0.5, 8.0)
                for field, value in zip(PricingConfig.model_fields, defaults):
                    if getattr(endpoint.pricing, field) is None:
                        setattr(endpoint.pricing, field, value)

        self.spent_cloud_cny: float = 0.0
        self.reserved_cloud_cny: float = 0.0
        self.total_cloud_prompt_tokens: int = 0
        self.total_cloud_cached_tokens: int = 0
        self.total_cloud_completion_tokens: int = 0
        self.call_history: list[GatewayCallRecord] = []

        # Real OpenAI clients
        self._cloud_client: AsyncOpenAI | None = None
        self._local_client: AsyncOpenAI | None = None
        self._cloud_small_client: AsyncOpenAI | None = None

        if self.backend == "real":
            self._init_real_clients()

    def _init_real_clients(self) -> None:
        cloud_key = os.environ.get(self.config.cloud_model.api_key_env, "placeholder_key")
        local_key = os.environ.get(self.config.local_model.api_key_env, "EMPTY")

        self._cloud_client = AsyncOpenAI(
            base_url=self.config.cloud_model.base_url,
            api_key=cloud_key,
            timeout=self.config.cloud_model.timeout_s,
            max_retries=0,  # The gateway's endpoint retry policy is the single owner.
        )
        self._local_client = AsyncOpenAI(
            base_url=self.config.local_model.base_url,
            api_key=local_key,
            timeout=self.config.local_model.timeout_s,
            max_retries=0,
        )
        if self.config.cloud_small_model:
            small_key = os.environ.get(self.config.cloud_small_model.api_key_env, "placeholder_key")
            self._cloud_small_client = AsyncOpenAI(
                base_url=self.config.cloud_small_model.base_url,
                api_key=small_key,
                timeout=self.config.cloud_small_model.timeout_s,
                max_retries=0,
            )

    def calculate_cost(
        self,
        pricing: PricingConfig,
        prompt_tokens: int,
        cached_tokens: int,
        completion_tokens: int,
    ) -> float:
        if any(v is None for v in pricing.model_dump().values()):
            raise ConfigurationError('Explicit token pricing is required')
        uncached_prompt = max(0, prompt_tokens - cached_tokens)
        cost = (
            (uncached_prompt * pricing.prompt_cny_per_m / 1_000_000.0)
            + (cached_tokens * pricing.cached_cny_per_m / 1_000_000.0)
            + (completion_tokens * pricing.completion_cny_per_m / 1_000_000.0)
        )
        return cost

    def reserve_budget(self, amount: float) -> None:
        """Reserve worst-case cloud budget before concurrent K-way generation (#15)."""
        remaining = self.budget.cloud_budget_cny - (self.spent_cloud_cny + self.reserved_cloud_cny)
        if amount > (remaining + 1e-6):
            raise BudgetExceededError(
                f"Concurrent budget reservation rejected: requested {amount:.5f} CNY "
                f"exceeds remaining unreserved budget {remaining:.5f} CNY "
                f"(spent={self.spent_cloud_cny:.5f}, reserved={self.reserved_cloud_cny:.5f}, "
                f"total={self.budget.cloud_budget_cny:.5f})."
            )
        self.reserved_cloud_cny += amount

    def release_budget(self, amount: float) -> None:
        """Release reservation after concurrent calls complete (#15)."""
        self.reserved_cloud_cny = max(0.0, self.reserved_cloud_cny - amount)

    def estimate_call_worst_case(
        self,
        target: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        task_class: str | None = None,
    ) -> float:
        """Estimate worst-case cost for a call based on target and messages (#15)."""
        endpoint, resolved = self._resolve_endpoint(target)
        if resolved not in ("cloud", "cloud_small"):
            return 0.0
        if endpoint.candidate_format == 'json_code' and task_class != 'paper':
            messages, _ = json_code_request(messages)
        return self._estimate_worst_case_cost(endpoint.pricing, messages, max_tokens)

    def _estimate_worst_case_cost(
        self,
        pricing: PricingConfig,
        messages: list[dict[str, str]],
        max_tokens: int | None,
    ) -> float:
        estimated_prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 3 + 60
        expected_completion = max_tokens if max_tokens is not None else 2048
        return self.calculate_cost(
            pricing=pricing,
            prompt_tokens=estimated_prompt_tokens,
            cached_tokens=0,
            completion_tokens=expected_completion,
        )

    def _resolve_endpoint(self, target: str) -> tuple[ModelEndpointConfig, str]:
        if target in ("local", "weak"):
            if self.weak_target == "cloud_small" and self.config.cloud_small_model:
                return self.config.cloud_small_model, "cloud_small"
            return self.config.local_model, "local"
        elif target == "cloud_small":
            if self.config.cloud_small_model:
                return self.config.cloud_small_model, "cloud_small"
            return self.config.cloud_model, "cloud"
        else:
            return self.config.cloud_model, "cloud"

    async def call(
        self,
        target: str,  # "local", "weak", "cloud", or "cloud_small"
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
        task_class: str = "math",
        response_kind: str = 'candidate',
    ) -> GatewayCallRecord:
        endpoint, resolved_target = self._resolve_endpoint(target)
        is_cloud_billing = resolved_target in ("cloud", "cloud_small")
        max_tokens = max_tokens if max_tokens is not None else endpoint.max_tokens
        if endpoint.candidate_format == 'json_code' and response_kind == 'candidate' and task_class in ('math', 'code'):
            messages, extra_body = json_code_request(messages, extra_body)

        # 1. HARD BUDGET CAP ENFORCEMENT
        if is_cloud_billing:
            remaining = self.budget.cloud_budget_cny - self.spent_cloud_cny
            if remaining <= 0:
                raise BudgetExceededError(
                    f"Cloud budget exhausted: spent {self.spent_cloud_cny:.5f} CNY "
                    f"of limit {self.budget.cloud_budget_cny:.5f} CNY."
                )

            worst_case = self._estimate_worst_case_cost(endpoint.pricing, messages, max_tokens)
            if (self.spent_cloud_cny + worst_case) > (self.budget.cloud_budget_cny + 1e-6):
                raise BudgetExceededError(
                    f"Pre-flight budget check rejected cloud call: estimated cost {worst_case:.5f} CNY "
                    f"would exceed remaining budget {remaining:.5f} CNY "
                    f"(spent={self.spent_cloud_cny:.5f}, total={self.budget.cloud_budget_cny:.5f})."
                )

        # 2. DISPATCH
        self.request_count += 1
        if self.prompt_dump_dir:
            self.prompt_dump_dir.mkdir(parents=True, exist_ok=True)
            kind = ('local' if target in ('local', 'weak', 'cloud_small') else
                    'hint' if 'strategic hint' in messages[0].get('content', '').lower() else 'solve')
            request = {'task': task_class, 'kind': kind, 'target': resolved_target,
                       'backend': self.backend, 'model': endpoint.name, 'messages': messages,
                       'max_tokens': max_tokens, 'temperature': temperature,
                       'extra_body': self._merge_extra_body(endpoint.extra_body, extra_body or {})}
            (self.prompt_dump_dir / f'{self.request_count:03d}_{task_class}_{kind}.json').write_text(
                json.dumps(request, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        call_id = f'call_{self.request_count}'
        context = dict(self.trace_context)
        if self.trace:
            self.trace.record('model_started', **context, call_id=call_id,
                              role='strong' if resolved_target == 'cloud' else 'small',
                              model=endpoint.name, target=resolved_target, backend=self.backend,
                              task=task_class, messages=messages, max_tokens=max_tokens)
        t0 = time.perf_counter()
        if self.backend == "mock":
            mock_res = self.mock_backend.generate(
                target=resolved_target,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                task_class=task_class,
            )
            wall_clock_s = time.perf_counter() - t0
            p_tokens = mock_res.prompt_tokens
            c_tokens = mock_res.completion_tokens
            cached = mock_res.cached_tokens
            content = mock_res.content
        else:
            try:
                p_tokens, cached, c_tokens, content, wall_clock_s = await self._call_real(
                    target=resolved_target, endpoint=endpoint, messages=messages,
                    max_tokens=max_tokens, temperature=temperature, extra_body=extra_body,
                )
            except Exception as error:
                if self.trace:
                    self.trace.record('model_failed', **context, call_id=call_id,
                                      error=type(error).__name__, elapsed_s=time.perf_counter()-t0)
                raise

        # 3. COST ACCOUNTING
        cost = self.calculate_cost(endpoint.pricing, p_tokens, cached, c_tokens)

        if is_cloud_billing:
            self.spent_cloud_cny += cost
            self.total_cloud_prompt_tokens += p_tokens
            self.total_cloud_cached_tokens += cached
            self.total_cloud_completion_tokens += c_tokens

        record = GatewayCallRecord(
            target=resolved_target,
            model_name=endpoint.name,
            prompt_tokens=p_tokens,
            cached_tokens=cached,
            completion_tokens=c_tokens,
            cost_cny=cost,
            wall_clock_s=wall_clock_s,
            content=content,
            extra={'trace_call_id': call_id,
                   **({'failure_signature': 'model/empty_response'} if not content.strip() else {})},
        )
        self.call_history.append(record)
        if self.trace:
            self.trace.record('model_finished', **context, call_id=call_id, output=content,
                              cost_cny=cost, elapsed_s=wall_clock_s,
                              prompt_tokens=p_tokens, completion_tokens=c_tokens,
                              failure_signature=record.extra.get('failure_signature'))
        return record

    @staticmethod
    def _merge_extra_body(base: dict, overrides: dict) -> dict:
        merged = copy.deepcopy(base)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = ModelGateway._merge_extra_body(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    async def _call_real(
        self,
        target: str,
        endpoint: ModelEndpointConfig,
        messages: list[dict[str, str]],
        max_tokens: int | None,
        temperature: float,
        extra_body: dict[str, Any] | None,
    ) -> tuple[int, int, int, str, float]:
        if target == "cloud":
            client = self._cloud_client
        elif target == "cloud_small":
            client = self._cloud_small_client or self._cloud_client
        else:
            client = self._local_client

        if client is None:
            raise ConfigurationError(f"OpenAI client for {target} is not initialized")

        call_kwargs: dict[str, Any] = {
            "model": endpoint.name,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        merged_body = self._merge_extra_body(endpoint.extra_body, extra_body or {})
        if merged_body:
            call_kwargs["extra_body"] = merged_body

        t0 = time.perf_counter()
        retries = endpoint.max_retries
        backoff = 1.0

        for attempt in range(retries + 1):
            try:
                resp = await client.chat.completions.create(**call_kwargs)
                wall_clock = time.perf_counter() - t0

                choice = resp.choices[0]
                content = choice.message.content or ""

                usage = resp.usage
                p_tokens = usage.prompt_tokens if usage else 0
                c_tokens = usage.completion_tokens if usage else 0
                cached = 0
                if usage and hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
                    cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0

                return p_tokens, cached, c_tokens, content, wall_clock
            except (RateLimitError, APIConnectionError, InternalServerError):
                if attempt == retries:
                    raise
                await asyncio.sleep(backoff)
                backoff *= 2.0
            except Exception:
                raise
        return 0, 0, 0, "", 0.0
