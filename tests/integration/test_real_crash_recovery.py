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

from controller.governance.proposals import ProposalPayload, submit_proposal
from controller.governance.reconcile import apply_approved_proposal
from controller.governance.state_machine import ProposalStatus, poll_approval
from controller.ingestion.events import ChunkDescriptor
from controller.ingestion.service import EventIngestionService
from controller.storage.audit import AuditLog, AuditQuery
from controller.storage.store import DatabaseStore


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


@pytest.mark.anyio
async def test_pending_approval_survives_store_restart_and_applies_once(postgres_store):
    class Remote:
        def __init__(self):
            self.status = "pending"
            self.version = 1
            self.approval_posts = 0
            self.apply_posts = 0
            self.history = []
            self.approval = {
                "approval_id": "apr-crash-recovery",
                "proposal_id": "proposal-crash-recovery",
                "workflow_id": "workflow-crash-recovery",
                "status": self.status,
                "base_version": 1,
                "changed_dims": ["prompt_version"],
            }

        async def submit_approval(self, payload):
            self.approval_posts += 1
            assert payload["proposal_id"] == self.approval["proposal_id"]
            return dict(self.approval)

        async def get_approval(self, approval_id):
            assert approval_id == self.approval["approval_id"]
            self.approval["status"] = self.status
            return dict(self.approval)

        async def list_approvals(self, *, workflow_id=None, status=None):
            assert workflow_id in (None, self.approval["workflow_id"])
            return [dict(self.approval)]

        async def get_workflow_config(self, workflow_id):
            return {
                "workflow_id": workflow_id,
                "version": self.version,
                "config": {"prompt_version": "P2" if self.version == 1 else "P3"},
            }

        async def get_config_history(self, workflow_id):
            return list(self.history)

        async def apply_approval(self, workflow_id, approval_id):
            self.apply_posts += 1
            assert approval_id == self.approval["approval_id"]
            assert self.status == "approved"
            self.version = 2
            self.status = "applied"
            self.approval["status"] = "applied"
            self.history.append(
                {
                    "version": 2,
                    "actor": "controller_approved",
                    "changed_dims": ["prompt_version"],
                    "proposal_id": self.approval["proposal_id"],
                    "approval_id": approval_id,
                    "applied_at": "2027-01-04T01:00:00+05:30",
                }
            )
            return {
                "workflow_id": workflow_id,
                "version": 2,
                "changed_dims": ["prompt_version"],
            }

    remote = Remote()
    proposal = ProposalPayload(
        proposal_id="proposal-crash-recovery",
        workflow_id="workflow-crash-recovery",
        base_version=1,
        proposed_config={"prompt_version": "P3"},
        change_summary="Apply validated prompt P3",
        diff={"prompt_version": {"before": "P2", "after": "P3"}},
        affected_leads=20,
        dry_run={"validation_run_id": "val-crash"},
        impact_score=0.2,
        rationale="Crash recovery integration fixture",
        evidence={"validation_run_id": "val-crash"},
    )
    first_store = postgres_store
    audit = AuditLog(first_store)
    submitted = await submit_proposal(remote, first_store, audit, proposal)
    assert submitted["status"] == "pending"
    assert remote.approval_posts == 1
    operation = await first_store.get_outbox_operation(
        "approval-submission:proposal-crash-recovery"
    )
    assert operation is not None and operation.status == "succeeded"
    await first_store.close()

    database_url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    restarted = DatabaseStore(database_url)
    await restarted.initialize()
    await restarted.migrate()
    audit = AuditLog(restarted)
    repeated_submission = await submit_proposal(remote, restarted, audit, proposal)
    assert repeated_submission["status"] == "pending"
    assert remote.approval_posts == 1
    assert (
        await poll_approval(remote, restarted, audit, proposal.proposal_id, max_polls=1)
        == ProposalStatus.PENDING
    )
    remote.status = "approved"
    assert (
        await poll_approval(remote, restarted, audit, proposal.proposal_id, max_polls=1)
        == ProposalStatus.APPROVED
    )
    result = await apply_approved_proposal(
        remote, restarted, audit, proposal.proposal_id
    )
    assert result["status"] == "applied"
    assert remote.apply_posts == 1

    await restarted.close()
    reopened = DatabaseStore(database_url)
    await reopened.initialize()
    await reopened.migrate()
    stored = await reopened.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "applied"
    apply_intent = await reopened.get_outbox_operation(
        "approval-apply:apr-crash-recovery"
    )
    assert apply_intent is not None
    assert apply_intent.status == "succeeded" and apply_intent.attempts == 1
    repeated = await apply_approved_proposal(
        remote, reopened, AuditLog(reopened), proposal.proposal_id
    )
    assert repeated["status"] == "applied"
    assert remote.apply_posts == 1
    audit_rows = await AuditLog(reopened).query(
        AuditQuery(
            workflow_id="workflow-crash-recovery",
            action="approval_apply_prepared",
            page_size=10,
        )
    )
    assert len(audit_rows.records) == 1
    await reopened.close()
