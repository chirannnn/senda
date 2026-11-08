"""PostgreSQL persistence guarantees for incident evidence."""

import pytest

from controller.errors import AmbiguousPostError, SimulatorHTTPError
from controller.incidents import request_candidate_suggestions
from controller.storage.audit import AuditEntry, AuditLog, AuditQuery
from controller.storage.store import DatabaseStore


@pytest.mark.anyio
async def test_incident_evidence_is_durable_and_idempotent(
    postgres_store: DatabaseStore,
):
    evidence = {
        "workflow_id": "incident-wf",
        "config_version": 3,
        "cutoff_event_id": 42,
        "person_count": 40,
    }
    async with postgres_store.transaction() as session:
        await postgres_store.ensure_workflow(session, "incident-wf")
        row, created = await postgres_store.save_incident(
            session,
            incident_id="incident-one",
            workflow_id="incident-wf",
            trigger_config_version=3,
            status="evidence_recorded",
            evidence=evidence,
            timeline=[{"event": "evidence_recorded", "cutoff_event_id": 42}],
        )
        assert created
        assert row.status == "evidence_recorded"

    async with postgres_store.transaction() as session:
        row, created = await postgres_store.save_incident(
            session,
            incident_id="incident-one",
            workflow_id="incident-wf",
            trigger_config_version=3,
            status="evidence_recorded",
            evidence=evidence,
            timeline=[{"event": "evidence_recorded", "cutoff_event_id": 42}],
        )
        assert not created
        assert row.evidence == evidence

    recovered = await postgres_store.get_incident("incident-one")
    assert recovered is not None
    assert recovered.timeline[0]["cutoff_event_id"] == 42


@pytest.mark.anyio
async def test_incident_audit_and_state_share_one_transaction(postgres_store):
    audit = AuditLog(postgres_store)
    with pytest.raises(RuntimeError, match="rollback fixture"):
        async with postgres_store.transaction() as session:
            await postgres_store.ensure_workflow(session, "incident-rollback-wf")
            await postgres_store.save_incident(
                session,
                incident_id="incident-rollback",
                workflow_id="incident-rollback-wf",
                trigger_config_version=1,
                status="evidence_recorded",
                evidence={"cutoff_event_id": 7},
                timeline=[{"event": "evidence_recorded"}],
            )
            await audit.append(
                session,
                AuditEntry(
                    action="incident_evidence_aggregated",
                    actor="controller",
                    reason="test transaction",
                    workflow_id="incident-rollback-wf",
                    resource_type="incident",
                    resource_id="incident-rollback",
                    evidence={"cutoff_event_id": 7},
                ),
            )
            raise RuntimeError("rollback fixture")
    assert await postgres_store.get_incident("incident-rollback") is None
    page = await audit.query(
        AuditQuery(
            action="incident_evidence_aggregated", resource_id="incident-rollback"
        )
    )
    assert page.records == ()


class _SuggestionAPI:
    def __init__(
        self, *, workflow_id="incident-suggestion-wf", credits=10, post_error=None
    ):
        self.workflow_id = workflow_id
        self.credits = credits
        self.post_error = post_error
        self.posts = 0

    async def get_workflow_config(self, workflow_id):
        return {
            "workflow_id": workflow_id,
            "version": 3,
            "config": {"prompt_version": "P1"},
        }

    async def list_workflows(self):
        return [
            {
                "workflow_id": self.workflow_id,
                "options": {"prompt_version": ["P1", "P2"]},
            }
        ]

    async def get_state(self):
        return {"credits_remaining": self.credits}

    async def suggest_fixes(self, workflow_id, problem):
        self.posts += 1
        if self.post_error:
            raise self.post_error
        return {
            "suggestions": [
                {"prompt_version": "P2", "summary": "Candidate"},
                {"prompt_version": "P99", "summary": "Unlisted"},
                {"prompt_version": "P1", "summary": "Current"},
            ],
            "credits_remaining": self.credits - 3,
        }


