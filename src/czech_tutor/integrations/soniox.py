import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from czech_tutor.tutor.errors import ASRError

logger = logging.getLogger(__name__)

SONIOX_API_URL = "https://api.soniox.com/v1"
SONIOX_MODEL = "stt-async-v6"
# B-CS without vocabulary terms, selected in the 2026-11-21 holdout.
# This is a recognition hint, not a guarantee of verbatim learner errors.
SONIOX_CONTEXT_TEXT = (
    "Nahrávka cizince mluvícího česky s občasnými ruskými vsuvkami. "
    "Česká slova zapisujte výhradně latinkou, ruské vsuvky azbukou. "
    "Nikdy nepřepisujte česká slova azbukou. "
    "Neopravujte gramatické chyby studenta, zachovejte přesně vyřčené infinitivy a koncovky."
)

NonEmptyString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class _IdResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: NonEmptyString


class _StatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    status: Literal["queued", "processing", "completed", "error"]


class _TranscriptToken(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    text: str = ""
    language: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


@dataclass(frozen=True)
class Transcript:
    text: str
    metadata: dict[str, object]


class _TranscriptResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    text: Annotated[str, StringConstraints(strict=True, strip_whitespace=True)]
    tokens: list[_TranscriptToken] = Field(default_factory=list)


class TranscriptionPending(ASRError):
    """The remote job is still active after the local polling budget."""


class TranscriptionFailed(ASRError):
    """Soniox reported a terminal transcription error."""


class EmptyTranscript(ASRError):
    """The completed job returned valid text without recognized speech."""


async def upload_audio_file(
    client: httpx.AsyncClient,
    api_key: str,
    audio_bytes: bytes,
    filename: str = "voice.ogg",
) -> str:
    """Upload audio bytes to Soniox Files API and return the file_id."""
    if not audio_bytes:
        raise ASRError("Cannot upload empty audio data.")
    try:
        response = await client.post(
            f"{SONIOX_API_URL}/files",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (filename, audio_bytes, "audio/ogg")},
            timeout=30.0,
        )
        response.raise_for_status()
        return _IdResponse.model_validate(response.json()).id
    except (httpx.HTTPError, ValueError):
        raise ASRError("Failed to upload audio to Soniox.") from None


async def create_transcription_job(
    client: httpx.AsyncClient,
    api_key: str,
    file_id: str,
    model: str = SONIOX_MODEL,
    language_hints: list[str] | None = None,
) -> str:
    """Submit an asynchronous transcription job for an uploaded file."""
    try:
        response = await client.post(
            f"{SONIOX_API_URL}/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "file_id": file_id,
                "language_hints": language_hints or ["cs"],
                "language_hints_strict": False,
                "enable_language_identification": True,
                "context": {"text": SONIOX_CONTEXT_TEXT},
            },
            timeout=15.0,
        )
        response.raise_for_status()
        return _IdResponse.model_validate(response.json()).id
    except (httpx.HTTPError, ValueError):
        raise ASRError("Failed to create transcription job in Soniox.") from None


