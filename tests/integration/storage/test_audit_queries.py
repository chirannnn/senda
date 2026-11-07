"""PostgreSQL read-only audit filter and stable pagination tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select

from controller.analytics.audit import query_for_cohort
from controller.analytics.cohorts import definitions_from_config
from controller.storage.audit import AuditCursor, AuditLog, AuditQuery
from controller.storage.models import AuditRecord


@pytest.mark.anyio
async def test_audit_query_filters_and_keyset_pages_without_mutation(postgres_store):
    store = postgres_store
    occurred_at = datetime(2027, 1, 4, 3, tzinfo=UTC)
    async with store.transaction() as session:
        await store.ensure_workflow(session, "audit-query-wf")
        session.add_all(
            [
                AuditRecord(
                    id=UUID(int=100 + index),
                    action=action,
                    actor=actor,
                    reason=f"test row {index}",
                    workflow_id="audit-query-wf",
                    resource_type="workflow_config",
                    resource_id="cfg",
                    evidence=evidence,
                    occurred_at=occurred_at,
                )
                for index, (action, actor, evidence) in enumerate(
                    [
                        (
                            "proposal",
                            "controller",
                            {
                                "config_version": 3,
                                "approval_id": "ap-1",
                                "outcome": "approved",
                            },
                        ),
                        (
                            "apply",
                            "controller",
                            {
                                "config_version": 3,
                                "cohort_id": "default",
                                "approval_id": "ap-1",
                                "outcome": "applied",
                            },
                        ),
                        (
                            "config_changed",
                            "supervisor",
                            {"config_version": 3, "outcome": "observed"},
                        ),
                        (
                            "proposal",
                            "controller",
                            {
                                "config_version": 4,
                                "approval_id": "ap-2",
                                "outcome": "pending",
                            },
                        ),
                        (
                            "legacy_record",
                            "controller",
                            {"config_version": "unverified", "outcome": "unknown"},
                        ),
                    ],
                    start=1,
                )
            ]
        )

    audit = AuditLog(store)
    filters = AuditQuery(
        workflow_id="audit-query-wf",
        actor="controller",
        config_version=3,
        approval_id="ap-1",
        start_at=datetime(2027, 1, 4, 0, tzinfo=UTC),
        end_at=datetime(2027, 1, 5, 0, tzinfo=UTC),
        page_size=1,
    )
    first = await audit.query(filters)
    assert [row.id for row in first.records] == [UUID(int=101)]
    assert first.next_cursor == AuditCursor(occurred_at, UUID(int=101))

    second = await audit.query(
        AuditQuery(
            workflow_id=filters.workflow_id,
            actor=filters.actor,
            config_version=filters.config_version,
            approval_id=filters.approval_id,
            start_at=filters.start_at,
            end_at=filters.end_at,
            page_size=1,
            after=first.next_cursor,
        )
    )
    assert [row.id for row in second.records] == [UUID(int=102)]
    assert second.next_cursor is None

    default_cohort = definitions_from_config("audit-query-wf", 3, {"cohorts": []})[-1]
    applied = await query_for_cohort(
        audit,
        default_cohort,
        start_at=datetime(2027, 1, 4, 0, tzinfo=UTC),
        end_at=datetime(2027, 1, 5, 0, tzinfo=UTC),
    )
    assert [row.id for row in applied.records] == [UUID(int=102)]
    supervisor = await audit.query(
        AuditQuery(workflow_id="audit-query-wf", actor="supervisor")
    )
    assert [row.id for row in supervisor.records] == [UUID(int=103)]
    config_three = await audit.query(
        AuditQuery(workflow_id="audit-query-wf", config_version=3)
    )
    assert [row.id for row in config_three.records] == [
        UUID(int=101),
        UUID(int=102),
        UUID(int=103),
    ]

    async with store.transaction() as session:
        count = await session.scalar(select(func.count()).select_from(AuditRecord))
    assert count == 5
    assert len(await audit.list_for_workflow("audit-query-wf")) == 5