async def _seed_eligible_incident(
    store, incident_id="incident-suggestion", workflow_id="incident-suggestion-wf"
):
    async with store.transaction() as session:
        await store.ensure_workflow(session, workflow_id)
        await store.save_incident(
            session,
            incident_id=incident_id,
            workflow_id=workflow_id,
            trigger_config_version=3,
            status="evidence_recorded",
            evidence={"sufficient": True, "hallucination_rate": 0.4},
            timeline=[{"event": "evidence_recorded"}],
        )


@pytest.mark.anyio
async def test_suggestions_are_credit_checked_allowlisted_and_audited(postgres_store):
    await _seed_eligible_incident(postgres_store)
    api = _SuggestionAPI(credits=10)
    result = await request_candidate_suggestions(
        api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id="incident-suggestion",
        problem="Observed population QA rate increased",
    )
    record = await postgres_store.get_incident("incident-suggestion")
    assert result["status"] == "candidates_ready"
    assert record.evidence["suggestion"]["candidates"] == [
        {"prompt_version": "P2", "summary": "Candidate"}
    ]
    assert record.evidence["suggestion"]["credits_used_observed"] == 3
    assert [item["event"] for item in record.timeline][-2:] == [
        "suggestion_intent",
        "candidates_ready",
    ]
    assert api.posts == 1
    rows = await AuditLog(postgres_store).list_for_workflow("incident-suggestion-wf")
    assert [row.action for row in rows].count("incident_phase_changed") == 2


@pytest.mark.anyio
async def test_insufficient_credits_never_calls_suggestion_endpoint(postgres_store):
    workflow_id = "workflow-no-credits"
    await _seed_eligible_incident(postgres_store, "incident-no-credits", workflow_id)
    api = _SuggestionAPI(workflow_id=workflow_id, credits=2.5)
    result = await request_candidate_suggestions(
        api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id="incident-no-credits",
        problem="Observed QA signal",
    )
    record = await postgres_store.get_incident("incident-no-credits")
    assert result["status"] == record.status == "insufficient_credits"
    assert record.evidence["suggestion_credit_check"]["spent"] == 0
    assert api.posts == 0


@pytest.mark.anyio
async def test_402_and_ambiguous_suggestion_are_durable_and_not_retried(postgres_store):
    await _seed_eligible_incident(postgres_store, "incident-402", "workflow-402")
    api_402 = _SuggestionAPI(
        workflow_id="workflow-402",
        post_error=SimulatorHTTPError("POST", "/llm/suggest-fixes", 402),
    )
    result = await request_candidate_suggestions(
        api_402,
        postgres_store,
        AuditLog(postgres_store),
        incident_id="incident-402",
        problem="Observed QA signal",
    )
    assert result["status"] == "insufficient_credits"
    assert api_402.posts == 1

    await _seed_eligible_incident(
        postgres_store, "incident-ambiguous", "workflow-ambiguous"
    )
    api_ambiguous = _SuggestionAPI(
        workflow_id="workflow-ambiguous",
        post_error=AmbiguousPostError("/llm/suggest-fixes", "lost response"),
    )
    arguments = {
        "incident_id": "incident-ambiguous",
        "problem": "Observed QA signal",
    }
    first = await request_candidate_suggestions(
        api_ambiguous, postgres_store, AuditLog(postgres_store), **arguments
    )
    second = await request_candidate_suggestions(
        api_ambiguous, postgres_store, AuditLog(postgres_store), **arguments
    )
    assert first["status"] == second["status"] == "suggestion_ambiguous"
    assert api_ambiguous.posts == 1


@pytest.mark.anyio
async def test_orphaned_suggestion_intent_is_manual_review_without_post(postgres_store):
    await _seed_eligible_incident(
        postgres_store, "incident-restart", "workflow-restart"
    )
    async with postgres_store.transaction() as session:
        await postgres_store.update_incident(
            session,
            "incident-restart",
            status="suggestion_intent",
            timeline_entry={"event": "suggestion_intent"},
        )
    api = _SuggestionAPI(workflow_id="workflow-restart")
    result = await request_candidate_suggestions(
        api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id="incident-restart",
        problem="Observed QA signal",
    )
    assert result["status"] == "suggestion_ambiguous"
    assert result["manual_review_required"]
    assert api.posts == 0
