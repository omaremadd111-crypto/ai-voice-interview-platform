"""Application entrypoint: settings -> service -> thin Gradio UI."""
from pathlib import Path
from typing import Any

from application.interview_agent_service import InterviewAgentService
from config.settings import Settings, load_settings
from services.llm.factory import get_llm_service
from services.logging_service import configure_logging
from ui.gradio_app import create_gradio_app


def build_app(settings: Settings | None = None) -> tuple[Any, Settings]:
    resolved_settings = settings or load_settings()
    configure_logging(resolved_settings.log_level)
    llm_service = get_llm_service(resolved_settings)
    service = InterviewAgentService(
        settings=resolved_settings,
        llm_service=llm_service,
    )
    app = create_gradio_app(
        service,
        resolved_settings.reports_dir,
        is_mock=llm_service.is_mock,
    )
    return app, resolved_settings


def main() -> None:
    app, settings = build_app()
    reports_dir = Path(settings.reports_dir).resolve()
    app.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
        allowed_paths=[str(reports_dir)],
    )


if __name__ == "__main__":
    main()
