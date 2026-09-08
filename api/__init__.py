"""FastAPI transport layer.

Presentation only, per docs/ARCHITECTURE.md: no business logic lives here. Routers validate
requests, call an application/*_service.py use case, and serialize the result.
No fastapi import should appear outside this package (mirrors "no gr.* outside
ui/"); no sqlalchemy/ORM import should appear inside it (routers depend on
application services and repository interfaces, never on services/db/orm_models
or a raw DB session).
"""
