"""Governed proposal lifecycle primitives."""

from controller.governance.proposals import ProposalPayload
from controller.governance.state_machine import ProposalStatus, transition

__all__ = ["ProposalPayload", "ProposalStatus", "transition"]
