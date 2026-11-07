"""Persist fixed-window experiment evidence and decisions.

Revision ID: 0006_experiment_decisions
Revises: 0005_governance_proposals
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_experiment_decisions"
down_revision = "0005_governance_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_decisions",
        sa.Column("experiment_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("config_version", sa.BigInteger(), nullable=False),
        sa.Column("cohort_id", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("cutoff_event_id", sa.BigInteger(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("config_version >= 0", name="ck_experiment_config_version"),
        sa.CheckConstraint("cutoff_event_id >= 0", name="ck_experiment_cutoff_event"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_references.workflow_id"]),
        sa.PrimaryKeyConstraint("experiment_id"),
        sa.UniqueConstraint(
            "workflow_id", "experiment_id", name="uq_experiment_workflow_identity"
        ),
    )
    op.create_index(
        "ix_experiment_decisions_workflow_created",
        "experiment_decisions",
        ["workflow_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_decisions_workflow_created", table_name="experiment_decisions"
    )
    op.drop_table("experiment_decisions")
