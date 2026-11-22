"""Bounded, single-attempt HeyGen synthesis and in-memory MP3 encoding."""

import asyncio
import re

import httpx
import lameenc

from czech_tutor.tutor.errors import TTSError

# Temporary free endpoint; never fall back to the paid model when it retires.
SPEECH_MODEL = "heygen/heygen-voice-1-turbo:free"
SPEECH_VOICES = (
    "nadine",
    "tabitha",
    "bram",
    "gareth",
    "noelle",
    "meredith",
    "sabrina",
    "lucian",
    "weston",
    "vincent",
)
MAX_SPEECH_CHARS = 4000
MAX_PCM_BYTES = 44100 * 2 * 180
MAX_MP3_BYTES = 2 * 1024 * 1024
PCM_CHUNK_BYTES = 64 * 1024


def validate_spoken_text(value: str) -> None:
    # Brackets are provider control tags, including non-spoken arbitrary text.
    # Preserve the exact transcript instead of silently deleting its contents.
    if (
        not value.strip()
        or len(value) > MAX_SPEECH_CHARS
        or re.search(r"[\[\]`]|https?://|www\.", value, re.IGNORECASE)
    ):
        raise TTSError("Reply is unsuitable for speech.")


def _create_mp3_encoder() -> lameenc.Encoder:
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(64)
    encoder.set_in_sample_rate(44100)
    encoder.set_channels(1)
    encoder.set_quality(2)
    encoder.silence()
    return encoder


def encode_mp3(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2 or len(pcm) > MAX_PCM_BYTES:
        raise TTSError("Invalid PCM audio.")
    encoder = _create_mp3_encoder()
    audio = bytes(encoder.encode(pcm) + encoder.flush())
    if not audio or len(audio) > MAX_MP3_BYTES:
        raise TTSError("Invalid encoded speech size.")
    return audio


async def _encode_mp3_chunk(encoder: lameenc.Encoder, pcm: bytes | None) -> bytes:
    """Finish the one bounded worker before propagating cancellation."""
    worker = asyncio.create_task(
        asyncio.to_thread(encoder.flush) if pcm is None else asyncio.to_thread(encoder.encode, pcm)
    )
    try:
        return bytes(await asyncio.shield(worker))
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


async def synthesize_speech(
    client: httpx.AsyncClient,
    api_key: str,
    text: str,
    voice: str,
) -> bytes:
    """Never retry an ambiguous paid POST; callers persist its reservation first."""
    validate_spoken_text(text)
    if voice not in SPEECH_VOICES or not api_key:
        raise TTSError("Invalid speech configuration.")
    try:
        async with asyncio.timeout(45):
            async with client.stream(
                "POST",
                "https://openrouter.ai/api/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": SPEECH_MODEL,
                    "input": text,
                    "voice": voice,
                    "response_format": "pcm",
                    "provider": {
                        "only": ["heygen"],
                        "allow_fallbacks": False,
                        "options": {"heygen": {"language": "cs"}},
                    },
                },
                timeout=httpx.Timeout(40, connect=5),
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise TTSError("Speech provider rejected the request.")
                if response.headers.get("content-type", "").split(";")[0] != "audio/pcm":
                    raise TTSError("Unexpected speech content type.")
                encoder = _create_mp3_encoder()
                pcm_size = 0
                audio = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=PCM_CHUNK_BYTES):
                    pcm_size += len(chunk)
                    if pcm_size > MAX_PCM_BYTES:
                        raise TTSError("Speech response exceeds the audio limit.")
                    if len(chunk) % 2:
                        raise TTSError("Invalid PCM audio.")
                    encoded = await _encode_mp3_chunk(encoder, chunk)
                    if len(audio) + len(encoded) > MAX_MP3_BYTES:
                        raise TTSError("Invalid encoded speech size.")
                    audio.extend(encoded)
                if not pcm_size:
                    raise TTSError("Invalid PCM audio.")
                encoded = await _encode_mp3_chunk(encoder, None)
                if not audio and not encoded or len(audio) + len(encoded) > MAX_MP3_BYTES:
                    raise TTSError("Invalid encoded speech size.")
                audio.extend(encoded)
                return bytes(audio)
    except (httpx.HTTPError, TimeoutError, RuntimeError, ValueError) as error:
        if isinstance(error, TTSError):
            raise
        raise TTSError("Speech synthesis failed.") from None
