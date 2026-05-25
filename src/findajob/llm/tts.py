"""Gemini TTS wrapper for two-speaker podcast generation (#870).

Direct Google AI API client — NOT through OpenRouter (Gemini TTS is
unavailable there as of 2026-05-24). Auth via ``GEMINI_API_KEY`` env var.

Audio quality drifts past ~2 min of continuous output, so longer scripts
are split at ``[SEGMENT]`` markers emitted by the scriptwriter prompt and
rendered as separate API calls. The PCM buffers are concatenated before a
single ffmpeg PCM→MP3 encode.

Spend-ceiling per-call gate mirrors the ``openrouter.complete()`` pattern:
raises ``LLMSpendCeilingExceeded`` before any HTTP work.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from findajob.audit import log_event

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1
DEFAULT_TIMEOUT_S = 180

PodcastFormat = Literal["deep_dive", "deep_dive_long", "brief", "qa_drill", "critical_analysis"]

PODCAST_FORMATS: dict[PodcastFormat, str] = {
    "deep_dive": "Deep Dive",
    "deep_dive_long": "Deep Dive (Extended)",
    "brief": "The Brief",
    "qa_drill": "Q&A Drill",
    "critical_analysis": "Critical Analysis",
}

DEFAULT_VOICES = ("Kore", "Puck")

# Gemini 3.1 Flash TTS pricing (per 1M tokens).
# Charges for both input text tokens and output audio tokens.
_INPUT_PER_MTOK = 1.00  # $1.00 / 1M input text tokens
_OUTPUT_PER_MTOK = 20.00  # $20.00 / 1M output audio tokens


@dataclass(frozen=True)
class TTSResult:
    pcm_bytes: bytes
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    segments: int


class GeminiTTSError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _check_call_gate() -> None:
    from findajob.spend_ceiling import check_call_gate  # noqa: PLC0415

    check_call_gate()


def _get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiTTSError("GEMINI_API_KEY not set", status_code=None)
    return key


def _render_segment(
    text: str,
    *,
    speaker_a: str,
    speaker_b: str,
    voice_a: str,
    voice_b: str,
    model: str,
    api_key: str,
    timeout_s: int,
) -> tuple[bytes, int, int]:
    """Render one script segment via Gemini TTS. Returns (pcm_bytes, input_tokens, output_tokens)."""
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "multiSpeakerVoiceConfig": {
                    "speakerVoiceConfigs": [
                        {
                            "speaker": speaker_a,
                            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_a}},
                        },
                        {
                            "speaker": speaker_b,
                            "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_b}},
                        },
                    ]
                }
            },
        },
    }

    url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={api_key}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()[:500]
        except Exception:
            pass
        raise GeminiTTSError(
            f"Gemini TTS HTTP {e.code}: {err_body}",
            status_code=e.code,
        ) from e
    except urllib.error.URLError as e:
        raise GeminiTTSError(f"Gemini TTS network error: {e}") from e

    candidates = resp_data.get("candidates", [])
    if not candidates:
        raise GeminiTTSError("Gemini TTS returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    audio_data = None
    for part in parts:
        inline = part.get("inlineData", {})
        if inline.get("mimeType", "").startswith("audio/"):
            audio_data = base64.b64decode(inline["data"])
            break

    if not audio_data:
        raise GeminiTTSError("Gemini TTS response contained no audio data")

    usage = resp_data.get("usageMetadata", {})
    input_tokens = usage.get("promptTokenCount", 0)
    output_tokens = usage.get("candidatesTokenCount", 0) or usage.get("totalTokenCount", 0) - input_tokens

    return audio_data, input_tokens, max(0, output_tokens)


def parse_script_segments(script: str) -> list[str]:
    """Split a scriptwriter output into segments at [SEGMENT] markers.

    If no markers are present, the entire script is one segment.
    Empty segments (after stripping) are dropped.
    """
    parts = re.split(r"\[SEGMENT\]", script, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def generate_audio(
    script: str,
    *,
    speaker_a: str = "Speaker A",
    speaker_b: str = "Speaker B",
    voice_a: str | None = None,
    voice_b: str | None = None,
    model: str = DEFAULT_MODEL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> TTSResult:
    """Render a two-speaker script to PCM audio via Gemini TTS.

    Splits on [SEGMENT] markers and concatenates the PCM buffers.
    Raises LLMSpendCeilingExceeded or GeminiTTSError on failure.
    """
    _check_call_gate()

    api_key = _get_api_key()
    va = voice_a or DEFAULT_VOICES[0]
    vb = voice_b or DEFAULT_VOICES[1]

    segments = parse_script_segments(script)
    if not segments:
        raise GeminiTTSError("Script is empty after parsing")

    pcm_buffers: list[bytes] = []
    total_input_tokens = 0
    total_output_tokens = 0

    for seg in segments:
        pcm, in_tok, out_tok = _render_segment(
            seg,
            speaker_a=speaker_a,
            speaker_b=speaker_b,
            voice_a=va,
            voice_b=vb,
            model=model,
            api_key=api_key,
            timeout_s=timeout_s,
        )
        pcm_buffers.append(pcm)
        total_input_tokens += in_tok
        total_output_tokens += out_tok

    all_pcm = b"".join(pcm_buffers)
    cost = total_input_tokens * _INPUT_PER_MTOK / 1_000_000 + total_output_tokens * _OUTPUT_PER_MTOK / 1_000_000

    return TTSResult(
        pcm_bytes=all_pcm,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=round(cost, 6),
        model=model,
        segments=len(segments),
    )


def pcm_to_mp3(pcm_bytes: bytes, output_path: str) -> None:
    """Convert raw PCM audio to MP3 via ffmpeg."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            str(CHANNELS),
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            output_path,
        ],
        input=pcm_bytes,
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace")[:500]
        raise GeminiTTSError(f"ffmpeg failed (exit {proc.returncode}): {stderr}")


def generate_podcast(
    script: str,
    output_path: str,
    *,
    speaker_a: str = "Speaker A",
    speaker_b: str = "Speaker B",
    voice_a: str | None = None,
    voice_b: str | None = None,
    model: str = DEFAULT_MODEL,
    conn=None,
    job_id: str | None = None,
    operation: str = "podcast_tts",
) -> str:
    """End-to-end: render script → PCM → MP3. Returns the output path.

    Cost is logged to cost_log when conn is provided. Spend ceiling is
    enforced before the first API call (two-point pattern: per-call gate
    here + launch gate at the route layer).
    """
    start = time.time()
    result = generate_audio(
        script,
        speaker_a=speaker_a,
        speaker_b=speaker_b,
        voice_a=voice_a,
        voice_b=voice_b,
        model=model,
    )
    latency_ms = int((time.time() - start) * 1000)

    pcm_to_mp3(result.pcm_bytes, output_path)

    log_event(
        "podcast_tts_complete",
        job_id=job_id,
        model=result.model,
        segments=result.segments,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        latency_ms=latency_ms,
        output=os.path.basename(output_path),
    )

    if conn is not None:
        try:
            from findajob.cost_tracking import log_call  # noqa: PLC0415

            log_call(
                conn,
                job_id=job_id,
                operation=operation,
                model=result.model,
                input_text=script,
                output_text=None,
                latency_ms=latency_ms,
                success=True,
                cost_usd_override=result.cost_usd,
                input_tokens_override=result.input_tokens,
                output_tokens_override=result.output_tokens,
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            log_event(
                "cost_log_failed",
                operation=operation,
                error=f"{type(e).__name__}: {e}",
            )

    return output_path
