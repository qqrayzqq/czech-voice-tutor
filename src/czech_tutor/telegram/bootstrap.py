import httpx
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.config import Settings
from czech_tutor.telegram.handlers import create_router
from czech_tutor.telegram.reply_sender import SpeechActions
from czech_tutor.telegram.voice_handlers import VoiceActions
from czech_tutor.tutor.delivery_service import confirm_reply_delivery
from czech_tutor.tutor.schemas import ReplyToSend
from czech_tutor.tutor.speech_service import complete_speech, prepare_speech, release_speech
from czech_tutor.tutor.text_service import process_text_message
from czech_tutor.tutor.voice_schemas import VoiceInput, VoiceReply
from czech_tutor.tutor.voice_service import (
    cancel_voice_input,
    confirm_voice_input,
    load_voice_input,
    process_voice_message,
    release_voice_reply,
    request_voice_edit,
    submit_voice_edit,
)


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
) -> Dispatcher:
    async def reply_to_text(
        telegram_user_id: int,
        username: str | None,
        telegram_chat_id: int,
        telegram_message_id: int,
        text: str,
    ) -> ReplyToSend | None:
        return await process_text_message(
            session_factory=session_factory,
            client=client,
            api_key=settings.open_router_api_key,
            telegram_user_id=telegram_user_id,
            username=username,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            text=text,
        )

    async def confirm_reply_sent(
        reply_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> None:
        await confirm_reply_delivery(
            session_factory=session_factory,
            reply_id=reply_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
        )

    async def process_voice(
        telegram_user_id: int,
        username: str | None,
        telegram_chat_id: int,
        telegram_message_id: int,
        telegram_file_id: str,
        audio_bytes: bytes,
    ) -> VoiceInput | None:
        return await process_voice_message(
            session_factory=session_factory,
            client=client,
            soniox_api_key=settings.soniox_api_key or "",
            telegram_user_id=telegram_user_id,
            username=username,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            telegram_file_id=telegram_file_id,
            audio_bytes=audio_bytes,
        )

    async def confirm_voice(
        telegram_user_id: int,
        job_id: int,
        revision: int,
        recover: bool,
    ) -> VoiceReply | None:
        return await confirm_voice_input(
            session_factory=session_factory,
            client=client,
            openrouter_api_key=settings.open_router_api_key,
            telegram_user_id=telegram_user_id,
            job_id=job_id,
            revision=revision,
            recover=recover,
        )

    async def cancel_voice(
        telegram_user_id: int,
        job_id: int,
        revision: int,
    ) -> bool:
        return await cancel_voice_input(
            session_factory=session_factory,
            client=client,
            soniox_api_key=settings.soniox_api_key or "",
            telegram_user_id=telegram_user_id,
            job_id=job_id,
            revision=revision,
        )

    async def load_voice(telegram_user_id: int, job_id: int | None) -> VoiceInput | None:
        return await load_voice_input(
            session_factory=session_factory,
            client=client,
            soniox_api_key=settings.soniox_api_key or "",
            telegram_user_id=telegram_user_id,
            job_id=job_id,
        )

    async def edit_voice(telegram_user_id: int, job_id: int, revision: int) -> VoiceInput | None:
        return await request_voice_edit(
            session_factory=session_factory,
            telegram_user_id=telegram_user_id,
            job_id=job_id,
            revision=revision,
        )

    async def submit_edit(
        telegram_user_id: int, telegram_chat_id: int, telegram_message_id: int, text: str
    ) -> VoiceInput | None:
        return await submit_voice_edit(
            session_factory=session_factory,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            text=text,
        )

    async def release_reply(telegram_user_id: int, job_id: int, delivery_token: str) -> None:
        await release_voice_reply(
            session_factory=session_factory,
            telegram_user_id=telegram_user_id,
            job_id=job_id,
            delivery_token=delivery_token,
        )

    voice_actions = None
    if settings.soniox_api_key:
        voice_actions = VoiceActions(
            process=process_voice,
            load=load_voice,
            request_edit=edit_voice,
            submit_edit=submit_edit,
            confirm=confirm_voice,
            cancel=cancel_voice,
            release_reply=release_reply,
        )

    async def prepare_audio(user_id: int, reply_id: int):
        return await prepare_speech(
            session_factory,
            client,
            settings.open_router_api_key,
            user_id,
            reply_id,
            settings.tts_voice,
        )

    async def complete_audio(
        user_id: int,
        reply_id: int,
        token: str,
        file_id: str,
        chat_id: int,
        message_id: int,
    ) -> None:
        await complete_speech(
            session_factory,
            user_id,
            reply_id,
            token,
            file_id,
            chat_id,
            message_id,
        )

    async def release_audio(user_id: int, reply_id: int, token: str) -> None:
        await release_speech(session_factory, user_id, reply_id, token)

    speech_actions = (
        SpeechActions(prepare_audio, complete_audio, release_audio)
        if settings.tts_enabled
        else None
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            settings.allowed_user_ids,
            reply_to_text,
            confirm_reply_sent,
            settings.mini_app_url,
            voice_actions=voice_actions,
            speech_actions=speech_actions,
        )
    )
    return dispatcher
