"""Per-request SQL timing: attributes cumulative SQL execution time to whichever
API request is currently in flight, via engine-level cursor-execute hooks.

Lives here, not in api/, because sqlalchemy stays confined to the persistence
layer and migrations (see tests/test_governance.py). api/request_timing.py
imports only the plain interface below -- SqlAccumulator, the ContextVar, and
instrument_engine_sql_timing() -- and never imports sqlalchemy itself.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import Engine, event


@dataclass
class SqlAccumulator:
    total_seconds: float = 0.0
    statement_count: int = 0


#: Set by the request-timing middleware for the duration of one request; read
#: by the engine hooks below. None outside any request (background workers,
#: migrations, scripts), where both hooks become a single ContextVar read plus
#: an early return.
current_sql_accumulator: ContextVar[SqlAccumulator | None] = ContextVar(
    "current_sql_accumulator", default=None
)


def instrument_engine_sql_timing(engine: Engine) -> None:
    """Attach SQL-timing hooks to one engine. Idempotent per engine instance is
    the caller's responsibility (api/request_timing.py does this once, lazily,
    on first request)."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        accumulator = current_sql_accumulator.get()
        if accumulator is not None:
            context._request_timing_sql_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        accumulator = current_sql_accumulator.get()
        start = getattr(context, "_request_timing_sql_start", None)
        if accumulator is not None and start is not None:
            accumulator.total_seconds += time.perf_counter() - start
            accumulator.statement_count += 1
