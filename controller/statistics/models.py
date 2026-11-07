"""Validated immutable values shared by the experiment evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Kpi(StrEnum):
    """Independent rate outcomes supported by persisted call facts."""

    PICKUP = "pickup"
    ENGAGEMENT = "engagement"


class Decision(StrEnum):
    """Deterministic experiment outcome; only WINNER can propose a change."""

    WINNER = "winner"
    NO_WINNER = "no_winner"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSAFE = "unsafe"


@dataclass(frozen=True, slots=True)
class PersonOutcome:
    """A person's additive successes and trials in exactly one experiment arm."""

    person_id: str
    arm_id: str
    successes: int
    trials: int

    def __post_init__(self) -> None:
        if not self.person_id or not self.arm_id:
            raise ValueError("person and arm IDs are required")
        if (
            isinstance(self.successes, bool)
            or not isinstance(self.successes, int)
            or isinstance(self.trials, bool)
            or not isinstance(self.trials, int)
            or self.trials < 0
            or not 0 <= self.successes <= self.trials
        ):
            raise ValueError("person outcomes must satisfy 0 <= successes <= trials")
