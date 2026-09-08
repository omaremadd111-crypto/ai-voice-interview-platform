"""Regression tests for the /positions latency investigation (P0 perf audit).

What the audit found: GET /api/v1/positions issues exactly two SQL round trips
per request (the auth lookup in get_current_user, then the position list
itself) -- no N+1, no duplicate auth lookup, no extra query hiding anywhere in
the dependency chain. The 1.3-2.5s observed in the browser was each of those
two round trips costing hundreds of milliseconds against a slow/misconfigured
Postgres endpoint (see the audit report), not application-level inefficiency.
These tests pin the query SHAPE (exactly two statements, always) so a future
change cannot silently turn this back into an N+1 without a test noticing --
they cannot pin absolute latency, because that number is dominated by network
distance to whatever TEST_DATABASE_URL happens to be for whoever runs this.

Also proves the request-timing instrumentation (api/request_timing.py) is
actually wired up: a Server-Timing header on the response, and one
API_REQUEST_TIMING structured log line per request.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

pytestmark = pytest.mark.usefixtures("pg_engine")


def _capture_sql(app, run) -> list[str]:
    """Every SQL statement executed while `run()` is in flight, in order."""
    statements: list[str] = []
    engine = app.state.engine

    def _before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split("\n")[0])

    event.listen(engine, "before_cursor_execute", _before)
    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return statements


def test_list_positions_issues_exactly_two_sql_statements(
    client: TestClient, auth_headers: dict,
) -> None:
    """One auth lookup + one position list -- proves no N+1 and no duplicate
    auth lookup for this endpoint. If FastAPI's dependency cache ever stopped
    collapsing repeated Depends(get_current_user) references, or a future
    change added a per-position query, this count would rise and this test
    would catch it before a browser Network tab ever would."""
    statements = _capture_sql(
        client.app, lambda: client.get("/api/v1/positions", headers=auth_headers)
    )
    assert len(statements) == 2, statements
    assert any("hr_users" in s for s in statements), statements
    assert any("positions" in s for s in statements), statements


def test_get_position_issues_exactly_two_sql_statements(
    client: TestClient, auth_headers: dict,
) -> None:
    """Same shape for the single-position read: auth lookup + one PK lookup."""
    created = client.post(
        "/api/v1/positions",
        json={"company_name": "Acme", "title": "Junior AI Engineer"},
        headers=auth_headers,
    ).json()

    statements = _capture_sql(
        client.app,
        lambda: client.get(f"/api/v1/positions/{created['id']}", headers=auth_headers),
    )
    assert len(statements) == 2, statements


def test_listing_many_positions_does_not_add_more_queries(
    client: TestClient, auth_headers: dict,
) -> None:
    """The N+1 check that actually matters: statement count must not scale
    with the number of positions returned."""
    for i in range(5):
        client.post(
            "/api/v1/positions",
            json={"company_name": "Acme", "title": f"Role {i}"},
            headers=auth_headers,
        )

    statements = _capture_sql(
        client.app, lambda: client.get("/api/v1/positions", headers=auth_headers)
    )
    # Still auth + one list query, regardless of how many rows that list query
    # returned -- a real N+1 would show one extra statement per position.
    assert len(statements) == 2, statements


def test_response_carries_a_server_timing_header(
    client: TestClient, auth_headers: dict,
) -> None:
    response = client.get("/api/v1/positions", headers=auth_headers)
    timing = response.headers.get("server-timing")
    assert timing is not None
    assert "total;dur=" in timing
    assert "sql;dur=" in timing


def test_request_timing_is_logged_as_a_structured_event(
    client: TestClient, auth_headers: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="interview_agent"):
        client.get("/api/v1/positions", headers=auth_headers)

    events = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "interview_agent"
    ]
    timing_events = [e for e in events if e.get("event") == "API_REQUEST_TIMING"]
    assert len(timing_events) == 1, events

    event_record = timing_events[0]
    assert event_record["path"] == "/api/v1/positions"
    assert event_record["method"] == "GET"
    assert event_record["status_code"] == 200
    assert event_record["sql_statement_count"] == 2
    assert event_record["total_ms"] >= event_record["sql_ms"] >= 0
