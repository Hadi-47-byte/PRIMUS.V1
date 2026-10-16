"""Tests for POST /test/wav-check — lightweight WAV header validation endpoint.

This dev-only endpoint accepts a multipart WAV upload and reports file size
plus header validity without calling STT / Gemini.

All tests use ``TestClient`` with the standard ``create_app`` factory —
no real hardware or external API calls required.
"""

from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient

from primus.api.app import create_app
from primus.provider import LLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockLLMProvider(LLMProvider):
    def complete(self, messages):
        return json.dumps({
            "response_text": "ok",
            "language": "en",
            "interaction_state": "idle",
        })


@pytest.fixture
def client():
    app = create_app(provider=MockLLMProvider())
    return TestClient(app)


def _make_wav(
    sample_rate: int = 16000,
    channels: int = 1,
    bits_per_sample: int = 16,
    data: bytes = b"\x00\x01" * 8000,
    audio_format: int = 1,  # PCM
) -> bytes:
    """Build a minimal valid WAV file (44-byte header + data)."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(data)
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s"   # RIFF id, file size - 8, WAVE
        "4sI"      # fmt sub-chunk id, sub-chunk size (16 for PCM)
        "HHIIHH"   # audio_fmt, channels, sample_rate, byte_rate,
                   # block_align, bits_per_sample
        "4sI",     # data sub-chunk id, data size
        b"RIFF", chunk_size, b"WAVE",
        b"fmt ", 16,
        audio_format, channels, sample_rate, byte_rate,
        block_align, bits_per_sample,
        b"data", data_size,
    )
    return header + data


# ===========================================================================
# POST /test/wav-check tests
# ===========================================================================

class TestWavCheckEndpoint:
    # -- valid WAV ----------------------------------------------------------

    def test_valid_wav_mono_16k(self, client):
        """Standard robot recording: mono, 16 kHz, 16-bit PCM."""
        wav = _make_wav()
        r = client.post(
            "/test/wav-check",
            files={"audio": ("recording.wav", wav, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "valid_wav"
        assert data["size_bytes"] == len(wav)
        assert data["wav"]["is_pcm"] is True
        assert data["wav"]["channels"] == 1
        assert data["wav"]["sample_rate"] == 16000
        assert data["wav"]["bits_per_sample"] == 16
        assert data["wav"]["audio_format"] == 1
        assert data["matches_robot_rate"] is True
        assert data["data"]["present"] is True
        # default data: 16 000 B at 16 kHz mono 16-bit = 0.5 s
        assert data["data"]["duration_seconds"] == 0.5

    def test_valid_wav_stereo_44k(self, client):
        """Stereo 44.1 kHz is valid but does not match robot rate."""
        wav = _make_wav(sample_rate=44100, channels=2)
        r = client.post(
            "/test/wav-check",
            files={"audio": ("stereo.wav", wav, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "valid_wav"
        assert data["wav"]["channels"] == 2
        assert data["wav"]["sample_rate"] == 44100
        assert data["matches_robot_rate"] is False

    def test_valid_wav_3_second_recording(self, client):
        """A 3-second recording at 16 kHz mono 16-bit = 96 000 data bytes."""
        pcm_data = b"\x00\x01" * 48000  # 96 000 bytes = 3 s
        wav = _make_wav(data=pcm_data)
        r = client.post(
            "/test/wav-check",
            files={"audio": ("rec3s.wav", wav, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "valid_wav"
        assert data["data"]["size_bytes"] == 96000
        assert data["data"]["duration_seconds"] == 3.0

    # -- too small ----------------------------------------------------------

    def test_empty_file_400(self, client):
        """Empty upload → 400 too_small."""
        r = client.post(
            "/test/wav-check",
            files={"audio": ("empty.wav", b"", "audio/wav")},
        )
        assert r.status_code == 400
        assert r.json()["status"] == "too_small"

    def test_header_truncated_400(self, client):
        """File smaller than 44 bytes → 400 too_small."""
        r = client.post(
            "/test/wav-check",
            files={"audio": ("tiny.wav", b"\x00" * 30, "audio/wav")},
        )
        assert r.status_code == 400
        assert r.json()["status"] == "too_small"

    # -- not a WAV ----------------------------------------------------------

    def test_non_wav_content(self, client):
        """Random bytes (≥44) with no RIFF/WAVE magic → not_wav."""
        r = client.post(
            "/test/wav-check",
            files={"audio": ("noise.bin", b"\x42" * 100, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "not_wav"

    def test_riff_without_wave(self, client):
        """RIFF header but WAVE magic missing → not_wav."""
        content = b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 100
        r = client.post(
            "/test/wav-check",
            files={"audio": ("video.avi", content, "audio/wav")},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "not_wav"

    def test_wave_without_fmt_chunk(self, client):
        """Valid RIFF/WAVE but fmt sub-chunk ID corrupted → not_wav."""
        wav = _make_wav()
        # Corrupt the 'fmt ' identifier at bytes 12-15
        wav = wav[:12] + b"XXXX" + wav[16:]
        r = client.post(
            "/test/wav-check",
            files={"audio": ("bad_fmt.wav", wav, "audio/wav")},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "not_wav"

    # -- missing upload field -----------------------------------------------

    def test_no_file_field_422(self, client):
        """POST without multipart file → FastAPI 422."""
        r = client.post("/test/wav-check")
        assert r.status_code == 422

    # -- too large ----------------------------------------------------------

    def test_oversized_file_400(self, client):
        """File exceeding 2 MB limit → 400 too_large."""
        big = b"\x00" * (2 * 1024 * 1024 + 1)
        r = client.post(
            "/test/wav-check",
            files={"audio": ("huge.wav", big, "audio/wav")},
        )
        assert r.status_code == 400
        data = r.json()
        assert data["status"] == "too_large"
        assert data["size_bytes"] > 2 * 1024 * 1024

    # -- non-PCM audio format -----------------------------------------------

    def test_non_pcm_audio_format(self, client):
        """WAV with audio_format != 1 (e.g. IEEE float) is valid but is_pcm=False."""
        wav = _make_wav(audio_format=3)  # IEEE float
        r = client.post(
            "/test/wav-check",
            files={"audio": ("float.wav", wav, "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "valid_wav"
        assert data["wav"]["is_pcm"] is False
        assert data["wav"]["audio_format"] == 3
