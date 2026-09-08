"""Validated turn-taking configuration for the realtime voice path.

The settings are pinned by regression tests. Retune them only with real-call
evidence and update those tests in the same change.

Turn taking decides whether the conversation feels human.

The four conversational requirements map onto four distinct mechanisms. They
are separated here rather than buried in the session constructor because this
file is the one that gets tuned by ear, over and over, during this phase.

    requirement                          mechanism
    -----------------------------------  -------------------------------------
    agent does not cut off thinking      semantic turn detector
      pauses                               (inference.TurnDetector)
    user can pause naturally             dynamic endpointing
                                           (learns the speaker's own rhythm)
    user can interrupt the agent         adaptive interruption
                                           (ML barge-in vs. backchannel)
    low latency                          preemptive generation
                                           (LLM starts before the turn ends)

A note on what NOT to do, because it is the trap this file exists to avoid:
a *fixed* endpointing delay forces a single silence threshold on every turn.
Set it short and the agent talks over anyone who pauses to think; set it long
and every single reply is late, including the instant ones. There is no value
that is right for both, which is why the two mechanisms below -- a semantic
decision plus a delay that adapts to the speaker -- replace it.
"""

from __future__ import annotations

from livekit.agents import inference
from livekit.agents.voice.turn import (
    EndpointingOptions,
    InterruptionOptions,
    PreemptiveGenerationOptions,
    TurnHandlingOptions,
)

from config.settings import Settings


def build_turn_detector() -> inference.TurnDetector:
    """The semantic end-of-turn model.

    This is the single most important component for "do not interrupt a
    thinking pause". It reads the conversation transcript, not just the audio
    energy, and produces an end-of-turn probability -- so it can tell that

        "I worked there for about, um..."          (obviously unfinished)

    is not the same silence as

        "...and that was about it."                (obviously finished)

    even though a VAD hears an identical gap in both. Silence duration alone
    cannot make that distinction, which is why VAD-only turn detection always
    ends up either impatient or sluggish.

    Left at the default version so it tracks the gateway's current best model;
    pin ``version=`` here if a run needs to be exactly reproducible.
    """
    return inference.TurnDetector()


def build_vad() -> inference.VAD:
    """Voice activity detection: the fast, cheap speech/no-speech signal.

    Runs locally via ``livekit-local-inference`` (Silero), so the decision
    "is someone talking right now" costs no network round trip. It feeds two
    things: the endpointing timer, and interruption detection while the agent
    is speaking.

    ``min_silence_duration`` is intentionally short. This is *not* the
    end-of-turn threshold -- it only marks where an utterance stopped. How
    long to wait after that point is the endpointing decision below, and
    conflating the two is how agents end up interrupting people.
    """
    return inference.VAD(
        min_speech_duration=0.05,
        min_silence_duration=0.25,
        # Half a second of audio before speech onset is prepended to the
        # buffer, so a turn starting mid-word is not clipped by the STT.
        prefix_padding_duration=0.5,
        activation_threshold=0.5,
    )


def build_turn_handling(settings: Settings) -> TurnHandlingOptions:
    """Assemble the turn-handling policy from validated settings."""

    endpointing = EndpointingOptions(
        # "dynamic" learns how long *this particular speaker* pauses mid-thought
        # and raises the floor toward that value, bounded by max_delay. A slow,
        # deliberate speaker is given more room automatically; a fast one keeps
        # snappy replies. The alternative, "fixed", applies one threshold to
        # everyone -- see the module docstring.
        mode="dynamic",
        min_delay=settings.voice_min_endpointing_delay,
        max_delay=settings.voice_max_endpointing_delay,
        alpha=settings.voice_endpointing_alpha,
    )

    interruption = InterruptionOptions(
        enabled=True,
        # "adaptive" is an ML classifier over the overlapping audio; it
        # separates a real barge-in ("wait, no --") from a backchannel
        # ("mhm", "yeah", "right"). VAD-based interruption cannot: to a VAD,
        # an agreeing "mhm" is speech, so the agent stops talking every time
        # the user makes an encouraging noise, which reads as skittish.
        #
        # Requires a streaming STT with word-aligned transcripts plus a VAD --
        # both provided in agent.py -- or the SDK falls back to VAD mode and
        # logs a warning.
        mode="adaptive",
        # Drop audio captured while the agent is speaking uninterruptibly, so
        # it is not replayed into the next turn as phantom user speech.
        discard_audio_if_uninterruptible=True,
        # A cough or a chair scrape is shorter than this and will not stop the
        # agent mid-sentence.
        min_duration=settings.voice_min_interruption_duration,
        # Require at least one *transcribed word* before a barge-in counts.
        #
        # This is the fix for non-speech noise. A cough is real audio of real
        # duration, so min_duration alone does not stop it -- but it transcribes
        # to zero words, and 0 < 1 fails this gate. Speech produces at least one
        # word and still interrupts normally.
        #
        # Critically, the SDK enforces this on the *audio-activity* interruption
        # path (agent_activity.py, _on_interruption_by_audio_activity), which is
        # the same path re-enabled by _fallback_to_vad_interruption. So when the
        # adaptive detector times out and LiveKit degrades to VAD-based
        # interruption, this gate is still applied and coughs are still ignored
        # -- which is exactly when a VAD needs the help most, since a VAD alone
        # cannot tell a cough from a word.
        #
        # The gate is skipped entirely when the value is 0, or when there is no
        # STT to produce a transcript. We always have one (agent.py).
        #
        # Cost: a legitimate barge-in now waits for its first word to be
        # transcribed rather than firing on raw audio activity.
        min_words=settings.voice_interruption_min_words,
        # If the agent stopped for something that turned out not to be an
        # interruption, pick the sentence back up instead of leaving a hole in
        # the conversation.
        resume_false_interruption=settings.voice_false_interruption_timeout is not None,
        false_interruption_timeout=settings.voice_false_interruption_timeout,
        # Overlapping speech in the first/last second of an agent turn is where
        # backchannels cluster ("mhm" as the agent starts, "okay" as it lands).
        # Inside these windows, anything the classifier calls a backchannel is
        # suppressed; anything it calls a real interruption still gets through.
        backchannel_boundary=(1.0, 1.0),
    )

    preemptive = PreemptiveGenerationOptions(
        # Start the LLM on the partial transcript while the user is still
        # finishing, and throw the result away if the turn changes shape. This
        # is what removes the LLM's time-to-first-token from perceived latency
        # in the common case where the guess was right.
        enabled=settings.voice_preemptive_generation,
        # Also pre-run TTS, removing its time-to-first-byte too. Costs
        # discarded synthesis on wrong guesses -- the right trade while the
        # goal is proving the conversation feels fast.
        preemptive_tts=settings.voice_preemptive_tts,
        # Long utterances are more likely to change direction before they end,
        # and a listener does not expect an instant answer to a long question.
        max_speech_duration=10.0,
        max_retries=3,
    )

    return TurnHandlingOptions(
        turn_detection=build_turn_detector(),
        endpointing=endpointing,
        interruption=interruption,
        preemptive_generation=preemptive,
    )
