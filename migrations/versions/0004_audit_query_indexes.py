"""Indexes for bounded workflow audit browsing."""

from __future__ import annotations

from alembic import op

revision = "0004_audit_query_indexes"
down_revision = "0003_cohort_definitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_audit_records_workflow_time", table_name="audit_records")
    op.create_index(
        "ix_audit_records_workflow_time_id",
        "audit_records",
        ["workflow_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_records_workflow_actor_time_id",
        "audit_records",
        ["workflow_id", "actor", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_records_workflow_action_time_id",
        "audit_records",
        ["workflow_id", "action", "occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_records_workflow_action_time_id", table_name="audit_records"
    )
    op.drop_index("ix_audit_records_workflow_actor_time_id", table_name="audit_records")
    op.drop_index("ix_audit_records_workflow_time_id", table_name="audit_records")
    op.create_index(
        "ix_audit_records_workflow_time",
        "audit_records",
        ["workflow_id", "occurred_at"],
    )
