"""Immutable versioned cohort predicates."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_cohort_definitions"
down_revision = "0002_kpi_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cohort_definitions",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("config_version", sa.BigInteger(), nullable=False),
        sa.Column("cohort_id", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("predicate", sa.JSON(), nullable=False),
        sa.CheckConstraint("config_version >= 0", name="ck_cohort_def_config_version"),
        sa.CheckConstraint("ordinal >= 0", name="ck_cohort_def_ordinal"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflow_references.workflow_id"]),
        sa.PrimaryKeyConstraint("workflow_id", "config_version", "cohort_id"),
        sa.UniqueConstraint(
            "workflow_id",
            "config_version",
            "ordinal",
            name="uq_cohort_def_workflow_version_ordinal",
        ),
    )
    op.create_index(
        "ix_cohort_definitions_workflow_version_order",
        "cohort_definitions",
        ["workflow_id", "config_version", "ordinal"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cohort_definitions_workflow_version_order",
        table_name="cohort_definitions",
    )
    op.drop_table("cohort_definitions")
