"""Canonical versioned cohort rules and ordered lead attribution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_FEATURES = {"lang", "city_tier", "age_band", "value_band"}


@dataclass(frozen=True, slots=True)
class CohortDefinition:
    """Shared cohort identity and ordered predicate for a config version."""

    workflow_id: str
    config_version: int
    cohort_id: str
    ordinal: int
    predicate: Mapping[str, tuple[str, ...]]

    @property
    def identity(self) -> tuple[str, int, str]:
        return self.workflow_id, self.config_version, self.cohort_id

    def to_record(self) -> dict[str, object]:
        return {
            "cohort_id": self.cohort_id,
            "ordinal": self.ordinal,
            "predicate": {key: list(values) for key, values in self.predicate.items()},
        }


def definitions_from_config(
    workflow_id: str, config_version: int, config: Mapping[str, object]
) -> tuple[CohortDefinition, ...]:
    """Build contract-ordered definitions from an HTTP config snapshot."""
    if (
        not workflow_id
        or isinstance(config_version, bool)
        or not isinstance(config_version, int)
        or config_version < 0
    ):
        raise ValueError("workflow ID and nonnegative config version are required")
    raw = config.get("cohorts", [])
    if not isinstance(raw, list):
        raise TypeError("config cohorts must be an ordered list")
    definitions: list[CohortDefinition] = []
    seen: set[str] = set()
    for ordinal, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError("each cohort definition must be an object")
        cohort_id = item.get("id")
        where = item.get("where")
        if not isinstance(cohort_id, str) or not cohort_id or cohort_id == "default":
            raise ValueError("configured cohorts need a non-reserved string ID")
        if cohort_id in seen:
            raise ValueError(f"duplicate cohort ID {cohort_id!r}")
        if not isinstance(where, dict):
            raise TypeError(f"cohort {cohort_id!r} needs a where object")
        predicate: dict[str, tuple[str, ...]] = {}
        for feature, values in where.items():
            if feature not in _FEATURES or not isinstance(values, list) or not values:
                raise ValueError(
                    f"cohort {cohort_id!r} has an invalid feature predicate"
                )
            if any(not isinstance(value, str) for value in values):
                raise ValueError(f"cohort {cohort_id!r} feature values must be strings")
            predicate[feature] = tuple(values)
        seen.add(cohort_id)
        definitions.append(
            CohortDefinition(workflow_id, config_version, cohort_id, ordinal, predicate)
        )
    definitions.append(
        CohortDefinition(workflow_id, config_version, "default", len(definitions), {})
    )
    return tuple(definitions)


def assign_cohort(
    features: Mapping[str, object] | None,
    definitions: Sequence[CohortDefinition],
) -> CohortDefinition | None:
    """Apply first-match precedence; missing features remain unattributed."""
    if features is None or not definitions:
        return None
    ordered = sorted(definitions, key=lambda definition: definition.ordinal)
    identity = {(item.workflow_id, item.config_version) for item in ordered}
    if len(identity) != 1:
        raise ValueError("cohort definitions must share workflow and config version")
    default = next((item for item in ordered if item.cohort_id == "default"), None)
    if default is None:
        raise ValueError("versioned cohort set must include its default definition")
    for definition in ordered:
        if definition.cohort_id == "default":
            continue
        if all(
            features.get(key) in accepted
            for key, accepted in definition.predicate.items()
        ):
            return definition
    return default
