import os

import pytest
from sqlalchemy import delete

from controller.storage.models import (
    AnalyticsCheckpoint,
    AuditRecord,
    ControllerRun,
    GovernanceProposal,
    KpiFact,
    OutboxOperation,
    ProcessingCheckpoint,
    SimulatorEvent,
    VersionedCohortDefinition,
    WorkflowReference,
)
from controller.storage.store import DatabaseStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def postgres_store():
    database_url = os.environ.get("CONTROLLER_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "set CONTROLLER_TEST_DATABASE_URL to a disposable PostgreSQL database"
        )

    store = DatabaseStore(database_url)
    try:
        await store.initialize()
        await store.migrate()
        async with store.transaction() as session:
            for model in (
                OutboxOperation,
                GovernanceProposal,
                AuditRecord,
                KpiFact,
                AnalyticsCheckpoint,
                VersionedCohortDefinition,
                SimulatorEvent,
                ProcessingCheckpoint,
                ControllerRun,
                WorkflowReference,
            ):
                await session.execute(delete(model))
        yield store
    finally:
        await store.close()
