"""Unit test for specification #9: alpha conversion from CNY/minute to CNY/second."""

import pytest
from tierlab.config import load_models_config
from tierlab.types import ModelsConfigFile, alpha_to_cny_per_second


def test_alpha_conversion_single_source_of_truth():
    # 0.06 CNY per minute should convert exactly to 0.001 CNY per second
    alpha_min = 0.06
    alpha_sec = alpha_to_cny_per_second(alpha_min)
    assert alpha_sec == pytest.approx(0.001, rel=1e-6)

    # 1.2 CNY per minute should convert to 0.02 CNY per second
    assert alpha_to_cny_per_second(1.2) == pytest.approx(0.02, rel=1e-6)


def test_models_config_alpha_property():
    cfg = load_models_config()
    assert hasattr(cfg, "alpha_cny_per_min")
    assert cfg.alpha_per_second() == pytest.approx(cfg.alpha_cny_per_min / 60.0, rel=1e-6)
