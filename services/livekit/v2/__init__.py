"""The current V2 realtime voice core.

This package owns the realtime path and nothing else. It never calls the
database, the evaluator, the report generator or any HR LLM synchronously --
every crossing into HR logic goes through ``asyncio.to_thread`` in
``director.py``, which is the single adapter boundary.

The previous implementation (``services/livekit/agent_server.py``) is untouched
and still selectable with ``VOICE_ENGINE=v1`` for rollback.
"""
