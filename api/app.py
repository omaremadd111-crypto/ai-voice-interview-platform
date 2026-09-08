"""FastAPI application factory.

create_app() takes optional Settings/APISettings overrides so tests can inject a
TEST_DATABASE_URL-configured Settings and a fixed test JWT secret without
touching real environment variables or .env -- mirrors how
InterviewAgentService(settings=...) already supports test injection.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.errors import register_exception_handlers
from api.request_timing import install_request_timing
from api.routers.agent_configs import router as agent_configs_router
from api.routers.auth import router as auth_router
from api.routers.candidates import router as candidates_router
from api.routers.health import router as health_router
from api.routers.interview_plans import router as interview_plans_router
from api.routers.interviews import router as interviews_router
from api.routers.pipeline import router as pipeline_router
from api.routers.positions import router as positions_router
from api.routers.public import router as public_router
from api.routers.questions import position_questions_router, questions_router
from api.routers.queues import router as queues_router
from api.routers.screening_config import router as screening_config_router
from api.routers.voice import router as voice_router
from api.settings import APISettings, load_api_settings
from application.interview_agent_service import InterviewAgentService
from config.settings import Settings, load_settings
from services.db.engine import build_engine, build_session_factory
from services.db.postgres_session_store import PostgresSessionStore
from services.document_parser import DocumentParser
from services.livekit.gateway import LiveKitRoomGateway


def create_app(settings: Settings | None = None, api_settings: APISettings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    resolved_api_settings = api_settings or load_api_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(resolved_settings)
        session_factory = build_session_factory(engine)
        app.state.settings = resolved_settings
        app.state.api_settings = resolved_api_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.document_parser = DocumentParser(resolved_settings)
        app.state.interview_agent_service = InterviewAgentService(
            settings=resolved_settings,
            session_store=PostgresSessionStore(session_factory),
            document_parser=app.state.document_parser,
        )
        app.state.livekit_gateway = LiveKitRoomGateway(resolved_settings)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="HR Interview Agent API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_api_settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    # Logs one API_REQUEST_TIMING event per request (total vs. SQL vs. the rest)
    # and adds a Server-Timing response header. See api/request_timing.py.
    install_request_timing(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(positions_router)
    app.include_router(screening_config_router)
    app.include_router(pipeline_router)
    app.include_router(position_questions_router)
    app.include_router(questions_router)
    app.include_router(candidates_router)
    app.include_router(interview_plans_router)
    app.include_router(agent_configs_router)
    app.include_router(interviews_router)
    app.include_router(queues_router)
    app.include_router(voice_router)
    # Kill switch: the automated screening pipeline's unauthenticated surface
    # exists in the app's route table only when explicitly enabled -- disabled,
    # every /api/v1/public/* path 404s at the framework level, before any
    # handler or service code runs.
    if resolved_api_settings.public_applications_enabled:
        app.include_router(public_router)

    return app
