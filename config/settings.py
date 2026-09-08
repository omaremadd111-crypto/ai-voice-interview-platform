"""Environment-driven application settings. No provider SDK imports here."""
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_PROVIDERS = {"anthropic", "mistral", "openrouter", "nvidia"}
SUPPORTED_INTERVIEW_TRANSPORTS = {"null", "livekit"}
SUPPORTED_TTS_PROVIDERS = {"cartesia", "inworld"}
SUPPORTED_TURN_DETECTION_MODES = {"default", "vad"}
SUPPORTED_EMAIL_PROVIDERS = {"null", "smtp"}
# Realtime voice implementations. "v1" is retained for rollback.
SUPPORTED_VOICE_ENGINES = {"v1", "v2"}
# Per-provider TTS defaults, pinned to concrete registry entries (never the
# moving "-latest" aliases or the bare provider name) so a bare
# VOICE_TTS_PROVIDER=cartesia works with no further tuning while staying
# reproducible.
_DEFAULT_TTS_MODEL_BY_PROVIDER = {
    "cartesia": "cartesia/sonic-3",
    "inworld": "inworld/inworld-tts-2",
}


class Settings(BaseModel):
    mock_mode: bool = True
    llm_provider: str = "mistral"
    mistral_api_key: str | None = None
    mistral_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    nvidia_api_key: str | None = None
    nvidia_model: str | None = None
    max_doc_chars: int = 20000
    # Upper bound on an uploaded CV file's raw bytes, checked before parsing so a
    # huge file is rejected without being handed to pypdf/python-docx. Separate
    # from max_doc_chars, which caps the EXTRACTED text.
    max_cv_upload_bytes: int = 5 * 1024 * 1024
    max_follow_ups_per_question: int = 2
    log_level: str = "INFO"
    rubrics_path: Path = PROJECT_ROOT / "config" / "rubrics.json"
    reports_dir: Path = PROJECT_ROOT / "reports"
    # PostgreSQL persistence (services/db/). None means "no database configured" --
    # the Gradio/dev harness and the full test suite keep working on
    # InMemorySessionStore without it.
    database_url: str | None = None
    # Background calling-queue worker (P5). These tune reliability, not behaviour:
    # how long a worker's claim on a candidate stays valid before another worker
    # may recover it, how often an idle worker looks for work, and the
    # deterministic exponential backoff between retry attempts.
    queue_worker_lease_seconds: int = 300
    queue_worker_poll_interval_seconds: float = 5.0
    queue_retry_base_seconds: int = 30
    queue_retry_factor: int = 2
    queue_retry_max_seconds: int = 900
    # Automated screening pipeline (P7 phase 2): applications accepted from one
    # submitter IP in a rolling hour, before POST /api/v1/public/jobs/{slug}/apply
    # starts returning 429 -- see application/rate_limiter.py. Per-position daily
    # caps are configured per position instead (Screening setup tab), since they
    # are a recruiter choice, not a platform-wide default.
    public_apply_rate_limit_per_ip_per_hour: int = 5
    # LiveKit voice transport (P6). Null remains the default so Demo / Mock Mode
    # works end to end without credentials. Selecting "livekit" makes the queue
    # worker dispatch the named voice agent instead.
    interview_transport: str = "null"
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    livekit_agent_name: str = "hr-screening-agent"
    livekit_stt_model: str = "deepgram/nova-3"
    # TTS provider selection (Cartesia | Inworld), both served by LiveKit
    # Inference with the same LiveKit credentials -- no provider SDK is added.
    # The model catalog below is taken verbatim from the installed SDK's own
    # registry (livekit.agents.inference.tts.TTSModels, livekit-agents 1.6.8);
    # model ids are never guessed from memory.
    #
    # Resolution rules (validated together in _resolve_tts_provider):
    #   voice_tts_provider unset + LIVEKIT_TTS_MODEL set
    #       -> provider inferred from the model prefix (legacy .env files keep
    #          working unchanged, e.g. inworld/inworld-tts-2 -> inworld).
    #   voice_tts_provider unset, no model -> cartesia (new default).
    #   both set -> they must agree ("inworld" requires an inworld/... model),
    #       otherwise Settings raises: misconfiguration fails fast here instead
    #       of surfacing as a mid-interview gateway error. There is deliberately
    #       NO runtime fallback between providers.
    voice_tts_provider: str | None = None
    livekit_tts_model: str | None = None
    livekit_tts_voice: str | None = None
    voice_public_base_url: str = "http://localhost:5173"
    voice_join_timeout_seconds: int = 600
    voice_interview_timeout_seconds: int = 3600
    voice_transport_poll_interval_seconds: float = 1.0
    voice_token_ttl_seconds: int = 900
    # Voice semantic intent layer (P6.6 / Phase 1 of the conversational-quality
    # plan). Global kill switch: when disabled, VoiceConversationService never
    # calls the classifier and behaves exactly as the pre-Phase-1 regex-only
    # system did. Timeout default is not a guess -- it is set from a real,
    # measured Mistral latency spike against the actual classifier prompt
    # during provider evaluation: n=30 calls against mistral-small-2603,
    # p50=562ms, p95=1250ms, max=1688ms; the default below is roughly 2.4x the
    # observed max, giving headroom for normal network/provider jitter while
    # still bounding a genuine hang. Re-measure and adjust if the provider or
    # model changes.
    voice_semantic_layer_enabled: bool = True
    voice_intent_classifier_timeout_seconds: float = 4.0
    # Below this confidence, a classifier result is treated as if classification
    # had not happened at all (falls back to the pre-existing regex-only gate)
    # rather than trusting a low-confidence dont_know/genuine_refusal call to
    # suppress a follow-up. Provisional default tuned against the Phase 1
    # regression suite, not a large real-world sample -- expect to revisit.
    voice_intent_min_confidence: float = 0.55
    # Silence / presence layer (conversational-quality Phase 2). Independent kill
    # switch from the semantic layer. The four ladder timings are engineering
    # ESTIMATES rather than product truths -- every one is env-overridable so
    # they can be re-tuned from
    # VOICE_STATE_TRANSITION log data without a code change. The only hard rule
    # (validated below) is their ordering: each escalation must sit strictly above
    # the previous one, and think_pause must stay clearly above the STT max_delay
    # of 3.0s so a nudge never fires while endpointing is still resolving a real
    # answer.
    voice_silence_layer_enabled: bool = True
    voice_think_pause_seconds: float = 6.0
    voice_presence_check_seconds: float = 15.0
    voice_presence_retry_seconds: float = 25.0
    voice_no_response_seconds: float = 45.0
    voice_max_nudges_per_question: int = 2
    # How often Aimy will simply repeat or rephrase the same question (Phase 3)
    # before offering to move on instead of looping. Delivery-only: never
    # affects the interview plan, follow-up caps, or any score.
    voice_max_repeats_per_question: int = 2
    # How often the LiveKit agent's silence watchdog wakes to ask the coordinator
    # whether a suggestion is due. Pure implementation granularity: smaller means
    # nudges fire closer to their threshold at the cost of more wakeups.
    voice_silence_poll_interval_seconds: float = 0.5
    # A/B switch for end-of-turn detection (Batch 3 latency investigation).
    # "default" leaves LiveKit's own eager default in place: passing no
    # `turn_detection` key to TurnHandlingOptions makes the SDK supply its
    # semantic `inference.TurnDetector()` (a cloud End-of-Utterance model)
    # alongside the default Silero VAD -- today's production behaviour,
    # unchanged. "vad" explicitly requests turn_detection="vad", which
    # disables that semantic model entirely: only VAD silence detection plus
    # the existing fixed min_delay/max_delay endpointing timer decide when a
    # turn ends, with no cloud round trip and no risk of the semantic model's
    # "probably not finished" prediction forcing the delay up to max_delay.
    # Keep this environment-configurable so deployments can compare both paths.
    voice_turn_detection_mode: str = "default"

    # ---- V2 realtime voice core (voice-v2-integration) ----------------------
    # Which realtime voice implementation the LiveKit worker runs.
    #   "v2" (default) -- the proven Voice V2 core: streaming voice LLM,
    #        semantic dynamic turn detection, adaptive interruption.
    #   "v1" -- the previous CoreInterviewVoiceAgent. Kept for rollback until
    #        the V2 path passes real voice testing. Nothing about v1 was
    #        deleted; setting this back to "v1" restores it with no code change.
    voice_engine: str = "v2"

    # The streaming conversational LLM. V1 had NO LLM in the session at all --
    # it spoke pre-computed strings via say(), which is why it could not stream
    # and could not use preemptive generation. Served by LiveKit Inference on
    # the same LIVEKIT_* credentials as STT/TTS.
    livekit_voice_llm_model: str = "google/gemini-3-flash"

    # Turn-taking values carried over EXACTLY from V2 tag hr-voice-baseline-v1.
    # These are the tuned numbers; changing them changes the proven behaviour.
    voice_min_endpointing_delay: float = 0.4
    voice_max_endpointing_delay: float = 2.0
    voice_endpointing_alpha: float = 0.9
    voice_min_interruption_duration: float = 0.4
    voice_interruption_min_words: int = 1
    voice_false_interruption_timeout: float | None = 2.0
    voice_preemptive_generation: bool = True
    voice_preemptive_tts: bool = True

    # ---- Interview recording (P0) + voice transcript (P1) -------------------
    # Master switch. Off means no egress call is ever made and every interview
    # runs exactly as it does today; the recording row is simply never created.
    recording_enabled: bool = False
    # Object storage the LiveKit egress worker uploads to. Egress runs on
    # LiveKit's servers, so a reachable bucket is required -- it cannot write to
    # this machine. Any S3-compatible target works (S3, MinIO, R2).
    recording_s3_bucket: str | None = None
    recording_s3_region: str | None = None
    #: Custom endpoint for S3-compatible storage. Blank means real AWS S3.
    recording_s3_endpoint: str | None = None
    recording_s3_access_key: str | None = None
    recording_s3_secret_key: str | None = None
    #: MinIO and most S3-compatible servers need path-style addressing.
    recording_s3_force_path_style: bool = False
    #: Public base URL for playback, when the bucket is served over HTTP. Blank
    #: means the API returns the storage path and HR playback is unavailable
    #: until a base URL is configured.
    recording_public_base_url: str | None = None

    # How often the background flusher writes buffered transcript segments.
    # Batched rather than per-utterance so the database is touched a handful of
    # times per interview instead of once per turn.
    voice_transcript_flush_interval_seconds: float = 2.0

    # Wording version stored alongside each consent, so a later change to the
    # notice stays auditable against what a candidate actually agreed to.
    recording_consent_version: str = "v1"

    # Whether a candidate must give recorded consent before a room token is
    # issued. DELIBERATELY SEPARATE from recording_enabled: recording, R2
    # upload and transcript persistence all stay fully live with this off --
    # only the pre-join consent checkbox and the POST /api/v1/voice/consent
    # gate are affected. Defaults to False for local/dev testing so a candidate
    # can start immediately.
    #
    # The consent infrastructure itself (migration 0005, interview_consents
    # table, ConsentRepository, the /voice/consent-notice and /voice/consent
    # endpoints, VoiceConsentRequiredError) is NOT removed by this flag -- it
    # stays dormant and ready. Set RECORDING_CONSENT_REQUIRED=true to re-enable
    # the gate with no code change.
    recording_consent_required: bool = False

    # ---- Candidate idle watchdog (services/livekit/v2/idle_watchdog.py) -----
    # Proactive nudges when a candidate never starts answering. NOT part of
    # turn detection or endpointing -- endpointing decides when an utterance
    # ends; this handles an utterance that never begins. Off changes nothing
    # about the stable voice pipeline; the watchdog is simply never attached.
    voice_idle_nudge_enabled: bool = True
    # Silence after the interviewer stops speaking before "Take your time."
    voice_idle_first_nudge_seconds: float = 8.0
    # Further silence after that nudge before offering to repeat the question.
    voice_idle_second_nudge_seconds: float = 9.0

    # ---- Transactional email (P7 phase 3: interview-invitation delivery) ----
    # EmailPort provider selection, mirroring llm_provider. "null" (default)
    # logs a hashed recipient and returns success with no credentials --
    # Demo / Mock Mode stays complete, exactly like the mock LLM.
    email_provider: str = "null"
    email_from_address: str | None = None
    email_from_name: str | None = None
    email_reply_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    # Send retry budget before an email_outbox row settles as failed and a
    # recruiter sees it flagged. The backoff shape itself reuses
    # queue_retry_base_seconds/factor/max_seconds -- one deterministic backoff
    # policy in the codebase, not two independently-tuned ones.
    email_outbox_max_attempts: int = 5

    @field_validator("llm_provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported llm_provider '{v}'; must be one of {sorted(SUPPORTED_PROVIDERS)}")
        return v

    @field_validator("email_provider")
    @classmethod
    def _validate_email_provider(cls, v: str) -> str:
        if v not in SUPPORTED_EMAIL_PROVIDERS:
            raise ValueError(
                f"Unsupported email_provider '{v}'; must be one of {sorted(SUPPORTED_EMAIL_PROVIDERS)}"
            )
        return v

    @field_validator("email_outbox_max_attempts", "smtp_port")
    @classmethod
    def _positive_email_setting(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be a positive integer")
        return v

    @field_validator("interview_transport")
    @classmethod
    def _validate_interview_transport(cls, v: str) -> str:
        if v not in SUPPORTED_INTERVIEW_TRANSPORTS:
            raise ValueError(
                f"Unsupported interview_transport '{v}'; must be one of "
                f"{sorted(SUPPORTED_INTERVIEW_TRANSPORTS)}"
            )
        return v

    @field_validator("voice_engine")
    @classmethod
    def _validate_voice_engine(cls, v: str) -> str:
        normalized = (v or "").strip().casefold()
        if normalized not in SUPPORTED_VOICE_ENGINES:
            raise ValueError(
                f"Unsupported voice_engine '{v}'; must be one of "
                f"{sorted(SUPPORTED_VOICE_ENGINES)}"
            )
        return normalized

    @field_validator("voice_turn_detection_mode")
    @classmethod
    def _validate_turn_detection_mode(cls, v: str) -> str:
        if v not in SUPPORTED_TURN_DETECTION_MODES:
            raise ValueError(
                f"Unsupported voice_turn_detection_mode '{v}'; must be one of "
                f"{sorted(SUPPORTED_TURN_DETECTION_MODES)}"
            )
        return v

    @field_validator(
        "max_doc_chars",
        "max_follow_ups_per_question",
        "max_cv_upload_bytes",
        "queue_worker_lease_seconds",
        "queue_retry_base_seconds",
        "queue_retry_max_seconds",
        "voice_join_timeout_seconds",
        "voice_interview_timeout_seconds",
        "voice_token_ttl_seconds",
        "public_apply_rate_limit_per_ip_per_hour",
    )
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be a positive integer")
        return v

    @field_validator("voice_max_nudges_per_question")
    @classmethod
    def _at_least_one_nudge(cls, v: int) -> int:
        if v < 1:
            raise ValueError("voice_max_nudges_per_question must be at least 1")
        return v

    @field_validator("voice_max_repeats_per_question")
    @classmethod
    def _at_least_one_repeat(cls, v: int) -> int:
        if v < 1:
            raise ValueError("voice_max_repeats_per_question must be at least 1")
        return v

    @field_validator("queue_worker_poll_interval_seconds")
    @classmethod
    def _positive_float(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be a positive number")
        return v

    @field_validator("voice_transport_poll_interval_seconds", "voice_intent_classifier_timeout_seconds")
    @classmethod
    def _positive_voice_poll_interval(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be a positive number")
        return v

    @field_validator(
        "voice_think_pause_seconds",
        "voice_presence_check_seconds",
        "voice_presence_retry_seconds",
        "voice_no_response_seconds",
        "voice_silence_poll_interval_seconds",
    )
    @classmethod
    def _positive_silence_timing(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be a positive number")
        return v

    @model_validator(mode="after")
    def _silence_ladder_ordering(self) -> "Settings":
        """Escalation ladder ordering is a correctness constraint, not a tuning
        preference (plan section 11): a later escalation must never fire before
        an earlier one. think_pause must also sit strictly above the configured
        STT endpointing max_delay of 3.0s so R1 can never happen at default
        endpointing settings."""
        ladder = (
            ("voice_think_pause_seconds", self.voice_think_pause_seconds),
            ("voice_presence_check_seconds", self.voice_presence_check_seconds),
            ("voice_presence_retry_seconds", self.voice_presence_retry_seconds),
            ("voice_no_response_seconds", self.voice_no_response_seconds),
        )
        for (earlier_name, earlier_value), (later_name, later_value) in zip(ladder, ladder[1:]):
            if later_value <= earlier_value:
                raise ValueError(
                    f"{later_name} ({later_value}) must be strictly greater than "
                    f"{earlier_name} ({earlier_value}) -- the silence escalation ladder "
                    f"must be strictly increasing."
                )
        if self.voice_think_pause_seconds <= 3.0:
            raise ValueError(
                "voice_think_pause_seconds must stay above the STT endpointing "
                "max_delay (3.0s) or the nudge fires while a real answer may "
                "still be resolving."
            )
        return self

    @model_validator(mode="after")
    def _recording_requires_storage(self) -> "Settings":
        """Recording may only be switched on when it can actually be delivered.

        Egress runs on LiveKit's servers and uploads to object storage; without
        a bucket and credentials it would start, fail somewhere out of process,
        and leave interviews silently unrecorded. Failing at startup instead
        means a misconfiguration is discovered before an interview happens, not
        after one is lost.

        RECORDING_PUBLIC_BASE_URL is deliberately NOT required: without it the
        file is still created and HR still sees status, duration and the
        transcript -- only in-browser playback is unavailable, which is a
        degraded state, not a broken one.
        """
        if not self.recording_enabled:
            return self

        missing = [
            name
            for name, value in (
                ("RECORDING_S3_BUCKET", self.recording_s3_bucket),
                ("RECORDING_S3_ACCESS_KEY", self.recording_s3_access_key),
                ("RECORDING_S3_SECRET_KEY", self.recording_s3_secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "RECORDING_ENABLED=true requires storage configuration. Missing: "
                + ", ".join(missing)
                + ". LiveKit egress uploads from its own servers and cannot write "
                "to this machine's disk, so an S3-compatible bucket is required. "
                "Set RECORDING_ENABLED=false to run without recording."
            )
        return self

    @model_validator(mode="after")
    def _email_requires_smtp_config(self) -> "Settings":
        """Mirrors _recording_requires_storage: a misconfigured send provider
        must fail at startup, not silently drop a candidate's invitation
        somewhere out of process during their first apply."""
        if self.email_provider != "smtp":
            return self

        missing = [
            name
            for name, value in (
                ("SMTP_HOST", self.smtp_host),
                ("SMTP_USERNAME", self.smtp_username),
                ("SMTP_PASSWORD", self.smtp_password),
                ("EMAIL_FROM_ADDRESS", self.email_from_address),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "EMAIL_PROVIDER=smtp requires SMTP configuration. Missing: "
                + ", ".join(missing)
                + ". Set EMAIL_PROVIDER=null to run without sending real email."
            )
        return self

    @model_validator(mode="after")
    def _resolve_tts_provider(self) -> "Settings":
        """Resolve and validate the TTS provider/model pair (see field docs)."""
        model = self.livekit_tts_model
        provider = self.voice_tts_provider

        if provider is not None and provider not in SUPPORTED_TTS_PROVIDERS:
            raise ValueError(
                f"Unsupported voice_tts_provider '{provider}'; must be one of "
                f"{sorted(SUPPORTED_TTS_PROVIDERS)}."
            )

        def _model_provider(model_value: str) -> str:
            return model_value.split("/", 1)[0] if "/" in model_value else model_value

        if provider is None and model is not None:
            inferred = _model_provider(model)
            if inferred not in SUPPORTED_TTS_PROVIDERS:
                raise ValueError(
                    f"livekit_tts_model '{model}' does not start with a supported "
                    f"TTS provider {sorted(SUPPORTED_TTS_PROVIDERS)}; cannot infer "
                    f"voice_tts_provider."
                )
            provider = inferred

        if provider is None:
            provider = "cartesia"

        resolved_model = model or _DEFAULT_TTS_MODEL_BY_PROVIDER[provider]
        actual = _model_provider(resolved_model)
        if actual != provider:
            raise ValueError(
                f"voice_tts_provider '{provider}' does not match livekit_tts_model "
                f"'{resolved_model}' (provider '{actual}'). Align the pair or drop "
                f"LIVEKIT_TTS_MODEL to use the {provider} default "
                f"({_DEFAULT_TTS_MODEL_BY_PROVIDER[provider]})."
            )

        self.voice_tts_provider = provider
        self.livekit_tts_model = resolved_model
        return self

    @field_validator("voice_intent_min_confidence")
    @classmethod
    def _confidence_bounds(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("must be within [0, 1]")
        return v

    @field_validator("queue_retry_factor")
    @classmethod
    def _at_least_one(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @property
    def has_api_key(self) -> bool:
        if self.llm_provider == "mistral":
            return bool(self.mistral_api_key)
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openrouter":
            return bool(self.openrouter_api_key)
        if self.llm_provider == "nvidia":
            return bool(self.nvidia_api_key)
        return False

    @property
    def has_livekit_credentials(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)


def _float_from_env(raw: str | None, default: float) -> float:
    """Parse a float env var, falling back to the tuned default when unset."""
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Expected a number, got {raw!r}") from exc


def _optional_float_from_env(raw: str | None, default: float | None) -> float | None:
    """Like _float_from_env, but 'none' disables the value entirely."""
    if raw is None or not raw.strip():
        return default
    if raw.strip().casefold() in {"none", "off", "disabled"}:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Expected a number or 'none', got {raw!r}") from exc


def _bool_from_env(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env_file: str | Path | None = None) -> Settings:
    dotenv_path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)
    return Settings(
        mock_mode=_bool_from_env(os.getenv("MOCK_MODE"), True),
        llm_provider=os.getenv("LLM_PROVIDER", "mistral"),
        mistral_api_key=os.getenv("MISTRAL_API_KEY") or None,
        mistral_model=os.getenv("MISTRAL_MODEL") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        openrouter_model=os.getenv("OPENROUTER_MODEL") or None,
        nvidia_api_key=os.getenv("NVIDIA_API_KEY") or None,
        nvidia_model=os.getenv("NVIDIA_MODEL") or None,
        max_doc_chars=int(os.getenv("MAX_DOC_CHARS", "20000")),
        max_cv_upload_bytes=int(os.getenv("MAX_CV_UPLOAD_BYTES", str(5 * 1024 * 1024))),
        max_follow_ups_per_question=int(os.getenv("MAX_FOLLOW_UPS_PER_QUESTION", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        database_url=os.getenv("DATABASE_URL") or None,
        queue_worker_lease_seconds=int(os.getenv("QUEUE_WORKER_LEASE_SECONDS", "300")),
        queue_worker_poll_interval_seconds=float(os.getenv("QUEUE_WORKER_POLL_INTERVAL_SECONDS", "5")),
        queue_retry_base_seconds=int(os.getenv("QUEUE_RETRY_BASE_SECONDS", "30")),
        queue_retry_factor=int(os.getenv("QUEUE_RETRY_FACTOR", "2")),
        queue_retry_max_seconds=int(os.getenv("QUEUE_RETRY_MAX_SECONDS", "900")),
        public_apply_rate_limit_per_ip_per_hour=int(
            os.getenv("PUBLIC_APPLY_RATE_LIMIT_PER_IP_PER_HOUR", "5")
        ),
        interview_transport=os.getenv("INTERVIEW_TRANSPORT", "null"),
        livekit_url=os.getenv("LIVEKIT_URL") or None,
        livekit_api_key=os.getenv("LIVEKIT_API_KEY") or None,
        livekit_api_secret=os.getenv("LIVEKIT_API_SECRET") or None,
        livekit_agent_name=os.getenv("LIVEKIT_AGENT_NAME", "hr-screening-agent"),
        livekit_stt_model=os.getenv("LIVEKIT_STT_MODEL", "deepgram/nova-3"),
        voice_tts_provider=os.getenv("VOICE_TTS_PROVIDER") or None,
        livekit_tts_model=os.getenv("LIVEKIT_TTS_MODEL") or None,
        livekit_tts_voice=os.getenv("LIVEKIT_TTS_VOICE") or None,
        voice_public_base_url=os.getenv("VOICE_PUBLIC_BASE_URL", "http://localhost:5173").rstrip("/"),
        voice_join_timeout_seconds=int(os.getenv("VOICE_JOIN_TIMEOUT_SECONDS", "600")),
        voice_interview_timeout_seconds=int(os.getenv("VOICE_INTERVIEW_TIMEOUT_SECONDS", "3600")),
        voice_transport_poll_interval_seconds=float(
            os.getenv("VOICE_TRANSPORT_POLL_INTERVAL_SECONDS", "1")
        ),
        voice_token_ttl_seconds=int(os.getenv("VOICE_TOKEN_TTL_SECONDS", "900")),
        voice_semantic_layer_enabled=_bool_from_env(os.getenv("VOICE_SEMANTIC_LAYER_ENABLED"), True),
        voice_intent_classifier_timeout_seconds=float(
            os.getenv("VOICE_INTENT_CLASSIFIER_TIMEOUT_SECONDS", "4.0")
        ),
        voice_intent_min_confidence=float(os.getenv("VOICE_INTENT_MIN_CONFIDENCE", "0.55")),
        voice_silence_layer_enabled=_bool_from_env(
            os.getenv("VOICE_SILENCE_LAYER_ENABLED"), True,
        ),
        voice_think_pause_seconds=float(os.getenv("VOICE_THINK_PAUSE_SECONDS", "6.0")),
        voice_presence_check_seconds=float(os.getenv("VOICE_PRESENCE_CHECK_SECONDS", "15")),
        voice_presence_retry_seconds=float(os.getenv("VOICE_PRESENCE_RETRY_SECONDS", "25")),
        voice_no_response_seconds=float(os.getenv("VOICE_NO_RESPONSE_SECONDS", "45")),
        voice_max_nudges_per_question=int(os.getenv("VOICE_MAX_NUDGES_PER_QUESTION", "2")),
        voice_max_repeats_per_question=int(
            os.getenv("VOICE_MAX_REPEATS_PER_QUESTION", "2")
        ),
        voice_silence_poll_interval_seconds=float(
            os.getenv("VOICE_SILENCE_POLL_INTERVAL_SECONDS", "0.5")
        ),
        voice_turn_detection_mode=os.getenv("VOICE_TURN_DETECTION_MODE", "default"),
        voice_engine=os.getenv("VOICE_ENGINE", "v2"),
        livekit_voice_llm_model=os.getenv(
            "LIVEKIT_VOICE_LLM_MODEL", "google/gemini-3-flash"
        ),
        voice_min_endpointing_delay=_float_from_env(
            os.getenv("VOICE_MIN_ENDPOINTING_DELAY"), 0.4
        ),
        voice_max_endpointing_delay=_float_from_env(
            os.getenv("VOICE_MAX_ENDPOINTING_DELAY"), 2.0
        ),
        voice_endpointing_alpha=_float_from_env(os.getenv("VOICE_ENDPOINTING_ALPHA"), 0.9),
        voice_min_interruption_duration=_float_from_env(
            os.getenv("VOICE_MIN_INTERRUPTION_DURATION"), 0.4
        ),
        voice_interruption_min_words=int(os.getenv("VOICE_INTERRUPTION_MIN_WORDS", "1")),
        voice_false_interruption_timeout=_optional_float_from_env(
            os.getenv("VOICE_FALSE_INTERRUPTION_TIMEOUT"), 2.0
        ),
        voice_preemptive_generation=_bool_from_env(
            os.getenv("VOICE_PREEMPTIVE_GENERATION"), True
        ),
        voice_preemptive_tts=_bool_from_env(os.getenv("VOICE_PREEMPTIVE_TTS"), True),
        recording_enabled=_bool_from_env(os.getenv("RECORDING_ENABLED"), False),
        recording_s3_bucket=os.getenv("RECORDING_S3_BUCKET") or None,
        recording_s3_region=os.getenv("RECORDING_S3_REGION") or None,
        recording_s3_endpoint=os.getenv("RECORDING_S3_ENDPOINT") or None,
        recording_s3_access_key=os.getenv("RECORDING_S3_ACCESS_KEY") or None,
        recording_s3_secret_key=os.getenv("RECORDING_S3_SECRET_KEY") or None,
        recording_s3_force_path_style=_bool_from_env(
            os.getenv("RECORDING_S3_FORCE_PATH_STYLE"), False
        ),
        recording_public_base_url=os.getenv("RECORDING_PUBLIC_BASE_URL") or None,
        voice_transcript_flush_interval_seconds=_float_from_env(
            os.getenv("VOICE_TRANSCRIPT_FLUSH_INTERVAL_SECONDS"), 2.0
        ),
        recording_consent_version=os.getenv("RECORDING_CONSENT_VERSION", "v1"),
        recording_consent_required=_bool_from_env(
            os.getenv("RECORDING_CONSENT_REQUIRED"), False
        ),
        voice_idle_nudge_enabled=_bool_from_env(
            os.getenv("VOICE_IDLE_NUDGE_ENABLED"), True
        ),
        voice_idle_first_nudge_seconds=_float_from_env(
            os.getenv("VOICE_IDLE_FIRST_NUDGE_SECONDS"), 8.0
        ),
        voice_idle_second_nudge_seconds=_float_from_env(
            os.getenv("VOICE_IDLE_SECOND_NUDGE_SECONDS"), 9.0
        ),
        email_provider=os.getenv("EMAIL_PROVIDER", "null"),
        email_from_address=os.getenv("EMAIL_FROM_ADDRESS") or None,
        email_from_name=os.getenv("EMAIL_FROM_NAME") or None,
        email_reply_to=os.getenv("EMAIL_REPLY_TO") or None,
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_use_tls=_bool_from_env(os.getenv("SMTP_USE_TLS"), True),
        email_outbox_max_attempts=int(os.getenv("EMAIL_OUTBOX_MAX_ATTEMPTS", "5")),
    )
