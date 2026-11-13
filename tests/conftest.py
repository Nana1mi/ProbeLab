"""Shared pytest fixtures for TierLab test suite."""

import os
import sys
from pathlib import Path
import pytest

# Ensure workspace root is in sys.path so integrations/ can be imported by tests
workspace_root = Path(__file__).resolve().parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

from tierlab.config import load_models_config
from tierlab.types import (
    Budget,
    ControllerConfig,
    ModelEndpointConfig,
    ModelsConfigFile,
    PricingConfig,
)



@pytest.fixture
def mock_models_config() -> ModelsConfigFile:
    return ModelsConfigFile(
        cloud_model=ModelEndpointConfig(
            name="mock-cloud-model",
            base_url="http://mock-cloud/v1",
            pricing=PricingConfig(
                prompt_cny_per_m=2.0,
                cached_cny_per_m=0.5,
                completion_cny_per_m=8.0,
            ),
            supports_guided_decoding=False,
        ),
        local_model=ModelEndpointConfig(
            name="mock-local-model",
            base_url="http://mock-local/v1",
            pricing=PricingConfig(
                prompt_cny_per_m=0.0,
                cached_cny_per_m=0.0,
                completion_cny_per_m=0.0,
            ),
            supports_guided_decoding=True,
        ),
        rho_gpu_cny_per_s=0.0006,
        default_alpha=0.0001,
    )


@pytest.fixture
def temp_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
