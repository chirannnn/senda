"""Allow one predeclared comparison family per workflow config version.

Revision ID: 0007_experiment_family_lock
Revises: 0006_experiment_decisions
"""

from __future__ import annotations

from alembic import op

revision = "0007_experiment_family_lock"
down_revision = "0006_experiment_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_experiment_workflow_config_family",
        "experiment_decisions",
        ["workflow_id", "config_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_experiment_workflow_config_family",
        "experiment_decisions",
        type_="unique",
    )
