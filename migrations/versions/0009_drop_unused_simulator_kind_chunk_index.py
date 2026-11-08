"""Drop unused simulator event kind/chunk index."""

from __future__ import annotations

from alembic import op

revision = "0009_drop_unused_event_idx"
down_revision = "0008_incident_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_simulator_events_kind_chunk",
            table_name="simulator_events",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_simulator_events_kind_chunk",
            "simulator_events",
            ["kind", "chunk_number"],
            postgresql_concurrently=True,
        )
