"""TierLab: Universal Research Agent with Local GPU & Cloud Escalation."""

__version__ = "0.1.0"

from tierlab.controller import ResearchController
from tierlab.gateway import ModelGateway
from tierlab.ledger import DecisionLedger
from tierlab.lessons import LessonStore
from tierlab.tasks import get_task
from tierlab.types import Budget, Candidate, ControllerConfig, PolicyType

__all__ = [
    "ResearchController",
    "ModelGateway",
    "DecisionLedger",
    "LessonStore",
    "Budget",
    "Candidate",
    "ControllerConfig",
    "PolicyType",
    "get_task",
]
