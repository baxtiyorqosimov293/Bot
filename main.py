from __future__ import annotations

import asyncio
import logging
import os
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandStart
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import settings
from db import db
from generator import generator
from keyboards import buy_kb, home_reply_kb, style_picker_kb
from texts import (
    ADMIN_CABINET_TEXT,
    BUY_TEXT,
    PAYMENT_SUCCESS_TEMPLATE,
    PHOTO_HINT_TEXT,
    REFERRAL_ACTIVATED_TEMPLATE,
    REFERRAL_TEMPLATE,
    SERVICE_ERROR_TEXT,
    STYLE_TEXT,
    TEMPORARY_UNAVAILABLE_TEXT,
    WELCOME_TEXT,
    ready_caption,
    user_cabinet_text,
)
from validator import PhotoValidator

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("azibax_ai_v3")

router = Router()
validator = PhotoValidator()
user_locks: dict[int, asyncio.Lock] = {}

STYLE_TITLES = {
    "classic": "Classic Studio",
    "dubai": "Dubai Soft",
}

PAYLOADS: dict[str, dict[str, int | str]] = {
    "single": {
        "title": "1 генерация",
        "stars": settings.price_single_xtr,
        "credits": 1,
    },
    "month": {
        "title": "Month Pro",
        "stars": settings.price_month_xtr,
        "credits": settings.month_limit,
    },
}


class Flow(StatesGroup):
    waiting_single_photo = State()


def get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def ensure_user_from_message(message: Message) -> None:
    if message.from_user:
        db.ensure_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
        )


def ensure_user_from_callback(call: CallbackQuery) -> None:
    db.ensure_user(
        call.from_user.id,
        call.from_user.username,
        call.from_user.full_name,
    )


def build_cabinet_text(user_id: int) -> str:
    if is_admin(user_id):
        return ADMIN_CABINET_TEXT

    user = db.get_user(user_id)
    if not user:
        return "Пользователь не найден."

    free_left = max(0, settings.free_trials - user.free_used)
    return user_cabinet_text(
        free_left=free_left,
        paid_credits=user.paid_credits,
        total_generations=user.total_generations,
        total_paid_stars=user.total_paid_stars,
        referrals_count=user.referrals_count,
    )


async def send_main(target: Message | CallbackQuery) -> None:
    if isinstance(target, Message):
        await target.answer(
            WELCOME_TEXT,
            reply_markup=home_reply_kb(),
            parse_mode="HTML",
        )
    else:
        ensure_user_from_callback(target)
        if target.message:
            await target.message.answer(
                WELCOME_TEXT,
                reply_markup=home_reply_kb(),
                parse_mode="HTML",
            )
        await target.answer()


async def save_largest_photo(bot: Bot, message: Message, filename_prefix: str) -> str:
    if not message.photo:
        raise RuntimeError("Фото не найдено")

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    out_path = settings.temp_path / f"{filename_prefix}_{uuid4().hex}.jpg"
    await bot.download_file(file.file_path, destination=out_path)
    return str(out_path)


