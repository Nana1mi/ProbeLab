"""Core data models and types for the TierLab framework."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class VerifierLevel(str, Enum):
    L0 = "L0"  # Executable / format
    L1 = "L1"  # Correctness / constraints
    L2 = "L2"  # Quality / objective value


class ConsultationRung(str, Enum):
    HINT = "hint"
    SOLVE = "solve"        # Direct cloud generation, replaces full takeover
    DIAGNOSE = "diagnose"  # Reserved, default disabled
    REPLAN = "replan"      # Reserved, default disabled


class PolicyType(str, Enum):
    TIERLAB = "tierlab"
    ALL_CLOUD = "all_cloud"
    ALL_LOCAL = "all_local"
    CASCADE = "cascade"
    STATIC_SPLIT = "static_split"
    NO_ADVICE_RETRY = "no_advice_retry"  # Grant extra local rounds at consultation triggers


class ActionType(str, Enum):
    PROPOSE_LOCAL = "propose_local"
    WIDEN = "widen"
    LESSON_HIT = "lesson_hit"
    HINT = "hint"
    SOLVE = "solve"
    DIAGNOSE = "diagnose"
    REPLAN = "replan"
    CLOUD_PROPOSE = "cloud_propose"
    STATIC_PLAN = "static_plan"
    NO_ADVICE_RETRY = "no_advice_retry"
    DUPLICATE_SKIP = "duplicate_skip"
    FINAL_EVAL = "final_eval"
    STOP = "stop"


class VerificationResult(BaseModel):
    """Unified verification outcome matching amended specification #2."""
    valid: bool
    score: float = 0.0
    feedback: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    elapsed_s: float = 0.0
    failure_signature: str | None = None


class PricingConfig(BaseModel):
    """Token pricing in CNY per million tokens."""
    prompt_cny_per_m: float | None = Field(default=None, ge=0)
    cached_cny_per_m: float | None = Field(default=None, ge=0)
    completion_cny_per_m: float | None = Field(default=None, ge=0)


class ModelEndpointConfig(BaseModel):
    name: str
    base_url: str = "http://localhost:8000/v1"
    api_key_env: str = "OPENAI_API_KEY"
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    supports_guided_decoding: bool = False
    extra_body: dict[str, Any] = Field(default_factory=dict)
    max_tokens: int = Field(default=2048, gt=0)
    candidate_format: Literal['raw', 'json_code'] = 'raw'
    timeout_s: float = 60.0
    max_retries: int = 3


def alpha_to_cny_per_second(alpha_cny_per_min: float) -> float:
    """SINGLE SOURCE OF TRUTH for alpha conversion: converts CNY/min to CNY/s."""
    return alpha_cny_per_min / 60.0


class ModelsConfigFile(BaseModel):
    cloud_model: ModelEndpointConfig
    local_model: ModelEndpointConfig
    cloud_small_model: ModelEndpointConfig | None = None  # Optional small model API
    rho_gpu_cny_per_s: float = 0.0006  # e.g., ~2.16 CNY/hour for RTX 4090
    alpha_cny_per_min: float = 0.006    # Value of saving 1 minute in CNY

    def alpha_per_second(self) -> float:
        return alpha_to_cny_per_second(self.alpha_cny_per_min)


class Budget(BaseModel):
    cloud_budget_cny: float = 10.0
    max_wall_clock_s: float = 600.0
    max_steps: int = 30
    min_delta_per_cny_threshold: float = 0.01
    recent_r_consultations: int = 3


class Candidate(BaseModel):
    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class LessonStatus(str, Enum):
    PROVISIONAL = "provisional"
    VALIDATED = "validated"


class LessonRecord(BaseModel):
    lesson_id: str
    signature: str
    task_class: str
    rung: str
    advice: str
    condition: str
    verified_delta: float
    cost_cny: float
    status: LessonStatus = LessonStatus.PROVISIONAL
    uses: int = 0


class LedgerEntry(BaseModel):
    run: str
    step: int
    action: str
    trigger: str
    cloud_in: int = 0
    cloud_out: int = 0
    cloud_cny: float = 0.0
    gpu_s: float = 0.0
    gpu_cny: float = 0.0     # Separately tracked: gpu_s * rho_gpu_cny_per_s
    wall_s: float = 0.0      # Separately tracked: step wall clock time
    verify_s: float = 0.0    # Time spent running verifiers for this step (#10)
    credited_to: str | None = None  # Attribution of improvement to hint (#11)
    score_before: float = 0.0
    score_after: float = 0.0
    verified: bool = False
    duplicate: bool = False
    lesson_id: str | None = None
    seed: int = 0
    policy: str = "tierlab"
    config_hash: str = ""
    timestamp: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class ControllerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # Forbid unknown fields (#6)

    k_schedule: list[int] = Field(default_factory=lambda: [1, 2, 4])
    k_init: int = 1
    local_patience: int = 2                   # Stagnant rounds before consultation (#12)
    stagnation_patience: int = 4              # Unified stopping rule: stop after P steps no improvement (#7)
    max_format_failures: int = 2              # Max consecutive format failures before escalation (#3)
    top_m_for_expensive: int = 2
    cascade_fail_threshold: int = 3
    no_widen: bool = False
    enable_lessons: bool = False              # Lessons library default disabled (#7)
    share_lessons_across_runs: bool = False   # Isolated per run by default (#7)
    enable_diagnose_rung: bool = False        # Default disabled (#4)
    enable_replan_rung: bool = False          # Default disabled (#4)
    weak_backend: str = "local"               # "local" or "cloud_small" (#8)
    alpha_cny_per_min: float = 0.006
    noise_threshold: float = 0.08
    backend: str = "mock"                     # "mock" or "real"
    local_max_tokens: int = Field(default=2048, gt=0)
    hint_max_tokens: int = Field(default=400, gt=0)
    max_feedback_chars: int = Field(default=2400, ge=256)
