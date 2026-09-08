"""Cartesia/Inworld TTS provider selection through LiveKit Inference.

The model catalog is verified against the installed SDK's own registry
(livekit.agents.inference.tts.TTSModels); these tests pin that contract so an
SDK upgrade that drops or renames entries fails loudly here rather than
mid-interview.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.settings import Settings, load_settings
from services.livekit.agent_server import build_tts_kwargs


# ---- settings resolution -------------------------------------------------------

# Keys a TTS-provider env file is allowed to influence; scrubbed before every
# load_settings() call below so ambient shell state can never decide an assert.
_TTS_ENV_KEYS = ("VOICE_TTS_PROVIDER", "LIVEKIT_TTS_MODEL", "LIVEKIT_TTS_VOICE")


def test_default_provider_is_cartesia_with_a_pinned_model() -> None:
    settings = Settings()
    assert settings.voice_tts_provider == "cartesia"
    assert settings.livekit_tts_model == "cartesia/sonic-3"


def test_legacy_env_with_only_inworld_model_infers_the_provider(
    tmp_path, monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    """Pre-existing .env files that pin LIVEKIT_TTS_MODEL=inworld/... keep
    working unchanged: the provider is inferred from the model prefix."""
    for key in _TTS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("LIVEKIT_TTS_MODEL=inworld/inworld-tts-2\n", encoding="utf-8")
    settings = load_settings(env_file=env_path)
    assert settings.voice_tts_provider == "inworld"
    assert settings.livekit_tts_model == "inworld/inworld-tts-2"


def test_explicit_cartesia_selection_uses_per_provider_defaults() -> None:
    settings = Settings(voice_tts_provider="cartesia")
    assert settings.livekit_tts_model == "cartesia/sonic-3"


def test_explicit_inworld_selection_keeps_inworld_fully_supported() -> None:
    settings = Settings(voice_tts_provider="inworld")
    assert settings.voice_tts_provider == "inworld"
    assert settings.livekit_tts_model == "inworld/inworld-tts-2"


@pytest.mark.parametrize("model", (
    "cartesia/sonic-3",
    "cartesia/sonic-3.5",
    "cartesia/sonic-2",
    "cartesia/sonic-turbo",
))
def test_every_registered_cartesia_model_selects_cleanly(model: str) -> None:
    settings = Settings(voice_tts_provider="cartesia", livekit_tts_model=model)
    assert settings.livekit_tts_model == model


def test_voice_id_passes_through_for_cartesia() -> None:
    settings = Settings(
        voice_tts_provider="cartesia",
        livekit_tts_model="cartesia/sonic-3",
        livekit_tts_voice="71a7cecd-a394-4961-a4c4-46c35ba8f981",
    )
    assert settings.livekit_tts_voice == "71a7cecd-a394-4961-a4c4-46c35ba8f981"


def test_invalid_provider_value_is_rejected_with_clear_error() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings(voice_tts_provider="elevenlabs")
    message = str(excinfo.value)
    assert "elevenlabs" in message
    assert "cartesia" in message and "inworld" in message


@pytest.mark.parametrize(("provider", "model"), (
    ("cartesia", "inworld/inworld-tts-2"),
    ("inworld", "cartesia/sonic-3"),
))
def test_mismatched_provider_and_model_fail_fast(provider: str, model: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings(voice_tts_provider=provider, livekit_tts_model=model)
    message = str(excinfo.value)
    assert provider in message and model in message


def test_unsupported_model_prefix_cannot_be_inferred() -> None:
    with pytest.raises(ValueError) as excinfo:
        Settings(livekit_tts_model="notaprovider/x")
    assert "notaprovider/x" in str(excinfo.value)


# ---- SDK registry contract ------------------------------------------------------


def test_installed_sdk_registry_still_serves_both_providers() -> None:
    """Guard against LiveKit SDK upgrades silently dropping a provider."""
    from livekit.agents.inference import tts as tts_module

    def _literals(union) -> set[str]:
        return {item for literal in union.__args__ for item in literal.__args__}

    registered = _literals(tts_module.TTSModels)
    assert {"cartesia", "cartesia/sonic-3"} <= registered
    assert "inworld/inworld-tts-2" in registered


# ---- TTS construction through LiveKit Inference ----------------------------------


@pytest.fixture
def dummy_livekit_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # inference.TTS mints an inference-grant JWT from these at construction;
    # values are dummies and never leave the process, let alone the log stream.
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey-dummy-not-real")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "dummy-secret-not-real")


def test_build_tts_kwargs_shape_is_provider_independent(dummy_livekit_credentials) -> None:
    kwargs = build_tts_kwargs(Settings(voice_tts_provider="cartesia"))
    assert kwargs == {"model": "cartesia/sonic-3"}
    kwargs = build_tts_kwargs(Settings(
        voice_tts_provider="inworld", livekit_tts_voice="elena",
    ))
    assert kwargs == {"model": "inworld/inworld-tts-2", "voice": "elena"}


def test_cartesia_configuration_produces_expected_tts_construction(
    dummy_livekit_credentials,
) -> None:
    from livekit.agents import inference

    settings = Settings(
        voice_tts_provider="cartesia",
        livekit_tts_voice="71a7cecd-a394-4961-a4c4-46c35ba8f981",
    )
    instance = inference.TTS(**build_tts_kwargs(settings))
    assert instance._opts.model == "cartesia/sonic-3"
    assert instance._opts.voice == "71a7cecd-a394-4961-a4c4-46c35ba8f981"
    assert instance.provider == "livekit"


def test_inworld_configuration_still_produces_expected_tts_construction(
    dummy_livekit_credentials,
) -> None:
    from livekit.agents import inference

    settings = Settings(voice_tts_provider="inworld", livekit_tts_voice="elena")
    instance = inference.TTS(**build_tts_kwargs(settings))
    assert instance._opts.model == "inworld/inworld-tts-2"
    assert instance._opts.voice == "elena"


def test_bare_cartesia_configuration_needs_no_further_tuning(
    tmp_path, monkeypatch: pytest.MonkeyPatch, isolated_environ,
    dummy_livekit_credentials,
) -> None:
    """A deployment setting only VOICE_TTS_PROVIDER=cartesia must work."""
    from livekit.agents import inference

    for key in _TTS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Deliberately a temp file, NOT load_settings(env_file=None): the latter
    # loads the developer's real .env, contradicting this test's premise and
    # leaking its contents (credentials included) into os.environ for every
    # later test via load_dotenv(override=False).
    env_path = tmp_path / ".env"
    env_path.write_text("VOICE_TTS_PROVIDER=cartesia\n", encoding="utf-8")
    settings = load_settings(env_file=env_path)
    assert settings.mock_mode is True  # nothing but the provider was configured
    assert settings.llm_provider == "mistral"
    assert settings.livekit_tts_model == "cartesia/sonic-3"
    instance = inference.TTS(**build_tts_kwargs(settings))
    assert instance._opts.model == "cartesia/sonic-3"


def test_tts_settings_tests_pass_in_either_execution_order() -> None:
    """P0-1 regression net: run the legacy-env inference test and the
    bare-cartesia construction test in BOTH orders, each in a fresh
    interpreter. Guards against the load_dotenv(override=False) os.environ
    injection ever re-introducing order dependence (delenv(raising=False)
    records nothing for absent keys, so an unisolated injection used to
    survive teardown and poison whichever test ran next). Ambient TTS
    variables are scrubbed from the child environment so this asserts THIS
    suite's isolation, never the developer's shell state."""
    root = Path(__file__).resolve().parent.parent
    module = "tests/test_voice_tts_provider.py"
    legacy = f"{module}::test_legacy_env_with_only_inworld_model_infers_the_provider"
    bare = f"{module}::test_bare_cartesia_configuration_needs_no_further_tuning"
    child_env = {
        key: value for key, value in os.environ.items() if key not in _TTS_ENV_KEYS
    }
    for order in ((legacy, bare), (bare, legacy)):
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "--no-header",
                "-p", "no:randomly", "-p", "no:cacheprovider",
                *order,
            ],
            cwd=root,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, (
            f"TTS settings tests failed when run as {[part.split('::')[-1] for part in order]}:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def test_production_agent_session_assembly_constructs_with_cartesia(
    dummy_livekit_credentials,
) -> None:
    """The exact STT+TTS+turn-handling assembly agent_server performs builds
    cleanly when the selected provider is Cartesia (local construction only --
    no room connection, no synthesis)."""
    from livekit.agents import AgentSession, inference

    from services.livekit.agent_server import _turn_handling_options

    settings = Settings(voice_tts_provider="cartesia")

    # Both inference.TTS and AgentSession resolve the *current* event loop via
    # the deprecated get_event_loop() path; prior asyncio.run()-based tests
    # leave none current, so install a dedicated loop for the assembly.
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        tts = inference.TTS(**build_tts_kwargs(settings))
        session = AgentSession(
            stt=inference.STT(model=settings.livekit_stt_model, language="en"),
            tts=tts,
            turn_handling=_turn_handling_options(allow_interruptions=True),
        )
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def test_production_agent_session_assembly_accepts_the_vad_turn_detection_experiment(
    dummy_livekit_credentials,
) -> None:
    """Batch 3: the experimental turn_detection_mode="vad" string is a value
    the real installed LiveKit SDK accepts for AgentSession construction (not
    just a dict key this project invented) -- caught here rather than only at
    a real voice test, since AgentSession validates turn_detection eagerly at
    construction against the actual VAD/STT/LLM it was given."""
    from livekit.agents import AgentSession, inference

    from services.livekit.agent_server import _turn_handling_options

    settings = Settings(voice_tts_provider="cartesia")

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        tts = inference.TTS(**build_tts_kwargs(settings))
        session = AgentSession(
            stt=inference.STT(model=settings.livekit_stt_model, language="en"),
            tts=tts,
            turn_handling=_turn_handling_options(
                allow_interruptions=True, turn_detection_mode="vad",
            ),
        )
    finally:
        asyncio.set_event_loop(None)
        loop.close()

    # AgentSessionOptions does not re-expose instances; assert on the very
    # instance handed to the session.
    assert tts._opts.model == "cartesia/sonic-3"
    assert session is not None
