"""Tests for findajob.llm.tts module.

Covers script parsing, segment-and-stitch, API response handling,
PCM-to-MP3 conversion, cost computation, and error paths.
All API calls are mocked — no real Gemini requests.
"""

import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from findajob.llm.tts import (
    _INPUT_PER_MTOK,
    _OUTPUT_PER_MTOK,
    DEFAULT_MODEL,
    DEFAULT_VOICES,
    PODCAST_FORMATS,
    GeminiTTSError,
    TTSResult,
    generate_audio,
    generate_podcast,
    parse_script_segments,
    pcm_to_mp3,
)


class TestParseScriptSegments:
    def test_single_segment(self):
        script = "[Speaker A] Hello, welcome.\n[Speaker B] Thanks for having me."
        segments = parse_script_segments(script)
        assert len(segments) == 1
        assert "[Speaker A]" in segments[0]

    def test_multiple_segments(self):
        script = (
            "[Speaker A] First part.\n[Speaker B] Yes.\n"
            "[SEGMENT]\n"
            "[Speaker A] Second part.\n[Speaker B] Indeed.\n"
            "[SEGMENT]\n"
            "[Speaker A] Third part."
        )
        segments = parse_script_segments(script)
        assert len(segments) == 3
        assert "First part" in segments[0]
        assert "Second part" in segments[1]
        assert "Third part" in segments[2]

    def test_case_insensitive_marker(self):
        script = "[Speaker A] Part one.\n[segment]\n[Speaker A] Part two."
        segments = parse_script_segments(script)
        assert len(segments) == 2

    def test_empty_segments_dropped(self):
        script = "[SEGMENT]\n[Speaker A] Only real content.\n[SEGMENT]\n\n[SEGMENT]"
        segments = parse_script_segments(script)
        assert len(segments) == 1

    def test_empty_script(self):
        assert parse_script_segments("") == []
        assert parse_script_segments("   ") == []


def _mock_gemini_response(*, input_tokens: int = 500, output_tokens: int = 1000) -> dict:
    """Build a fake Gemini TTS API response with PCM audio data."""
    fake_pcm = b"\x00\x01" * 100  # 200 bytes of fake PCM
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/L16;rate=24000",
                                "data": base64.b64encode(fake_pcm).decode(),
                            }
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
            "totalTokenCount": input_tokens + output_tokens,
        },
    }


