"""Initial durable event, checkpoint, audit and outbox schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_durable_store"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "controller_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="running", nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), server_default="{}", nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name="ck_controller_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_controller_runs_started_at", "controller_runs", ["started_at"])

    op.create_table(
        "workflow_references",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
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
        sa.PrimaryKeyConstraint("workflow_id"),
    )

    op.create_table(
        "simulator_events",
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_number", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "stored_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_simulator_events_chunk", "simulator_events", ["chunk_number"])
    op.create_index(
        "ix_simulator_events_kind_chunk", "simulator_events", ["kind", "chunk_number"]
    )

    op.create_table(
        "processing_checkpoints",
        sa.Column("checkpoint_key", sa.Text(), nullable=False),
        sa.Column("chunk_number", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("checkpoint_key"),
    )

    op.create_table(
        "audit_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_references.workflow_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_records_workflow_time",
        "audit_records",
        ["workflow_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_records_action_time", "audit_records", ["action", "occurred_at"]
    )

    op.create_table(
        "outbox_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_operations_attempts"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'reconciliation_required')",
            name="ck_outbox_operations_status",
        ),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_references.workflow_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index(
        "ix_outbox_operations_status_created",
        "outbox_operations",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_operations_status_created", table_name="outbox_operations")
    op.drop_table("outbox_operations")
    op.drop_index("ix_audit_records_action_time", table_name="audit_records")
    op.drop_index("ix_audit_records_workflow_time", table_name="audit_records")
    op.drop_table("audit_records")
    op.drop_table("processing_checkpoints")
    op.drop_index("ix_simulator_events_kind_chunk", table_name="simulator_events")
    op.drop_index("ix_simulator_events_chunk", table_name="simulator_events")
    op.drop_table("simulator_events")
    op.drop_table("workflow_references")
    op.drop_index("ix_controller_runs_started_at", table_name="controller_runs")
    op.drop_table("controller_runs")
