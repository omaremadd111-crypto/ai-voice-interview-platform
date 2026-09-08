"""Settings loader and rubric config loader/validator tests."""
from pathlib import Path

import pytest

from config.rubric_config import RubricConfigError, load_rubric_config
from config.settings import Settings, load_settings


def test_settings_defaults_favor_mock_mode(
    monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    for key in ("MOCK_MODE", "LLM_PROVIDER", "MISTRAL_API_KEY", "MISTRAL_MODEL",
                "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                "MAX_DOC_CHARS", "MAX_FOLLOW_UPS_PER_QUESTION", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    settings = load_settings(env_file=Path("does-not-exist.env"))
    assert settings.mock_mode is True
    assert settings.has_api_key is False
    assert settings.llm_provider == "mistral"
    assert settings.max_doc_chars == 20000
    assert settings.max_follow_ups_per_question == 2


def test_settings_reads_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    # load_dotenv uses override=False by design (a real shell env var should win
    # over a checked-in .env file), so the test must not let ambient OS env vars
    # (e.g. an ANTHROPIC_MODEL set for the outer shell) shadow the file's values.
    # isolated_environ additionally guarantees load_dotenv's injection of any
    # previously-absent file key is discarded at teardown instead of leaking
    # process-wide into later tests.
    for key in ("MOCK_MODE", "LLM_PROVIDER", "MISTRAL_API_KEY", "MISTRAL_MODEL",
                "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                "MAX_DOC_CHARS", "MAX_FOLLOW_UPS_PER_QUESTION", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MOCK_MODE=false\nLLM_PROVIDER=mistral\nMISTRAL_API_KEY=test-key-123\n"
        "MISTRAL_MODEL=mistral-test\n"
        "MAX_DOC_CHARS=5000\n",
        encoding="utf-8",
    )
    settings = load_settings(env_file=env_path)
    assert settings.mock_mode is False
    assert settings.has_api_key is True
    assert settings.mistral_model == "mistral-test"
    assert settings.max_doc_chars == 5000


def test_has_api_key_uses_only_the_selected_provider() -> None:
    mistral = Settings(
        llm_provider="mistral",
        mistral_api_key=None,
        anthropic_api_key="anthropic-test-key",
        openrouter_api_key="openrouter-test-key",
    )
    anthropic = Settings(
        llm_provider="anthropic",
        mistral_api_key="mistral-test-key",
        anthropic_api_key=None,
        openrouter_api_key="openrouter-test-key",
    )
    openrouter = Settings(
        llm_provider="openrouter",
        mistral_api_key="mistral-test-key",
        anthropic_api_key="anthropic-test-key",
        openrouter_api_key=None,
    )
    assert mistral.has_api_key is False
    assert anthropic.has_api_key is False
    assert openrouter.has_api_key is False


def test_has_api_key_true_when_openrouter_key_is_configured() -> None:
    settings = Settings(llm_provider="openrouter", openrouter_api_key="unit-test-key")
    assert settings.has_api_key is True


def test_settings_reads_openrouter_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    for key in ("MOCK_MODE", "LLM_PROVIDER", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
                "MISTRAL_API_KEY", "MISTRAL_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "MOCK_MODE=false\nLLM_PROVIDER=openrouter\nOPENROUTER_API_KEY=test-key-123\n"
        "OPENROUTER_MODEL=nvidia/nemotron-3.5-lightning:free\n",
        encoding="utf-8",
    )
    settings = load_settings(env_file=env_path)
    assert settings.llm_provider == "openrouter"
    assert settings.openrouter_api_key == "test-key-123"
    assert settings.openrouter_model == "nvidia/nemotron-3.5-lightning:free"
    assert settings.has_api_key is True


def test_settings_rejects_unsupported_provider() -> None:
    with pytest.raises(ValueError):
        Settings(llm_provider="openai")


def test_voice_transport_defaults_to_no_key_mock_channel() -> None:
    settings = Settings()
    assert settings.interview_transport == "null"
    assert settings.has_livekit_credentials is False


def test_settings_validate_livekit_transport_and_credentials() -> None:
    settings = Settings(
        interview_transport="livekit",
        livekit_url="wss://test.livekit.cloud",
        livekit_api_key="test-key",
        livekit_api_secret="test-secret",
    )
    assert settings.has_livekit_credentials is True
    with pytest.raises(ValueError):
        Settings(interview_transport="pstn")


def test_settings_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError):
        Settings(max_doc_chars=0)
    with pytest.raises(ValueError):
        Settings(max_follow_ups_per_question=-1)


def test_voice_semantic_layer_defaults_on_with_a_measured_timeout() -> None:
    settings = Settings()
    assert settings.voice_semantic_layer_enabled is True
    assert settings.voice_intent_classifier_timeout_seconds > 0
    assert 0.0 <= settings.voice_intent_min_confidence <= 1.0


def test_voice_semantic_layer_kill_switch_reads_from_env(
    monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    for key in ("VOICE_SEMANTIC_LAYER_ENABLED", "VOICE_INTENT_CLASSIFIER_TIMEOUT_SECONDS",
                "VOICE_INTENT_MIN_CONFIDENCE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOICE_SEMANTIC_LAYER_ENABLED", "false")
    settings = load_settings(env_file=Path("does-not-exist.env"))
    assert settings.voice_semantic_layer_enabled is False


def test_settings_rejects_invalid_voice_intent_tuning() -> None:
    with pytest.raises(ValueError):
        Settings(voice_intent_classifier_timeout_seconds=0)
    with pytest.raises(ValueError):
        Settings(voice_intent_min_confidence=1.5)
    with pytest.raises(ValueError):
        Settings(voice_intent_min_confidence=-0.1)


def test_voice_turn_detection_mode_defaults_to_baseline() -> None:
    """Batch 3 A/B switch: unset means the current, unchanged production
    behaviour (LiveKit's own eager turn-detection default), not the
    experimental VAD-only mode."""
    settings = Settings()
    assert settings.voice_turn_detection_mode == "default"


def test_voice_turn_detection_mode_reads_from_env(
    monkeypatch: pytest.MonkeyPatch, isolated_environ,
) -> None:
    monkeypatch.delenv("VOICE_TURN_DETECTION_MODE", raising=False)
    monkeypatch.setenv("VOICE_TURN_DETECTION_MODE", "vad")
    settings = load_settings(env_file=Path("does-not-exist.env"))
    assert settings.voice_turn_detection_mode == "vad"


def test_settings_rejects_unsupported_turn_detection_mode() -> None:
    with pytest.raises(ValueError):
        Settings(voice_turn_detection_mode="semantic")


def test_rubric_config_loads_and_sums_to_100() -> None:
    config = load_rubric_config(Path("config/rubrics.json"))
    for name, profile in config.profiles.items():
        total = sum(profile.weights.values())
        assert total == pytest.approx(100.0), f"{name} weights sum to {total}"
    assert config.default_profile in config.profiles


def test_rubric_config_get_profile_defaults() -> None:
    config = load_rubric_config(Path("config/rubrics.json"))
    default_profile = config.get_profile(None)
    assert default_profile is config.profiles[config.default_profile]
    with pytest.raises(ValueError):
        config.get_profile("does-not-exist")


def test_rubric_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RubricConfigError):
        load_rubric_config(tmp_path / "missing.json")


def test_rubric_config_rejects_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "rubrics.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RubricConfigError):
        load_rubric_config(bad)


def test_rubric_config_rejects_weights_not_summing_to_100(tmp_path: Path) -> None:
    bad = tmp_path / "rubrics.json"
    bad.write_text(
        """{"profiles": {"technical": {"description": "x", "weights":
        {"technical_knowledge": 50, "relevant_experience": 25, "problem_solving": 25,
        "job_requirement_coverage": 25, "communication": 0, "behavioral_competencies": 0}}},
        "default_profile": "technical", "thresholds": {"min_evidence_coverage": 0.5,
        "strong_evidence_min_score": 75, "proceed_min_score": 60}}""",
        encoding="utf-8",
    )
    with pytest.raises(RubricConfigError):
        load_rubric_config(bad)


def test_rubric_config_rejects_incomplete_profile(tmp_path: Path) -> None:
    bad = tmp_path / "rubrics.json"
    bad.write_text(
        """{"profiles": {"technical": {"description": "x",
        "weights": {"technical_knowledge": 100}}},
        "default_profile": "technical", "thresholds": {"min_evidence_coverage": 0.5,
        "strong_evidence_min_score": 75, "proceed_min_score": 60}}""",
        encoding="utf-8",
    )
    with pytest.raises(RubricConfigError):
        load_rubric_config(bad)


def test_rubric_config_rejects_unknown_default_profile(tmp_path: Path) -> None:
    bad = tmp_path / "rubrics.json"
    bad.write_text(
        """{"profiles": {"technical": {"description": "x", "weights":
        {"technical_knowledge": 30, "relevant_experience": 25, "problem_solving": 20,
        "job_requirement_coverage": 15, "communication": 10, "behavioral_competencies": 0}}},
        "default_profile": "general", "thresholds": {"min_evidence_coverage": 0.5,
        "strong_evidence_min_score": 75, "proceed_min_score": 60}}""",
        encoding="utf-8",
    )
    with pytest.raises(RubricConfigError):
        load_rubric_config(bad)
