"""PostgreSQL persistence guarantees for incident evidence."""

import pytest

from controller.errors import AmbiguousPostError, SimulatorHTTPError
from controller.incidents import (
    request_candidate_suggestions,
    validate_incident_candidate,
    validation_credit_cost,
)
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
        self,
        *,
        workflow_id="incident-suggestion-wf",
        credits=10,
        post_error=None,
        validation_error=None,
        validation_response=None,
        config_version=3,
        validation_floor=0.9,
        bump_config_after_validation=False,
    ):
        self.workflow_id = workflow_id
        self.credits = credits
        self.post_error = post_error
        self.validation_error = validation_error
        self.validation_response = validation_response
        self.config_version = config_version
        self.validation_floor = validation_floor
        self.bump_config_after_validation = bump_config_after_validation
        self.posts = 0
        self.validation_posts = 0

    async def get_workflow_config(self, workflow_id):
        return {
            "workflow_id": workflow_id,
            "version": self.config_version,
            "config": {"prompt_version": "P1"},
        }

    async def list_workflows(self):
        return [
            {
                "workflow_id": self.workflow_id,
                "options": {"prompt_version": ["P1", "P2"]},
                "governance": {"min_validation_pass_rate": self.validation_floor},
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

    async def run_validation(self, workflow_id, prompt_version, sample_size):
        self.validation_posts += 1
        if self.validation_error:
            raise self.validation_error
        if self.validation_response is not None:
            result = self.validation_response
            if self.bump_config_after_validation:
                self.config_version += 1
            return result
        failures = round(sample_size * 0.05)
        result = {
            "validation_run_id": "val_00001",
            "workflow_id": workflow_id,
            "prompt_version": prompt_version,
            "n": sample_size,
            "failures": failures,
            "pass_rate": 1 - failures / sample_size,
            "ts": "2027-01-04T01:00:00+05:30",
            "credits_remaining": self.credits - validation_credit_cost(sample_size),
        }
        if self.bump_config_after_validation:
            self.config_version += 1
        return result


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


async def _seed_candidate_incident(store, incident_id, workflow_id):
    async with store.transaction() as session:
        await store.ensure_workflow(session, workflow_id)
        await store.save_incident(
            session,
            incident_id=incident_id,
            workflow_id=workflow_id,
            trigger_config_version=3,
            status="candidates_ready",
            evidence={
                "sufficient": True,
                "suggestion": {
                    "candidates": [{"prompt_version": "P2", "summary": "Candidate"}]
                },
            },
            timeline=[{"event": "candidates_ready"}],
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


@pytest.mark.anyio
async def test_validation_binds_candidate_config_and_workflow_floor(postgres_store):
    incident_id, workflow_id = "incident-validation-pass", "workflow-validation-pass"
    await _seed_candidate_incident(postgres_store, incident_id, workflow_id)
    api = _SuggestionAPI(workflow_id=workflow_id)
    result = await validate_incident_candidate(
        api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id=incident_id,
        prompt_version="P2",
        sample_size=100,
    )
    record = await postgres_store.get_incident(incident_id)
    evidence = record.evidence["validations"]["P2"]
    assert result["status"] == record.status == "candidate_validated"
    assert evidence["validation_run_id"] == "val_00001"
    assert evidence["config_version"] == evidence["config_version_after"] == 3
    assert evidence["pass_rate"] >= evidence["minimum_pass_rate"]
    assert evidence["credits_used_observed"] == 1
    assert api.validation_posts == 1


@pytest.mark.anyio
async def test_validation_below_workflow_floor_is_rejected(postgres_store):
    incident_id, workflow_id = "incident-validation-fail", "workflow-validation-fail"
    await _seed_candidate_incident(postgres_store, incident_id, workflow_id)
    api = _SuggestionAPI(workflow_id=workflow_id, validation_floor=0.97)
    result = await validate_incident_candidate(
        api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id=incident_id,
        prompt_version="P2",
        sample_size=100,
    )
    record = await postgres_store.get_incident(incident_id)
    assert result["status"] == record.status == "candidate_rejected"
    assert record.evidence["validations"]["P2"]["status"] == "rejected"


@pytest.mark.anyio
async def test_validation_mismatch_or_midrun_config_change_is_not_accepted(
    postgres_store,
):
    mismatch_id, mismatch_wf = (
        "incident-validation-mismatch",
        "workflow-validation-mismatch",
    )
    await _seed_candidate_incident(postgres_store, mismatch_id, mismatch_wf)
    mismatch_api = _SuggestionAPI(
        workflow_id=mismatch_wf,
        validation_response={
            "validation_run_id": "val-mismatch",
            "workflow_id": mismatch_wf,
            "prompt_version": "P1",
            "n": 100,
            "failures": 5,
            "pass_rate": 0.95,
            "ts": "2027-01-04T01:00:00+05:30",
            "credits_remaining": 9,
        },
    )
    mismatch = await validate_incident_candidate(
        mismatch_api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id=mismatch_id,
        prompt_version="P2",
        sample_size=100,
    )
    mismatch_record = await postgres_store.get_incident(mismatch_id)
    assert mismatch["status"] == "validation_failed"
    assert (
        mismatch_record.evidence["validations"]["P2"]["status"] == "stale_or_mismatched"
    )

    changed_id, changed_wf = (
        "incident-validation-changed",
        "workflow-validation-changed",
    )
    await _seed_candidate_incident(postgres_store, changed_id, changed_wf)
    changed_api = _SuggestionAPI(
        workflow_id=changed_wf, bump_config_after_validation=True
    )
    changed = await validate_incident_candidate(
        changed_api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id=changed_id,
        prompt_version="P2",
        sample_size=100,
    )
    changed_record = await postgres_store.get_incident(changed_id)
    assert changed["status"] == "validation_failed"
    assert (
        changed_record.evidence["validations"]["P2"]["status"] == "stale_or_mismatched"
    )


@pytest.mark.anyio
async def test_validation_stale_ambiguous_and_insufficient_credit_paths(postgres_store):
    stale_id, stale_wf = "incident-validation-stale", "workflow-validation-stale"
    await _seed_candidate_incident(postgres_store, stale_id, stale_wf)
    stale_api = _SuggestionAPI(workflow_id=stale_wf, config_version=4)
    stale = await validate_incident_candidate(
        stale_api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id=stale_id,
        prompt_version="P2",
        sample_size=100,
    )
    assert stale["status"] == "stale_or_unconfigured_validation"
    assert stale_api.validation_posts == 0

    ambiguous_id, ambiguous_wf = (
        "incident-validation-ambiguous",
        "workflow-validation-ambiguous",
    )
    await _seed_candidate_incident(postgres_store, ambiguous_id, ambiguous_wf)
    ambiguous_api = _SuggestionAPI(
        workflow_id=ambiguous_wf,
        validation_error=AmbiguousPostError("/validation/runs", "lost response"),
    )
    arguments = {
        "incident_id": ambiguous_id,
        "prompt_version": "P2",
        "sample_size": 100,
    }
    first = await validate_incident_candidate(
        ambiguous_api, postgres_store, AuditLog(postgres_store), **arguments
    )
    second = await validate_incident_candidate(
        ambiguous_api, postgres_store, AuditLog(postgres_store), **arguments
    )
    assert first["status"] == second["status"] == "validation_ambiguous"
    assert ambiguous_api.validation_posts == 1

    poor_id, poor_wf = "incident-validation-credit", "workflow-validation-credit"
    await _seed_candidate_incident(postgres_store, poor_id, poor_wf)
    poor_api = _SuggestionAPI(workflow_id=poor_wf, credits=0)
    poor = await validate_incident_candidate(
        poor_api,
        postgres_store,
        AuditLog(postgres_store),
        incident_id=poor_id,
        prompt_version="P2",
        sample_size=100,
    )
    assert poor["status"] == "insufficient_credits"
    assert poor_api.validation_posts == 0
