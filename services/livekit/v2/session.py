"""AgentSession assembly for the validated realtime voice pipeline.

This is the pipeline whose behaviour was proven by real voice testing:

    mic -> streaming STT -> turn handling -> streaming LLM -> streaming TTS

The single most consequential difference from the previous implementation
(``services/livekit/agent_server.py``) is the ``llm=`` argument. V1 built an
AgentSession with **no LLM and no VAD** and spoke pre-computed strings through
``session.say()``. That made two things impossible by construction:

  * streaming -- the whole reply had to exist before any audio could start;
  * preemptive generation -- there was no generation to run preemptively.

Here the LLM is in the session, so every spoken turn streams, and the interview
plan reaches it as context rather than as a pre-rendered script.

All four models are served by LiveKit Inference on the same ``LIVEKIT_*``
credentials the product already uses for STT and TTS. No new provider account
is required.
"""

from __future__ import annotations

from livekit.agents import inference
from livekit.agents.voice import AgentSession

from config.settings import Settings
from services.livekit.v2.turn_tuning import build_turn_handling, build_vad


def language_code(language: str) -> str:
    """Persona language name -> STT language code.

    Kept identical to the V1 implementation (``agent_server._language_code``)
    so an existing persona configured with "English"/"Arabic" keeps working
    unchanged across the engine switch.
    """
    common = {"english": "en", "arabic": "ar", "french": "fr", "spanish": "es"}
    normalized = (language or "").strip().casefold()
    if normalized in common:
        return common[normalized]
    if len(normalized) in {2, 5}:
        return normalized
    return "en"


def build_tts_kwargs(settings: Settings) -> dict[str, str]:
    """TTS construction arguments for the configured provider.

    Mirrors ``agent_server.build_tts_kwargs`` exactly: provider selection stays
    configuration-driven, validated at startup by config.settings, and this
    site never hardcodes a provider or falls back between them.
    """
    tts_kwargs: dict[str, str] = {"model": settings.livekit_tts_model or ""}
    if settings.livekit_tts_voice:
        tts_kwargs["voice"] = settings.livekit_tts_voice
    return tts_kwargs


def build_session(settings: Settings, *, language: str = "English") -> AgentSession:
    """Assemble the proven V2 pipeline.

    Every tuned value comes from :mod:`services.livekit.v2.turn_tuning`, which
    is the ported V2 configuration. Nothing about turn taking is decided here.
    """
    return AgentSession(
        # --- ear -------------------------------------------------------------
        # Streaming STT with interim results and word-aligned timestamps. The
        # alignment is load-bearing: adaptive interruption needs it to tell a
        # barge-in from a backchannel, and the min_words gate needs the
        # transcript to filter non-speech noise.
        stt=inference.STT(
            model=settings.livekit_stt_model,
            language=language_code(language),
        ),
        # Local VAD. Feeds endpointing and interruption detection. V1 had none.
        vad=build_vad(),
        # --- brain -----------------------------------------------------------
        # The conversational LLM. New in V2; V1 had no LLM in the session.
        llm=inference.LLM(model=settings.livekit_voice_llm_model),
        # --- voice -----------------------------------------------------------
        tts=inference.TTS(**build_tts_kwargs(settings)),
        # --- turn taking -----------------------------------------------------
        turn_handling=build_turn_handling(settings),
        # A candidate who has gone quiet this long is marked "away". Nothing in
        # this path acts on it -- the old SilenceCoordinator is deliberately not
        # reintroduced -- but the transition is logged, which is the data a
        # future presence feature would need instead of guessing.
        user_away_timeout=15.0,
    )