class TestGenerateAudio:
    @patch("findajob.llm.tts._check_call_gate")
    @patch("findajob.llm.tts.urllib.request.urlopen")
    def test_single_segment(self, mock_urlopen, mock_gate):
        resp_data = _mock_gemini_response(input_tokens=500)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = generate_audio("[Speaker A] Hello.\n[Speaker B] Hi.")

        assert isinstance(result, TTSResult)
        assert result.segments == 1
        assert result.input_tokens == 500
        assert result.output_tokens == 1000
        expected_cost = round(500 * _INPUT_PER_MTOK / 1_000_000 + 1000 * _OUTPUT_PER_MTOK / 1_000_000, 6)
        assert result.cost_usd == expected_cost
        assert len(result.pcm_bytes) == 200

    @patch("findajob.llm.tts._check_call_gate")
    @patch("findajob.llm.tts.urllib.request.urlopen")
    def test_multi_segment_concatenation(self, mock_urlopen, mock_gate):
        resp_data = _mock_gemini_response(input_tokens=300)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        script = "[Speaker A] Part one.\n[SEGMENT]\n[Speaker A] Part two."

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            result = generate_audio(script)

        assert result.segments == 2
        assert result.input_tokens == 600  # 300 * 2
        assert result.output_tokens == 2000  # 1000 * 2
        assert len(result.pcm_bytes) == 400  # 200 * 2

    def test_missing_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            with pytest.raises(GeminiTTSError, match="GEMINI_API_KEY not set"):
                generate_audio("[Speaker A] Hello.")

    @patch("findajob.llm.tts._check_call_gate")
    @patch("findajob.llm.tts.urllib.request.urlopen")
    def test_no_audio_in_response(self, mock_urlopen, mock_gate):
        resp_data = {"candidates": [{"content": {"parts": [{"text": "oops"}]}}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with pytest.raises(GeminiTTSError, match="no audio data"):
                generate_audio("[Speaker A] Hello.")

    @patch("findajob.llm.tts._check_call_gate")
    @patch("findajob.llm.tts.urllib.request.urlopen")
    def test_empty_candidates(self, mock_urlopen, mock_gate):
        resp_data = {"candidates": []}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with pytest.raises(GeminiTTSError, match="no candidates"):
                generate_audio("[Speaker A] Hello.")

    @patch("findajob.llm.tts._check_call_gate")
    def test_empty_script_raises(self, mock_gate):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            with pytest.raises(GeminiTTSError, match="empty"):
                generate_audio("")

    @patch("findajob.llm.tts._check_call_gate")
    @patch("findajob.llm.tts.urllib.request.urlopen")
    def test_custom_voices(self, mock_urlopen, mock_gate):
        resp_data = _mock_gemini_response()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            generate_audio(
                "[Speaker A] Hello.",
                voice_a="Zephyr",
                voice_b="Charon",
            )

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        body = json.loads(req.data)
        voices = body["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]
        assert voices[0]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Zephyr"
        assert voices[1]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Charon"


class TestPcmToMp3:
    def test_ffmpeg_not_found(self, tmp_path):
        output = str(tmp_path / "test.mp3")
        fake_pcm = b"\x00" * 100
        with patch("findajob.llm.tts.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr=b"ffmpeg not found")
            with pytest.raises(GeminiTTSError, match="ffmpeg failed"):
                pcm_to_mp3(fake_pcm, output)


class TestGeneratePodcast:
    @patch("findajob.llm.tts.pcm_to_mp3")
    @patch("findajob.llm.tts.generate_audio")
    @patch("findajob.llm.tts.log_event")
    def test_end_to_end(self, mock_log, mock_audio, mock_mp3, tmp_path):
        mock_audio.return_value = TTSResult(
            pcm_bytes=b"\x00" * 100,
            input_tokens=500,
            output_tokens=1000,
            cost_usd=0.0205,
            model=DEFAULT_MODEL,
            segments=1,
        )

        output = str(tmp_path / "podcast.mp3")
        result = generate_podcast(
            "[Speaker A] Hello.\n[Speaker B] Hi.",
            output,
            job_id="test-123",
            operation="podcast_tts_deep_dive",
        )

        assert result == output
        mock_audio.assert_called_once()
        mock_mp3.assert_called_once_with(b"\x00" * 100, output)
        mock_log.assert_called_once()
        assert mock_log.call_args.kwargs["job_id"] == "test-123"

    @patch("findajob.llm.tts.pcm_to_mp3")
    @patch("findajob.llm.tts.generate_audio")
    @patch("findajob.llm.tts.log_event")
    def test_cost_logged_to_db(self, mock_log, mock_audio, mock_mp3, tmp_path):
        mock_audio.return_value = TTSResult(
            pcm_bytes=b"\x00" * 100,
            input_tokens=1000,
            output_tokens=2000,
            cost_usd=0.041,
            model=DEFAULT_MODEL,
            segments=2,
        )

        mock_conn = MagicMock()
        output = str(tmp_path / "podcast.mp3")

        with patch("findajob.cost_tracking.log_call") as mock_log_call:
            generate_podcast(
                "[Speaker A] Hello.",
                output,
                conn=mock_conn,
                job_id="test-456",
            )
            mock_log_call.assert_called_once()
            call_kwargs = mock_log_call.call_args.kwargs
            assert call_kwargs["cost_usd_override"] == 0.041
            assert call_kwargs["input_tokens_override"] == 1000
            assert call_kwargs["output_tokens_override"] == 2000
            mock_conn.commit.assert_called_once()


class TestPodcastFormats:
    def test_all_formats_defined(self):
        assert "deep_dive" in PODCAST_FORMATS
        assert "deep_dive_long" in PODCAST_FORMATS
        assert "brief" in PODCAST_FORMATS
        assert "qa_drill" in PODCAST_FORMATS
        assert "critical_analysis" in PODCAST_FORMATS

    def test_default_voices(self):
        assert len(DEFAULT_VOICES) == 2
        assert all(isinstance(v, str) for v in DEFAULT_VOICES)
