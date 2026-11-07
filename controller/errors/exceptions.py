"""Typed failures surfaced by the controller boundary."""


class ControllerError(Exception):
    """Base class for expected, user-actionable controller failures."""


class ConfigurationError(ControllerError, ValueError):
    """Invalid or incomplete runtime configuration."""


class SimulatorError(ControllerError):
    """Simulator transport or response failure."""


class SimulatorHTTPError(SimulatorError):
    """Non-success response from a documented simulator endpoint."""

    def __init__(self, method: str, path: str, status_code: int) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        super().__init__(f"Simulator returned HTTP {status_code} for {method} {path}.")


class AmbiguousPostError(SimulatorError):
    """A POST may have reached the simulator, but its response was not received."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"The result of POST {path} is ambiguous ({reason}); do not retry it blindly."
        )


class GovernanceError(ControllerError):
    """A proposal lifecycle operation failed or requires operator attention."""


class ProposalReconciliationRequired(GovernanceError):
    """Remote proposal state could not be established safely."""
