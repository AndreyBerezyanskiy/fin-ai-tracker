from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from application.services import AccessService, TelegramBootstrapService
from bot.handlers.commands import HELP_TEXT
from bot.keyboards import (
    AccessCallback,
    access_management_keyboard,
    access_request_keyboard,
    main_menu_keyboard,
)
from infrastructure.database import AccessGrant, AccessRequest, Household

logger = logging.getLogger(__name__)


def _chat_details(message: Message) -> tuple[str, str | None]:
    if message.chat.type == "private" and message.from_user is not None:
        return message.from_user.full_name, message.from_user.username
    return message.chat.title or message.chat.full_name, message.chat.username


def _start_parameter(message: Message) -> str | None:
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 else None


async def _notify_access_admins(
    bot: Bot, admin_user_ids: frozenset[int], request: AccessRequest
) -> None:
    username = getattr(request, "username", None)
    lines = [
        "👤 Новий запит на доступ",
        f"Назва: {request.title}",
        f"Тип: {request.chat_type}",
        f"Chat ID: {request.telegram_chat_id}",
    ]
    if username:
        lines.append(f"Username: @{username}")
    for admin_id in admin_user_ids:
        try:
            await bot.send_message(
                admin_id,
                "\n".join(lines),
                reply_markup=access_request_keyboard(request.id),
            )
        except TelegramAPIError:
            logger.warning("Could not deliver access request to admin")


async def access_start_command(
    message: Message,
    event_update: Update,
    bot: Bot,
    access_service: AccessService,
    bootstrap_service: TelegramBootstrapService,
    admin_user_ids: frozenset[int],
    household: Household | None = None,
    access_bootstrap: bool = False,
    is_access_admin: bool = False,
) -> None:
    if household is not None and not access_bootstrap:
        subject = (
            "Приватний бюджет"
            if message.chat.type == "private"
            else f"Групу «{household.name}»"
        )
        await message.answer(
            f"{subject} підключено до сімейного бюджету.\n\n{HELP_TEXT}",
            reply_markup=main_menu_keyboard(is_access_admin=is_access_admin),
        )
        return

    if message.from_user is None:
        return
    title, username = _chat_details(message)
    token = _start_parameter(message)
    if token:
        result = await access_service.redeem_invitation(
            token=token,
            telegram_chat_id=message.chat.id,
            chat_type=message.chat.type,
            title=title,
            username=username,
        )
        if result.status == "granted":
            context = await bootstrap_service.bootstrap(
                update_id=event_update.update_id,
                chat_id=message.chat.id,
                chat_name=title,
                user_id=message.from_user.id,
                display_name=message.from_user.full_name,
                username=message.from_user.username,
            )
            if context is not None:
                await message.answer(
                    "✅ Доступ активовано. Тепер бот готовий до роботи.",
                    reply_markup=main_menu_keyboard(is_access_admin=is_access_admin),
                )
            return
        errors = {
            "invalid": "Посилання недійсне.",
            "used": "Це запрошення вже використано.",
            "expired": "Термін дії запрошення минув.",
            "wrong_target": "Це запрошення призначене для іншого типу чату.",
        }
        await message.answer(f"❌ {errors[result.status]} Попросіть адміністратора створити нове.")
        return

    request = await access_service.request_access(
        telegram_chat_id=message.chat.id,
        requester_user_id=message.from_user.id,
        chat_type=message.chat.type,
        title=title,
        username=username,
    )
    if admin_user_ids:
        await _notify_access_admins(bot, admin_user_ids, request)
        await message.answer("🔐 Запит на доступ надіслано адміністратору.")
    else:
        await message.answer(
            "🔐 Запит збережено, але адміністратор доступу ще не налаштований. "
            "Зверніться до власника бота."
        )


