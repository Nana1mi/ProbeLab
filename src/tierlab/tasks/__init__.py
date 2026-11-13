"""Research tasks registry and exports."""

from tierlab.tasks.base import ResearchTask, Verifier
from tierlab.tasks.code_opt import CodeOptimizationTask
from tierlab.tasks.math_packing import MathPackingTask
from tierlab.tasks.paper_qa import PaperQATask


def get_task(name: str, **kwargs) -> ResearchTask:
    """Task factory by name."""
    task_name = name.lower().strip()
    if task_name in ("math", "math_packing", "circles"):
        return MathPackingTask(**kwargs)
    elif task_name in ("code", "code_opt", "cosine"):
        return CodeOptimizationTask(**kwargs)
    elif task_name in ("paper", "paper_qa", "qa"):
        return PaperQATask(**kwargs)
    else:
        raise ValueError(f"Unknown task name: {name}. Available: math, code, paper")


__all__ = [
    "ResearchTask",
    "Verifier",
    "MathPackingTask",
    "CodeOptimizationTask",
    "PaperQATask",
    "get_task",
]
