"""Persist connected-call QA and incident lifecycle evidence.

Revision ID: 0008_incident_evidence
Revises: 0007_experiment_family_lock
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_incident_evidence"
down_revision = "0007_experiment_family_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kpi_facts", sa.Column("qa_hallucination", sa.Boolean()))
    op.add_column("kpi_facts", sa.Column("qa_flow_adherence", sa.Float()))
    op.add_column(
        "kpi_facts",
        sa.Column(
            "qa_quality",
            sa.Text(),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.create_check_constraint(
        "ck_kpi_facts_qa_quality",
        "kpi_facts",
        "qa_quality IN ('valid', 'incomplete', 'invalid', 'not_applicable')",
    )
    op.create_table(
        "incident_records",
        sa.Column("incident_id", sa.Text(), primary_key=True),
        sa.Column(
            "workflow_id",
            sa.Text(),
            sa.ForeignKey("workflow_references.workflow_id"),
            nullable=False,
        ),
        sa.Column("trigger_config_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("timeline", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workflow_id", "trigger_config_version", name="uq_incident_workflow_config"
        ),
    )
    op.create_index("ix_incident_records_status", "incident_records", ["status"])


def downgrade() -> None:
    op.drop_index("ix_incident_records_status", table_name="incident_records")
    op.drop_table("incident_records")
    op.drop_constraint("ck_kpi_facts_qa_quality", "kpi_facts", type_="check")
    op.drop_column("kpi_facts", "qa_quality")
    op.drop_column("kpi_facts", "qa_flow_adherence")
    op.drop_column("kpi_facts", "qa_hallucination")
