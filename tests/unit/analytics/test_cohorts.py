"""Ordered, versioned cohort construction and matching tests."""

import pytest

from controller.analytics.cohorts import assign_cohort, definitions_from_config


def test_cohorts_use_config_order_and_default_fallback():
    definitions = definitions_from_config(
        "wf",
        7,
        {
            "cohorts": [
                {"id": "older", "where": {"age_band": ["60+", "46-60"]}},
                {"id": "tamil", "where": {"lang": ["ta"]}},
            ]
        },
    )
    match = assign_cohort({"age_band": "46-60", "lang": "ta"}, definitions)
    assert match is not None
    assert (match.workflow_id, match.config_version, match.cohort_id) == (
        "wf",
        7,
        "older",
    )
    fallback = assign_cohort({"age_band": "18-30", "lang": "hi"}, definitions)
    assert fallback is not None and fallback.cohort_id == "default"
    assert assign_cohort(None, definitions) is None


def test_cohort_identity_includes_workflow_and_config_version():
    old = definitions_from_config(
        "wf", 4, {"cohorts": [{"id": "young", "where": {"age_band": ["18-30"]}}]}
    )[0]
    new = definitions_from_config(
        "wf", 5, {"cohorts": [{"id": "young", "where": {"age_band": ["18-30"]}}]}
    )[0]
    assert old.identity == ("wf", 4, "young")
    assert new.identity == ("wf", 5, "young")
    assert old.identity != new.identity


@pytest.mark.parametrize(
    "config",
    [
        {"cohorts": "invalid"},
        {
            "cohorts": [
                {"id": "duplicate", "where": {}},
                {"id": "duplicate", "where": {}},
            ]
        },
        {"cohorts": [{"id": "default", "where": {}}]},
        {"cohorts": [{"id": "bad", "where": {"unknown": ["x"]}}]},
    ],
)
def test_malformed_cohort_rules_fail_closed(config):
    with pytest.raises((TypeError, ValueError)):
        definitions_from_config("wf", 0, config)


def test_config_version_must_be_an_integer_not_a_boolean():
    with pytest.raises(ValueError, match="config version"):
        definitions_from_config("wf", True, {"cohorts": []})
