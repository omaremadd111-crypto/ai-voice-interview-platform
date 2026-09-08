"""LiveKit voice-agent process entrypoint: ``python voice_agent.py dev``.

Which realtime implementation runs is decided by ``VOICE_ENGINE``:

    VOICE_ENGINE=v2  (default)  the proven Voice V2 core
    VOICE_ENGINE=v1             the previous CoreInterviewVoiceAgent

Both are present. V1 was not deleted, and switching back is an environment
change with no code change, so a failed real-voice test can be rolled back in
the time it takes to restart the worker.
"""
from config.settings import load_settings


def main() -> None:
    settings = load_settings()
    if settings.voice_engine == "v1":
        from services.livekit.agent_server import run_voice_agent

        run_voice_agent()
    else:
        from services.livekit.v2.agent_server_v2 import run_voice_agent_v2

        run_voice_agent_v2()


if __name__ == "__main__":
    main()
