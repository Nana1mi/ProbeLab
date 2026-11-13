"""Exceptions for the TierLab framework."""

class TierLabError(Exception):
    """Base exception for all TierLab errors."""
    pass


class BudgetExceededError(TierLabError):
    """Raised when an operation would exceed the allocated cloud budget."""
    pass


class SandboxExecutionError(TierLabError):
    """Raised when candidate code fails to execute in the sandbox."""
    pass


class SandboxTimeoutError(SandboxExecutionError):
    """Raised when candidate code execution exceeds the timeout limit."""
    pass


class ConfigurationError(TierLabError):
    """Raised when invalid configuration is provided."""
    pass


class VerificationError(TierLabError):
    """Raised when verification encounters an unrecoverable error."""
    pass
