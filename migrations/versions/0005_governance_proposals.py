"""Persist governance proposal payloads and lifecycle observations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_governance_proposals"
down_revision = "0004_audit_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governance_proposals",
        sa.Column("proposal_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("base_version", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="draft", nullable=False
        ),
        sa.Column("approval_id", sa.Text(), nullable=True),
        sa.Column("remote_status", sa.Text(), nullable=True),
        sa.Column("outcome", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("base_version >= 0", name="ck_governance_proposals_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'pending', 'approved', 'rejected', "
            "'expired', 'applying', 'applied', 'lapsed', 'stale', "
            "'reconciliation_required')",
            name="ck_governance_proposals_status",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_references.workflow_id"]),
        sa.PrimaryKeyConstraint("proposal_id"),
        sa.UniqueConstraint("approval_id", name="uq_governance_proposals_approval_id"),
    )
    op.create_index(
        "ix_governance_proposals_workflow_status",
        "governance_proposals",
        ["workflow_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_governance_proposals_workflow_status", table_name="governance_proposals"
    )
    op.drop_table("governance_proposals")
