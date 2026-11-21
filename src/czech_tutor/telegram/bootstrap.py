import httpx
from aiogram import Dispatcher
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from czech_tutor.config import Settings
from czech_tutor.persistence import voice_jobs as jobs
from czech_tutor.persistence.models import VoiceJob
from czech_tutor.persistence.users import get_user, upsert_user
from czech_tutor.telegram.handlers import create_router
from czech_tutor.telegram.reply_sender import NoticeActions, SpeechActions
from czech_tutor.telegram.voice_cards import reconcile_voice_card
from czech_tutor.telegram.voice_handlers import VoiceActions
from czech_tutor.tutor.delivery_service import confirm_reply_delivery
from czech_tutor.tutor.errors import StorageError
from czech_tutor.tutor.feedback_notice import claim_notice, complete_notice
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

    async def advance_voice(user_id: int, state: VoiceInput) -> VoiceReply | None:
        return await confirm_voice_input(
            session_factory,
            client,
            settings.open_router_api_key,
            user_id,
            state.job_id,
            state.revision,
            automatic=True,
        )

    async def remember_card(
        user_id: int, job_id: int, message_id: int, token: str | None = None
    ) -> bool:
        try:
            async with session_factory.begin() as session:
                return await jobs.remember_voice_card(
                    session, user_id, job_id, message_id, token=token
                )
        except SQLAlchemyError:
            raise StorageError("Failed to record voice card.") from None

    async def claim_card(
        user_id: int, job_id: int, expected: int | None, allow_recovery: bool = False
    ) -> str | None:
        try:
            async with session_factory.begin() as session:
                return await jobs.claim_voice_card(
                    session, user_id, job_id, expected, allow_recovery=allow_recovery
                )
        except SQLAlchemyError:
            raise StorageError("Failed to reserve voice card.") from None

    async def release_card(user_id: int, job_id: int, token: str) -> bool:
        try:
            async with session_factory.begin() as session:
                return await jobs.release_voice_card(session, user_id, job_id, token)
        except SQLAlchemyError:
            raise StorageError("Failed to release voice card.") from None

    async def reconcile_card(bot, user_id: int, job_id: int) -> bool:
        return await reconcile_voice_card(
            bot, session_factory, user_id, job_id, settings.mini_app_url
        )

    async def obsolete_cards(user_id: int) -> list[int]:
        async with session_factory.begin() as session:
            result = await session.execute(
                select(VoiceJob.card_message_id)
                .where(
                    VoiceJob.telegram_user_id == user_id,
                    VoiceJob.status.in_(("confirmed", "cancelled", "failed")),
                    VoiceJob.card_message_id.is_not(None),
                )
                .order_by(VoiceJob.id.desc())
                .limit(5)
            )
            return list(result.scalars())

    async def load_language(user_id: int) -> str:
        try:
            async with session_factory.begin() as session:
                learner = await get_user(session, user_id)
                if learner is None:
                    learner = await upsert_user(session, user_id, None)
                return learner.interface_language
        except SQLAlchemyError:
            return "ru"

    async def prepare_notice(user_id: int, reply_id: int) -> bool:
        return await claim_notice(session_factory, user_id, reply_id)

    async def finish_notice(user_id: int, reply_id: int, message_id: int) -> None:
        await complete_notice(session_factory, user_id, reply_id, message_id)

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

    async def confirm_with_cleanup(user_id, job_id, revision, recover, on_accepted):
        return await confirm_voice_input(
            session_factory,
            client,
            settings.open_router_api_key,
            user_id,
            job_id,
            revision,
            recover,
            on_accepted=on_accepted,
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
            advance=advance_voice,
            claim_card=claim_card,
            release_card=release_card,
            reconcile_card=reconcile_card,
            remember_card=remember_card,
            obsolete_cards=obsolete_cards,
            confirm_with_cleanup=confirm_with_cleanup,
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
            notice_actions=NoticeActions(prepare_notice, finish_notice),
            load_language=load_language,
        )
    )
    return dispatcher
