import pytest

from controller.governance.state_machine import ProposalStatus, transition


@pytest.mark.parametrize(
    "current,target",
    [
        (ProposalStatus.DRAFT, ProposalStatus.SUBMITTED),
        (ProposalStatus.SUBMITTED, ProposalStatus.PENDING),
        (ProposalStatus.PENDING, ProposalStatus.APPROVED),
        (ProposalStatus.PENDING, ProposalStatus.REJECTED),
        (ProposalStatus.PENDING, ProposalStatus.EXPIRED),
        (ProposalStatus.APPROVED, ProposalStatus.APPLYING),
        (ProposalStatus.APPLYING, ProposalStatus.APPLIED),
        (ProposalStatus.APPLYING, ProposalStatus.LAPSED),
        (ProposalStatus.APPROVED, ProposalStatus.STALE),
    ],
)
def test_documented_lifecycle_transitions(current, target):
    assert transition(current, target) == target


def test_same_state_observation_is_idempotent():
    assert (
        transition(ProposalStatus.PENDING, ProposalStatus.PENDING)
        == ProposalStatus.PENDING
    )


@pytest.mark.parametrize(
    "current,target",
    [
        (ProposalStatus.DRAFT, ProposalStatus.APPLIED),
        (ProposalStatus.REJECTED, ProposalStatus.APPLIED),
        (ProposalStatus.EXPIRED, ProposalStatus.APPLYING),
        (ProposalStatus.APPLIED, ProposalStatus.PENDING),
    ],
)
def test_invalid_or_terminal_transitions_fail(current, target):
    with pytest.raises(ValueError, match="invalid proposal transition"):
        transition(current, target)
