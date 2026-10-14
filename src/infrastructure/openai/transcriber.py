from __future__ import annotations

import asyncio
from typing import Protocol

from openai import AsyncOpenAI


class VoiceTranscriptionError(RuntimeError):
    pass


class VoiceTranscriber(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


class OpenAIVoiceTranscriber:
    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        conversion_timeout_seconds: float = 20.0,
    ) -> None:
        self.client = client
        self.model = model
        self.conversion_timeout_seconds = conversion_timeout_seconds

    async def transcribe(self, audio: bytes) -> str:
        wav_audio = await self._convert_to_wav(audio)
        try:
            result = await self.client.audio.transcriptions.create(
                model=self.model,
                file=("voice.wav", wav_audio, "audio/wav"),
                prompt=(
                    "Короткий запис про сімейні фінанси українською мовою. "
                    "Можливі суми, валюти, дати, доходи, витрати та заплановані оплати."
                ),
            )
        except Exception as error:
            raise VoiceTranscriptionError("transcription failed") from error
        return result.text.strip()

    async def _convert_to_wav(self, audio: bytes) -> bytes:
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "wav",
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise VoiceTranscriptionError("ffmpeg is unavailable") from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(audio), timeout=self.conversion_timeout_seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise VoiceTranscriptionError("audio conversion timed out") from error
        if process.returncode != 0 or not stdout:
            detail = stderr.decode(errors="replace")[:200]
            raise VoiceTranscriptionError(f"audio conversion failed: {detail}")
        return stdout


__all__ = ["OpenAIVoiceTranscriber", "VoiceTranscriber", "VoiceTranscriptionError"]