async def _read_status(
    client: httpx.AsyncClient,
    api_key: str,
    transcription_id: str,
    *,
    missing_ok: bool = False,
) -> str | None:
    try:
        response = await client.get(
            f"{SONIOX_API_URL}/transcriptions/{transcription_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        if missing_ok and response.status_code == 404:
            return None
        response.raise_for_status()
        return _StatusResponse.model_validate(response.json()).status
    except (httpx.HTTPError, ValueError):
        raise ASRError("Failed to poll transcription status from Soniox.") from None


async def poll_transcription_status(
    client: httpx.AsyncClient,
    api_key: str,
    transcription_id: str,
) -> str:
    """Check and validate the documented remote transcription status."""
    status = await _read_status(client, api_key, transcription_id)
    assert status is not None
    return status


async def fetch_transcript_text(
    client: httpx.AsyncClient,
    api_key: str,
    transcription_id: str,
    *,
    detailed: bool = False,
) -> str | Transcript:
    """Fetch a nonempty final transcript once the job has completed."""
    try:
        response = await client.get(
            f"{SONIOX_API_URL}/transcriptions/{transcription_id}/transcript",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        response.raise_for_status()
        transcript = _TranscriptResponse.model_validate(response.json())
        text = transcript.text
        if not text:
            raise EmptyTranscript("Soniox returned an empty transcript.")
        if detailed:
            languages = Counter(
                t.language for t in transcript.tokens if t.language and t.text.strip()
            )
            scores = [
                t.confidence
                for t in transcript.tokens
                if t.confidence is not None and t.text.strip()
            ]
            metadata: dict[str, object] = {"languages": dict(languages)}
            if scores:
                metadata.update(
                    mean_confidence=sum(scores) / len(scores), min_confidence=min(scores)
                )
            return Transcript(text, metadata)
        return text
    except (httpx.HTTPError, ValueError):
        raise ASRError("Failed to fetch transcript from Soniox.") from None


async def wait_for_transcript(
    client: httpx.AsyncClient,
    api_key: str,
    transcription_id: str,
    *,
    max_poll_attempts: int = 20,
    poll_interval_sec: float = 0.5,
    detailed: bool = False,
) -> str | Transcript:
    """Poll within a local budget without cancelling or deleting remote work."""
    for attempt in range(max_poll_attempts):
        status = await poll_transcription_status(client, api_key, transcription_id)
        if status == "completed":
            return await fetch_transcript_text(client, api_key, transcription_id, detailed=detailed)
        if status == "error":
            raise TranscriptionFailed("Soniox transcription failed on the server.")
        if attempt + 1 < max_poll_attempts:
            await asyncio.sleep(poll_interval_sec)
    raise TranscriptionPending("Soniox transcription timed out waiting for completion.")


async def _delete_object(
    client: httpx.AsyncClient, api_key: str, collection: str, object_id: str
) -> bool:
    try:
        response = await client.delete(
            f"{SONIOX_API_URL}/{collection}/{object_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        if response.status_code != 404:
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.warning("Failed to delete Soniox %s object %s.", collection, object_id)
        return False


async def delete_audio_file(
    client: httpx.AsyncClient,
    api_key: str,
    file_id: str,
) -> bool:
    """Delete an unused audio file; return whether cleanup is confirmed."""
    return await _delete_object(client, api_key, "files", file_id)


async def cleanup_transcription(
    client: httpx.AsyncClient,
    api_key: str,
    file_id: str | None,
    transcription_id: str | None,
) -> bool:
    """Delete terminal jobs before files; preserve active or uncertain work.

    A missing transcription_id asserts that no remote transcription exists.
    The caller must retain IDs when this returns False and retry cleanup later.
    """
    if transcription_id is not None:
        try:
            status = await _read_status(client, api_key, transcription_id, missing_ok=True)
        except ASRError:
            logger.warning("Failed to check Soniox cleanup status for %s.", transcription_id)
            return False
        if status in {"queued", "processing"}:
            return False
        if not await _delete_object(client, api_key, "transcriptions", transcription_id):
            return False
    if file_id is not None:
        return await delete_audio_file(client, api_key, file_id)
    return True


async def transcribe_voice(
    client: httpx.AsyncClient,
    api_key: str,
    audio_bytes: bytes,
    language_hints: list[str] | None = None,
    max_poll_attempts: int = 20,
    poll_interval_sec: float = 0.5,
) -> str:
    """Convenience for nonpersistent callers; preserve active jobs on timeout.

    Production callers should persist both remote IDs before waiting and use
    the lower-level functions so interrupted work and failed cleanup can resume.
    A failed create request can be ambiguous, so its uploaded file is retained.
    """
    file_id = await upload_audio_file(client, api_key, audio_bytes)
    job_id = await create_transcription_job(client, api_key, file_id, language_hints=language_hints)
    try:
        text = await wait_for_transcript(
            client,
            api_key,
            job_id,
            max_poll_attempts=max_poll_attempts,
            poll_interval_sec=poll_interval_sec,
        )
    except TranscriptionPending:
        raise
    except ASRError:
        await cleanup_transcription(client, api_key, file_id, job_id)
        raise
    await cleanup_transcription(client, api_key, file_id, job_id)
    return text
