"""Real subprocess termination against PostgreSQL for recovery boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from controller.ingestion.events import ChunkDescriptor
from controller.ingestion.service import EventIngestionService


class _RestartSimulator:
    def __init__(self, body: str):
        self.body = body

    @asynccontextmanager
    async def stream_chunk_lines(self, _chunk):
        def lines():
            yield from self.body.splitlines()

        yield lines()


@pytest.mark.anyio
async def test_hard_kill_during_chunk_transaction_rolls_back_and_replay_is_unique(
    postgres_store, tmp_path
):
    database_url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    marker = tmp_path / "first-row-flushed"
    worker = r"""
import asyncio, json, sys
from contextlib import asynccontextmanager
from controller.ingestion.events import ChunkDescriptor
from controller.ingestion.service import EventIngestionService
from controller.storage.store import DatabaseStore

database_url, marker = sys.argv[1:]
events = [
    {"event_id": 990001, "type": "lead", "name": "first"},
    {"event_id": 990003, "type": "call", "name": "second"},
]
class Simulator:
    @asynccontextmanager
    async def stream_chunk_lines(self, _chunk):
        def lines():
            yield json.dumps(events[0])
            open(marker, "w").write("inserted first batch; transaction is still open")
            import time
            time.sleep(120)
            yield json.dumps(events[1])
        yield lines()
async def main():
    store = DatabaseStore(database_url)
    await store.initialize()
    descriptor = ChunkDescriptor(900, "events", 2, 990001, 990003, {
        "chunk": 900, "kind": "events", "events": 2,
        "first_event_id": 990001, "last_event_id": 990003,
    })
    await EventIngestionService(Simulator()).ingest_descriptor(
        store, descriptor, checkpoint_key="crash-demo", batch_size=1
    )
    await store.close()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        worker,
        database_url,
        str(marker),
        cwd=Path(__file__).resolve().parents[2],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while (
            not marker.exists()
            and process.returncode is None
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.05)
        if not marker.exists():
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=2)
            pytest.fail(
                "worker did not reach partial uncommitted insert; "
                f"exit={process.returncode}; stdout={stdout!r}; stderr={stderr!r}"
            )
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=10)

    assert await postgres_store.get_event(990001) is None
    assert await postgres_store.get_event(990003) is None
    assert await postgres_store.get_checkpoint("crash-demo") is None

    payloads = [
        {"event_id": 990001, "type": "lead", "name": "first"},
        {"event_id": 990003, "type": "call", "name": "second"},
    ]
    body = "\n".join(json.dumps(item) for item in payloads) + "\n"
    descriptor = ChunkDescriptor(
        900,
        "events",
        2,
        990001,
        990003,
        {
            "chunk": 900,
            "kind": "events",
            "events": 2,
            "first_event_id": 990001,
            "last_event_id": 990003,
        },
    )
    service = EventIngestionService(_RestartSimulator(body))
    inserted = await service.ingest_descriptor(
        postgres_store,
        descriptor,
        checkpoint_key="crash-demo",
        batch_size=1,
    )
    replayed = await service.ingest_descriptor(
        postgres_store,
        descriptor,
        checkpoint_key="crash-demo",
        batch_size=1,
    )
    assert inserted == 2
    assert replayed == 0
    assert await postgres_store.get_checkpoint("crash-demo") == 900
    stored = [await postgres_store.get_event(event_id) for event_id in (990001, 990003)]
    assert all(stored)