def _access_text(
    requests: tuple[AccessRequest, ...], grants: tuple[AccessGrant, ...]
) -> str:
    lines = ["👥 Керування доступом", ""]
    lines.append(f"Очікують схвалення: {len(requests)}")
    lines.append(f"Активні підключення: {len(grants)}")
    if grants:
        lines.extend(["", "Підключення:"])
        for grant in grants:
            marker = "👤" if grant.chat_type == "private" else "👥"
            username = f" · @{grant.username}" if grant.username else ""
            lines.append(f"{marker} {grant.title}{username}")
    return "\n".join(lines)


async def _show_access_menu(callback: CallbackQuery, service: AccessService) -> None:
    requests = await service.list_pending_requests()
    grants = await service.list_active_grants()
    if callback.message is not None:
        await callback.message.edit_text(
            _access_text(requests, grants),
            reply_markup=access_management_keyboard(requests, grants),
        )


async def access_callback(
    callback: CallbackQuery,
    callback_data: AccessCallback,
    bot: Bot,
    access_service: AccessService,
    is_access_admin: bool = False,
) -> None:
    if not is_access_admin:
        await callback.answer("Ця дія доступна лише адміністратору.", show_alert=True)
        return
    action, value = callback_data.action, callback_data.value
    if action == "menu":
        await _show_access_menu(callback, access_service)
        await callback.answer()
        return
    if action == "close":
        if callback.message is not None:
            await callback.message.edit_text(
                "Головне меню", reply_markup=main_menu_keyboard(is_access_admin=True)
            )
        await callback.answer()
        return
    if action == "invite":
        if value not in {"private", "group"}:
            await callback.answer("Невідомий тип запрошення.", show_alert=True)
            return
        invitation = await access_service.create_invitation(
            created_by_user_id=callback.from_user.id, target_type=value
        )
        me = await bot.get_me()
        parameter = "start" if value == "private" else "startgroup"
        label = "користувачу" if value == "private" else "адміністратору групи"
        if callback.message is not None:
            await callback.message.answer(
                f"Надішліть це одноразове посилання {label}. Воно діє 24 години:\n\n"
                f"https://t.me/{me.username}?{parameter}={invitation.token}"
            )
        await callback.answer("Запрошення створено.")
        return
    if action in {"approve", "reject"}:
        try:
            request_id = int(value)
        except ValueError:
            await callback.answer("Некоректний запит.", show_alert=True)
            return
        if action == "approve":
            approved = await access_service.approve_request(
                request_id=request_id, reviewed_by_user_id=callback.from_user.id
            )
            request = approved[0] if approved else None
            notification = "✅ Адміністратор надав доступ. Надішліть /start."
        else:
            request = await access_service.reject_request(
                request_id=request_id, reviewed_by_user_id=callback.from_user.id
            )
            notification = "❌ Адміністратор відхилив запит на доступ."
        if request is None:
            await callback.answer("Запит уже оброблено.", show_alert=True)
            return
        try:
            await bot.send_message(request.telegram_chat_id, notification)
        except TelegramAPIError:
            logger.warning("Could not deliver access decision")
        await _show_access_menu(callback, access_service)
        await callback.answer("Готово.")
        return
    if action == "revoke":
        try:
            telegram_chat_id = int(value)
        except ValueError:
            await callback.answer("Некоректний чат.", show_alert=True)
            return
        grant = await access_service.revoke(telegram_chat_id)
        if grant is None:
            await callback.answer("Доступ уже відкликано.", show_alert=True)
            return
        try:
            await bot.send_message(grant.telegram_chat_id, "🔒 Доступ до бота відкликано.")
        except TelegramAPIError:
            logger.warning("Could not deliver access revocation")
        await _show_access_menu(callback, access_service)
        await callback.answer("Доступ відкликано.")
        return
    await callback.answer("Невідома дія.", show_alert=True)


def create_access_router() -> Router:
    router = Router(name="access")
    router.message.register(access_start_command, Command("start"))
    router.callback_query.register(access_callback, AccessCallback.filter())
    return router


__all__ = ["create_access_router"]
