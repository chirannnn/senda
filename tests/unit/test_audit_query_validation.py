"""Read-only audit query validation tests."""

from datetime import datetime
from uuid import UUID

import pytest

from controller.storage.audit import AuditCursor, AuditQuery


def test_audit_query_requires_bounded_pages_and_aware_ordered_times():
    with pytest.raises(ValueError, match="page size"):
        AuditQuery(page_size=501)
    with pytest.raises(ValueError, match="timezone"):
        AuditQuery(start_at=datetime(2027, 1, 1))
    with pytest.raises(ValueError, match="follow"):
        AuditQuery(
            start_at=datetime.fromisoformat("2027-01-02T00:00:00+00:00"),
            end_at=datetime.fromisoformat("2027-01-01T00:00:00+00:00"),
        )


def test_audit_cursor_is_explicit_and_query_filters_are_immutable_values():
    moment = datetime.fromisoformat("2027-01-01T00:00:00+00:00")
    query = AuditQuery(workflow_id="wf", config_version=2, page_size=25)
    cursor = AuditCursor(moment, UUID(int=2))
    resumed = AuditQuery(workflow_id="wf", config_version=2, page_size=25, after=cursor)
    assert resumed.after == cursor
    assert query.workflow_id == "wf"


def test_audit_query_rejects_boolean_as_integer_configuration():
    with pytest.raises(ValueError, match="config version"):
        AuditQuery(config_version=True)
