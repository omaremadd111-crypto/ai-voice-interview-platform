"""Per-request timing instrumentation: request -> auth -> service -> DB -> SQL.

Built from what a real investigation of a slow /positions endpoint found: the
bottleneck was two DB round trips (the auth lookup in get_current_user, then
the service's own query) each taking hundreds of milliseconds against a
slow/misconfigured Postgres endpoint, with negligible time anywhere else
(session checkout, ORM overhead, JSON serialization). A coarse split -- total
wall time vs. cumulative SQL time vs. statement count -- is what actually
separates "the database is slow" from "something in our own code is slow",
which is the question that matters for every future regression here. It does
not need a named stage per FastAPI dependency to answer that question, and
adding one would mean hooking dependency resolution order for a benefit this
endpoint's own profiling already showed is not there for typical CRUD routes.

SQL time is attributed to the current request via a contextvar that
services/db/timing.py's engine hooks read; requests never see each other's
numbers even though FastAPI may interleave several concurrently on the same
event loop. The actual sqlalchemy Engine event hooks live in that module, not
here -- sqlalchemy stays confined to the persistence layer and migrations
(see tests/test_governance.py); this module only consumes its plain interface.
"""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from services.db.timing import (
    SqlAccumulator,
    current_sql_accumulator,
    instrument_engine_sql_timing,
)
from services.logging_service import Event, log_event


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Logs one API_REQUEST_TIMING event per request: total vs. SQL vs. the rest.

    ``non_sql_ms`` covers everything SQL timing cannot see by construction:
    JWT decode, Pydantic validation/serialization, routing, and any in-process
    computation. A request where non_sql_ms is the large number is a code
    problem; one where sql_ms dominates is a database/connection problem --
    that split is the actionable signal this middleware exists to produce.
    """

    async def dispatch(self, request: Request, call_next):
        # Lazy, one-time instrumentation of the app's engine. Deferred to first
        # request (rather than done at add_middleware() time in create_app())
        # because the engine is only built inside the lifespan context manager,
        # which runs after middleware registration; app.state.engine is always
        # populated by the time any request reaches here.
        if not getattr(request.app.state, "_sql_timing_instrumented", False):
            instrument_engine_sql_timing(request.app.state.engine)
            request.app.state._sql_timing_instrumented = True

        accumulator = SqlAccumulator()
        token = current_sql_accumulator.set(accumulator)
        request_start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            current_sql_accumulator.reset(token)

        total_seconds = time.perf_counter() - request_start
        sql_ms = accumulator.total_seconds * 1000
        total_ms = total_seconds * 1000

        log_event(
            Event.API_REQUEST_TIMING,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            total_ms=round(total_ms, 1),
            sql_ms=round(sql_ms, 1),
            sql_statement_count=accumulator.statement_count,
            non_sql_ms=round(total_ms - sql_ms, 1),
        )
        response.headers["Server-Timing"] = (
            f"total;dur={total_ms:.1f}, sql;dur={sql_ms:.1f}"
        )
        return response


def install_request_timing(app: FastAPI) -> None:
    """Register the timing middleware. Safe to call before the app has an engine --
    see RequestTimingMiddleware.dispatch for why the engine hook is deferred."""
    app.add_middleware(RequestTimingMiddleware)
