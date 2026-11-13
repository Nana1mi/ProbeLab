"""Tests for ModelGateway: token accounting, pricing, and budget hard cap."""

import pytest
from tierlab.exceptions import BudgetExceededError
from tierlab.gateway import ModelGateway
from tierlab.mock_llm import MockLLMBackend
from tierlab.types import Budget, ModelsConfigFile, PricingConfig


@pytest.mark.asyncio
async def test_gateway_local_free_pricing(mock_models_config: ModelsConfigFile):
    gateway = ModelGateway(
        models_config=mock_models_config,
        budget=Budget(cloud_budget_cny=1.0),
        backend="mock",
    )
    rec = await gateway.call(
        target="local",
        messages=[{"role": "user", "content": "generate solution"}],
    )
    assert rec.target == "local"
    assert rec.cost_cny == 0.0
    assert rec.wall_clock_s >= 0.0
    assert len(rec.content) > 0
    assert gateway.spent_cloud_cny == 0.0


@pytest.mark.asyncio
async def test_gateway_cloud_pricing_and_usage(mock_models_config: ModelsConfigFile):
    gateway = ModelGateway(
        models_config=mock_models_config,
        budget=Budget(cloud_budget_cny=5.0),
        backend="mock",
    )
    rec = await gateway.call(
        target="cloud",
        messages=[
            {"role": "system", "content": "You are advisor. Provide a strategic hint (max 150 tokens)."},
            {"role": "user", "content": "Need direction"},
        ],
        max_tokens=150,
    )
    assert rec.target == "cloud"
    assert rec.prompt_tokens > 0
    assert rec.completion_tokens > 0
    assert rec.cost_cny > 0.0
    assert gateway.spent_cloud_cny == rec.cost_cny


@pytest.mark.asyncio
async def test_gateway_hard_budget_cap_preflight_rejection(mock_models_config: ModelsConfigFile):
    # Set cloud budget extremely low so even a single cloud call is rejected
    gateway = ModelGateway(
        models_config=mock_models_config,
        budget=Budget(cloud_budget_cny=0.000001),
        backend="mock",
    )
    with pytest.raises(BudgetExceededError) as exc_info:
        await gateway.call(
            target="cloud",
            messages=[{"role": "user", "content": "Large request requiring advice"}],
            max_tokens=500,
        )
    assert "budget" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_gateway_hard_budget_cap_exhaustion(mock_models_config: ModelsConfigFile):
    # Allow 1 call, then block subsequent call
    gateway = ModelGateway(
        models_config=mock_models_config,
        budget=Budget(cloud_budget_cny=0.001),
        backend="mock",
    )
    # First small call passes
    rec1 = await gateway.call(
        target="cloud",
        messages=[{"role": "system", "content": "strategic hint (max 150 tokens)."}],
        max_tokens=20,
    )
    assert rec1.cost_cny > 0.0

    # Exhaust budget manually to simulate limit reached
    gateway.spent_cloud_cny = gateway.budget.cloud_budget_cny

    with pytest.raises(BudgetExceededError):
        await gateway.call(
            target="cloud",
            messages=[{"role": "user", "content": "Another call"}],
            max_tokens=50,
        )
