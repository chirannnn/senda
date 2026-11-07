import pytest

from controller.governance.proposals import ProposalPayload


def _proposal(**overrides):
    values = {
        "proposal_id": "proposal-1",
        "workflow_id": "workflow-1",
        "base_version": 3,
        "proposed_config": {"prompt_version": "P2"},
        "change_summary": "Use the approved prompt candidate",
        "diff": {"prompt_version": {"before": "P1", "after": "P2"}},
        "affected_leads": 100,
        "dry_run": {"method": "historical_replay", "sample": 100},
        "impact_score": 0.4,
        "rationale": "Evidence supports evaluation of this candidate",
    }
    values.update(overrides)
    return ProposalPayload(**values)


def test_payload_matches_approval_contract_and_hash_is_stable():
    first = _proposal()
    second = _proposal(proposed_config={"prompt_version": "P2"})

    assert first.to_api_payload()["proposal_id"] == "proposal-1"
    assert first.payload_hash == second.payload_hash
    assert len(first.payload_hash) == 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("proposal_id", " "),
        ("workflow_id", ""),
        ("base_version", True),
        ("base_version", -1),
        ("proposed_config", {}),
        ("change_summary", " "),
        ("diff", {}),
        ("affected_leads", True),
        ("affected_leads", -1),
        ("dry_run", {}),
        ("impact_score", float("nan")),
        ("impact_score", 1.1),
        ("rationale", ""),
        ("evidence", []),
        ("expires_in_hours", 0),
    ],
)
def test_invalid_proposal_fields_are_rejected(field, value):
    with pytest.raises(ValueError):
        _proposal(**{field: value})


def test_non_json_payload_values_are_rejected():
    with pytest.raises(ValueError, match="JSON values"):
        _proposal(proposed_config={"bad": object()})
