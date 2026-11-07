"""Durable event-derived KPI facts and their restart cursor."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_kpi_facts"
down_revision = "0001_durable_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_checkpoints",
        sa.Column("processor", sa.Text(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("processor"),
    )
    op.create_table(
        "kpi_facts",
        sa.Column("source_event_id", sa.BigInteger(), nullable=False),
        sa.Column("source_chunk", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("campaign_id", sa.Text(), nullable=True),
        sa.Column("lead_id", sa.Text(), nullable=True),
        sa.Column("person_id", sa.Text(), nullable=True),
        sa.Column("config_version", sa.BigInteger(), nullable=True),
        sa.Column("observed_cohort_id", sa.Text(), nullable=True),
        sa.Column("variant", sa.JSON(), nullable=True),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("quality", sa.Text(), nullable=False),
        sa.Column("quality_detail", sa.Text(), nullable=True),
        sa.Column("dials", sa.Integer(), server_default="0", nullable=False),
        sa.Column("connected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("concluded", sa.Integer(), server_default="0", nullable=False),
        sa.Column("uploaded", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "derived_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dials >= 0 AND connected >= 0 AND concluded >= 0 AND uploaded >= 0 "
            "AND connected <= dials AND concluded <= connected",
            name="ck_kpi_facts_counts",
        ),
        sa.ForeignKeyConstraint(["source_event_id"], ["simulator_events.event_id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_references.workflow_id"]),
        sa.PrimaryKeyConstraint("source_event_id"),
    )
    op.create_index(
        "ix_kpi_facts_workflow_time", "kpi_facts", ["workflow_id", "occurred_at"]
    )
    op.create_index(
        "ix_kpi_facts_workflow_version_time",
        "kpi_facts",
        ["workflow_id", "config_version", "occurred_at"],
    )
    op.create_index(
        "ix_kpi_facts_campaign", "kpi_facts", ["workflow_id", "campaign_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_kpi_facts_campaign", table_name="kpi_facts")
    op.drop_index("ix_kpi_facts_workflow_version_time", table_name="kpi_facts")
    op.drop_index("ix_kpi_facts_workflow_time", table_name="kpi_facts")
    op.drop_table("kpi_facts")
    op.drop_table("analytics_checkpoints")
