from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from infrastructure.openai import OpenAIVoiceTranscriber, VoiceTranscriptionError


class FakeProcess:
    def __init__(
        self, *, returncode: int = 0, stdout: bytes = b"wav", stderr: bytes = b""
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.communicate = AsyncMock(return_value=(stdout, stderr))
        self.kill = lambda: None
        self.wait = AsyncMock()


@pytest.mark.asyncio
async def test_transcriber_converts_audio_and_calls_openai() -> None:
    create = AsyncMock(return_value=SimpleNamespace(text="  кава 5 євро  "))
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )
    process = FakeProcess(stdout=b"wav-bytes")
    transcriber = OpenAIVoiceTranscriber(client, model="gpt-transcribe")

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
        result = await transcriber.transcribe(b"ogg-bytes")

    process.communicate.assert_awaited_once_with(b"ogg-bytes")
    assert create.await_args.kwargs["model"] == "gpt-transcribe"
    assert create.await_args.kwargs["file"] == (
        "voice.wav",
        b"wav-bytes",
        "audio/wav",
    )
    assert result == "кава 5 євро"


@pytest.mark.asyncio
async def test_transcriber_rejects_invalid_audio_before_openai() -> None:
    create = AsyncMock()
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )
    process = FakeProcess(returncode=1, stdout=b"", stderr=b"invalid audio")
    transcriber = OpenAIVoiceTranscriber(client, model="gpt-transcribe")

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
        pytest.raises(VoiceTranscriptionError, match="audio conversion failed"),
    ):
        await transcriber.transcribe(b"bad")

    create.assert_not_awaited()