def cleanup_paths(paths: list[str | None]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def is_billing_error(text: str) -> bool:
    lowered = text.lower()
    return (
        "billing" in lowered
        or "hard limit" in lowered
        or "quota" in lowered
        or "insufficient credit" in lowered
        or "balance" in lowered
    )


def is_rate_limit_error(text: str) -> bool:
    lowered = text.lower()
    return (
        "429" in lowered
        or "throttled" in lowered
        or "rate limit" in lowered
        or "too many requests" in lowered
    )


@router.message(CommandStart())
async def start_cmd(
    message: Message,
    state: FSMContext,
    command: CommandObject | None = None,
) -> None:
    ensure_user_from_message(message)
    await state.clear()

    start_arg = command.args if command else None
    referral_notice = None

    if start_arg and start_arg.startswith("ref_"):
        ref_part = start_arg.replace("ref_", "").strip()
        if ref_part.isdigit():
            referrer_id = int(ref_part)
            if message.from_user.id != referrer_id:
                success = db.bind_referral(
                    new_user_id=message.from_user.id,
                    referrer_id=referrer_id,
                    bonus_credits=settings.referral_bonus_credits,
                )
                if success:
                    referral_notice = REFERRAL_ACTIVATED_TEMPLATE.format(
                        bonus=settings.referral_bonus_credits
                    )

    if referral_notice:
        await message.answer(referral_notice, parse_mode="HTML")

    await send_main(message)


@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    await state.clear()
    await send_main(message)


@router.message(Command("styles"))
async def styles_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(STYLE_TEXT, reply_markup=home_reply_kb(), parse_mode="HTML")


@router.message(Command("cabinet"))
async def cabinet_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(
        build_cabinet_text(message.from_user.id),
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


@router.message(Command("buy"))
async def buy_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(BUY_TEXT, reply_markup=buy_kb(), parse_mode="HTML")


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    await state.clear()
    await message.answer("Текущая операция отменена.", reply_markup=home_reply_kb())


@router.message(Command("admin_stats"))
async def admin_stats(message: Message) -> None:
    ensure_user_from_message(message)
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только админу.")
        return

    stats = db.stats()
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"Пользователи: <b>{stats['users']}</b>\n"
        f"Генерации: <b>{stats['generations']}</b>\n"
        f"Оплачено Stars: <b>{stats['paid_stars']}</b>",
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


@router.message(Command("star_balance"))
async def star_balance_cmd(message: Message) -> None:
    ensure_user_from_message(message)

    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только админу.")
        return

    try:
        balance = await message.bot.get_my_star_balance()
        amount = getattr(balance, "amount", 0)
        nanostar_amount = getattr(balance, "nanostar_amount", 0)

        await message.answer(
            "⭐ <b>Баланс Stars бота</b>\n\n"
            f"Stars: <b>{amount}</b>\n"
            f"NanoStars: <b>{nanostar_amount}</b>",
            reply_markup=home_reply_kb(),
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(
            f"Не удалось получить баланс Stars:\n{str(e)[:700]}",
            reply_markup=home_reply_kb(),
        )


@router.message(F.text == "🖼 Создать фото")
async def open_create_menu(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    await state.clear()
    await state.set_state(Flow.waiting_single_photo)
    await message.answer(PHOTO_HINT_TEXT, reply_markup=style_picker_kb())


@router.message(F.text == "🎨 Стили")
async def open_styles_menu(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(STYLE_TEXT, reply_markup=home_reply_kb(), parse_mode="HTML")


@router.message(F.text == "💳 Купить")
async def open_buy_menu(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(BUY_TEXT, reply_markup=buy_kb(), parse_mode="HTML")


@router.message(F.text == "💰 Баланс")
async def balance_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(
        build_cabinet_text(message.from_user.id),
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


@router.message(F.text == "⭐ Stars баланс")
async def stars_balance_from_button(message: Message) -> None:
    if is_admin(message.from_user.id):
        await star_balance_cmd(message)
    else:
        await message.answer("Эта кнопка доступна только админу.", reply_markup=home_reply_kb())


@router.message(F.text == "👥 Рефералка")
async def referral_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    bot_info = await message.bot.get_me()
    user = db.get_user(message.from_user.id)

    referral_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"
    referrals_count = user.referrals_count if user else 0

    await message.answer(
        REFERRAL_TEMPLATE.format(
            referral_link=referral_link,
            referrals_count=referrals_count,
            bonus=settings.referral_bonus_credits,
        ),
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


@router.message(F.text == "⚡ 1 генерация")
async def buy_single_from_button(message: Message) -> None:
    ensure_user_from_message(message)

    if is_admin(message.from_user.id):
        await message.answer(
            "Ты админ. У тебя безлимитный тестовый режим.",
            reply_markup=home_reply_kb(),
        )
        return

    p = PAYLOADS["single"]
    await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title=str(p["title"]),
        description="Пакет для AziBax AI",
        payload="buy_single",
        currency="XTR",
        prices=[LabeledPrice(label=str(p["title"]), amount=int(p["stars"]))],
        start_parameter="buy-single",
    )


@router.message(F.text == "📅 Month Pro")
async def buy_month_from_button(message: Message) -> None:
    ensure_user_from_message(message)

    if is_admin(message.from_user.id):
        await message.answer(
            "Ты админ. У тебя безлимитный тестовый режим.",
            reply_markup=home_reply_kb(),
        )
        return

    p = PAYLOADS["month"]
    await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title=str(p["title"]),
        description="Пакет для AziBax AI",
        payload="buy_month",
        currency="XTR",
        prices=[LabeledPrice(label=str(p["title"]), amount=int(p["stars"]))],
        start_parameter="buy-month",
    )


@router.callback_query(F.data == "back:menu")
async def back_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await send_main(call)


@router.callback_query(F.data.startswith("style:"))
async def choose_style(call: CallbackQuery, state: FSMContext) -> None:
    ensure_user_from_callback(call)
    style_code = call.data.split(":", 1)[1]

    if style_code not in STYLE_TITLES:
        await call.answer("Стиль не найден", show_alert=True)
        return

    await state.update_data(style=style_code)
    await state.set_state(Flow.waiting_single_photo)

    if call.message:
        await call.message.edit_text(
            f"Выбран стиль: {STYLE_TITLES[style_code]}\n\n"
            "Теперь отправь 1 фото."
        )

    await call.answer()


@router.callback_query(F.data.startswith("invoice:"))
async def send_invoice_handler(call: CallbackQuery) -> None:
    ensure_user_from_callback(call)

    if is_admin(call.from_user.id):
        await call.answer("Ты админ. У тебя безлимитный тестовый режим.", show_alert=True)
        return

    package = call.data.split(":", 1)[1]
    if package not in PAYLOADS:
        await call.answer("Пакет не найден", show_alert=True)
        return

    p = PAYLOADS[package]
    await call.bot.send_invoice(
        chat_id=call.from_user.id,
        title=str(p["title"]),
        description="Пакет для AziBax AI",
        payload=f"buy_{package}",
        currency="XTR",
        prices=[LabeledPrice(label=str(p["title"]), amount=int(p["stars"]))],
        start_parameter=f"buy-{package}",
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    ensure_user_from_message(message)
    payment = message.successful_payment
    payload = payment.invoice_payload.replace("buy_", "")

    if payload not in PAYLOADS:
        await message.answer("Платёж прошёл, но пакет не распознан.")
        return

    plan = PAYLOADS[payload]
    added = db.add_payment(
        user_id=message.from_user.id,
        payload=payload,
        stars=int(plan["stars"]),
        credits_added=int(plan["credits"]),
        telegram_payment_charge_id=getattr(payment, "telegram_payment_charge_id", None),
        provider_payment_charge_id=getattr(payment, "provider_payment_charge_id", None),
    )

    if not added:
        await message.answer(
            "Этот платёж уже был зачислен раньше.",
            reply_markup=home_reply_kb(),
        )
        return

    await message.answer(
        PAYMENT_SUCCESS_TEMPLATE.format(
            credits=int(plan["credits"]),
            cabinet_text=build_cabinet_text(message.from_user.id),
        ),
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


async def process_generation(message: Message, state: FSMContext, image_path: str) -> None:
    ensure_user_from_message(message)
    user_id = message.from_user.id
    lock = get_lock(user_id)

    if lock.locked():
        cleanup_paths([image_path])
        await message.answer("⏳ У тебя уже идёт генерация. Дождись завершения.")
        return

    validation = validator.validate(image_path)
    if not validation.ok:
        cleanup_paths([image_path])
        await state.clear()
        await message.answer(validation.message, reply_markup=home_reply_kb())
        return

    if (not is_admin(user_id)) and (not db.can_generate(user_id)):
        cleanup_paths([image_path])
        await state.clear()
        await message.answer(
            "Бесплатные попытки закончились. Купи пакет, чтобы продолжить.",
            reply_markup=buy_kb(),
        )
        return

    data = await state.get_data()
    style = data.get("style", "classic")
    source = "admin" if is_admin(user_id) else db.consume_generation(user_id)

    async with lock:
        await message.answer("🪄 Генерирую фото... Это может занять немного времени.")
        generated_paths: list[str] = []

        try:
            variants = await generator.generate_variants(
                image_path=image_path,
                style_code=style,
                variants_count=1,
            )

            for variant in variants:
                result_path = settings.temp_path / f"result_{user_id}_{uuid4().hex}.jpg"
                result_path.write_bytes(variant.image_bytes)
                generated_paths.append(str(result_path))

            if not generated_paths:
                raise RuntimeError("Генератор не вернул изображение")

            best_path = generated_paths[0]

            await message.answer_photo(
                FSInputFile(best_path),
                caption=ready_caption(
                    style_title=STYLE_TITLES[style],
                    cabinet_text=build_cabinet_text(user_id),
                ),
                reply_markup=home_reply_kb(),
                parse_mode="HTML",
            )

        except Exception as e:
            logger.exception("Generation failed: %s", e)

            if source != "admin":
                db.refund_generation(user_id, source)

            err_text = str(e)

            if is_billing_error(err_text):
                user_text = TEMPORARY_UNAVAILABLE_TEXT
            elif is_rate_limit_error(err_text):
                user_text = "⚠️ Сервис сейчас перегружен. Попробуй ещё раз через несколько секунд."
            else:
                user_text = SERVICE_ERROR_TEXT

            await message.answer(user_text, reply_markup=home_reply_kb())

            for admin_id in settings.admin_ids:
                try:
                    await message.bot.send_message(
                        admin_id,
                        f"Ошибка генерации у user_id={user_id}:\n{err_text[:1200]}",
                    )
                except Exception:
                    pass

        finally:
            cleanup_paths([image_path] + generated_paths)
            await state.clear()


@router.message(Flow.waiting_single_photo, F.content_type == ContentType.PHOTO)
async def single_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    image_path = await save_largest_photo(bot, message, f"{message.from_user.id}_single")
    await process_generation(message, state, image_path)


@router.message(Flow.waiting_single_photo)
async def wrong_content(message: Message) -> None:
    await message.answer("Нужно отправить именно фото.")


async def main() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Bot started in polling mode")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Bot shutdown")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
