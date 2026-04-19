from __future__ import annotations

import asyncio
import base64
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)).strip())


def _parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            result.add(int(item))
    return result


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    admin_ids: set[int] = field(
        default_factory=lambda: _parse_admin_ids(
            os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USER_ID") or ""
        )
    )

    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip()
    free_generations: int = _env_int("FREE_TRIALS", 3)

    single_generation_stars: int = _env_int("PRICE_SINGLE_XTR", 45)
    month_plan_stars: int = _env_int("PRICE_MONTH_XTR", 299)
    year_plan_stars: int = _env_int("PRICE_YEAR_XTR", 1999)

    month_plan_credits: int = _env_int("MONTH_LIMIT", 40)
    year_plan_credits: int = _env_int("YEAR_LIMIT", 400)

    referral_bonus_credits: int = _env_int("REFERRAL_BONUS_CREDITS", 1)

    image_model: str = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip()
    image_size: str = os.getenv("OPENAI_SIZE", "768x768").strip()

    db_path: str = os.getenv("DB_PATH", "/var/data/bot.db").strip()
    temp_dir: str = os.getenv("TEMP_DIR", "tmp").strip()

    def validate(self) -> None:
        if not self.bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN не найден в переменных окружения")
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY не найден в переменных окружения")


settings = Settings()
settings.validate()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("azibax_photo_bot")


@dataclass(frozen=True)
class StylePreset:
    code: str
    title: str
    description: str
    prompt_fragment: str


STYLE_PRESETS: dict[str, StylePreset] = {
    "classic": StylePreset(
        code="classic",
        title="Classic Studio",
        description="Чистый premium-портрет с мягким светом.",
        prompt_fragment=(
            "Use a subtle clean studio portrait style with soft natural lighting and minimal changes. "
            "Do not over-beautify or over-correct the face."
        ),
    ),
    "anime": StylePreset(
        code="anime",
        title="Anime Cinematic",
        description="Мягкий аниме-стиль с сохранением лица.",
        prompt_fragment=(
            "Apply a soft cinematic anime-inspired mood only if the real face remains recognizable. "
            "Do not turn the person into a different character."
        ),
    ),
    "dubai": StylePreset(
        code="dubai",
        title="Dubai Luxe",
        description="Luxury / Dubai / old money aesthetic.",
        prompt_fragment=(
            "Apply a subtle luxury Dubai old-money aesthetic with elegant lighting, premium styling, "
            "and upscale atmosphere, but keep the real face natural and recognizable."
        ),
    ),
}


def style_text() -> str:
    lines = ["🎨 <b>Доступные стили</b>\n"]
    for preset in STYLE_PRESETS.values():
        lines.append(f"• <b>{preset.title}</b> — {preset.description}")
    return "\n".join(lines)


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧍 Один человек", callback_data="mode:single")],
            [InlineKeyboardButton(text="🧑‍🤝‍🧑 Два человека", callback_data="mode:duo")],
            [InlineKeyboardButton(text="🎨 Стили", callback_data="show:styles")],
            [InlineKeyboardButton(text="💎 Купить генерации", callback_data="buy:menu")],
            [InlineKeyboardButton(text="👤 Кабинет", callback_data="show:cabinet")],
        ]
    )


def style_picker_kb(mode: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"✨ {style.title}", callback_data=f"style:{mode}:{style.code}")]
        for style in STYLE_PRESETS.values()
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 1 генерация", callback_data="invoice:single")],
            [InlineKeyboardButton(text="📅 Month Pro", callback_data="invoice:month")],
            [InlineKeyboardButton(text="🏆 Year Pro", callback_data="invoice:year")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:menu")],
        ]
    )


def home_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🖼 Создать фото"),
                KeyboardButton(text="🎨 Стили"),
            ],
            [
                KeyboardButton(text="💳 Купить"),
                KeyboardButton(text="💰 Баланс"),
            ],
            [
                KeyboardButton(text="⚡ 1 генерация"),
                KeyboardButton(text="📅 Month Pro"),
                KeyboardButton(text="🏆 Year Pro"),
            ],
            [
                KeyboardButton(text="👥 Рефералка"),
                KeyboardButton(text="⭐ Stars баланс"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие 👇",
    )


@dataclass
class UserRecord:
    user_id: int
    username: str | None
    full_name: str
    free_used: int
    paid_credits: int
    total_paid_stars: int
    total_generations: int
    referrer_id: int | None
    referral_bonus_given: int
    referrals_count: int


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        self.migrate_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    free_used INTEGER NOT NULL DEFAULT 0,
                    paid_credits INTEGER NOT NULL DEFAULT 0,
                    total_paid_stars INTEGER NOT NULL DEFAULT 0,
                    total_generations INTEGER NOT NULL DEFAULT 0,
                    referrer_id INTEGER,
                    referral_bonus_given INTEGER NOT NULL DEFAULT 0,
                    referrals_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    stars INTEGER NOT NULL,
                    credits_added INTEGER NOT NULL,
                    telegram_payment_charge_id TEXT UNIQUE,
                    provider_payment_charge_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def migrate_db(self) -> None:
        with self.connect() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }

            if "referrer_id" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
            if "referral_bonus_given" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN referral_bonus_given INTEGER NOT NULL DEFAULT 0")
            if "referrals_count" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN referrals_count INTEGER NOT NULL DEFAULT 0")

    def ensure_user(self, user_id: int, username: str | None, full_name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, full_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name
                """,
                (user_id, username, full_name),
            )

    def get_user(self, user_id: int) -> UserRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, username, full_name, free_used, paid_credits,
                       total_paid_stars, total_generations,
                       referrer_id, referral_bonus_given, referrals_count
                FROM users
                WHERE user_id=?
                """,
                (user_id,),
            ).fetchone()
            return UserRecord(**dict(row)) if row else None

    def can_generate(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return bool(user and (user.free_used < settings.free_generations or user.paid_credits > 0))

    def consume_generation(self, user_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT free_used, paid_credits FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if not row:
                raise RuntimeError("Пользователь не найден")

            if row["free_used"] < settings.free_generations:
                conn.execute(
                    "UPDATE users SET free_used=free_used+1, total_generations=total_generations+1 WHERE user_id=?",
                    (user_id,),
                )
                return "free"

            if row["paid_credits"] > 0:
                conn.execute(
                    "UPDATE users SET paid_credits=paid_credits-1, total_generations=total_generations+1 WHERE user_id=?",
                    (user_id,),
                )
                return "paid"

        raise RuntimeError("Нет доступных генераций")

    def refund_generation(self, user_id: int, source: str) -> None:
        with self.connect() as conn:
            if source == "free":
                conn.execute(
                    """
                    UPDATE users
                    SET free_used = CASE WHEN free_used > 0 THEN free_used - 1 ELSE 0 END,
                        total_generations = CASE WHEN total_generations > 0 THEN total_generations - 1 ELSE 0 END
                    WHERE user_id=?
                    """,
                    (user_id,),
                )
            elif source == "paid":
                conn.execute(
                    """
                    UPDATE users
                    SET paid_credits = paid_credits + 1,
                        total_generations = CASE WHEN total_generations > 0 THEN total_generations - 1 ELSE 0 END
                    WHERE user_id=?
                    """,
                    (user_id,),
                )

    def add_payment(
        self,
        user_id: int,
        payload: str,
        stars: int,
        credits_added: int,
        telegram_payment_charge_id: str | None = None,
        provider_payment_charge_id: str | None = None,
    ) -> bool:
        with self.connect() as conn:
            if telegram_payment_charge_id:
                existing = conn.execute(
                    "SELECT id FROM payments WHERE telegram_payment_charge_id=?",
                    (telegram_payment_charge_id,),
                ).fetchone()
                if existing:
                    return False

            conn.execute(
                """
                INSERT INTO payments (
                    user_id, payload, stars, credits_added,
                    telegram_payment_charge_id, provider_payment_charge_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    payload,
                    stars,
                    credits_added,
                    telegram_payment_charge_id,
                    provider_payment_charge_id,
                ),
            )
            conn.execute(
                """
                UPDATE users
                SET paid_credits=paid_credits+?,
                    total_paid_stars=total_paid_stars+?
                WHERE user_id=?
                """,
                (credits_added, stars, user_id),
            )
            return True

    def bind_referral(self, new_user_id: int, referrer_id: int, bonus_credits: int) -> bool:
        with self.connect() as conn:
            new_user = conn.execute(
                """
                SELECT referrer_id, referral_bonus_given
                FROM users
                WHERE user_id=?
                """,
                (new_user_id,),
            ).fetchone()

            referrer = conn.execute(
                "SELECT user_id FROM users WHERE user_id=?",
                (referrer_id,),
            ).fetchone()

            if not new_user or not referrer:
                return False

            if new_user_id == referrer_id:
                return False

            if new_user["referrer_id"] is not None:
                return False

            conn.execute(
                """
                UPDATE users
                SET referrer_id=?,
                    referral_bonus_given=1
                WHERE user_id=?
                """,
                (referrer_id, new_user_id),
            )

            conn.execute(
                """
                UPDATE users
                SET paid_credits=paid_credits+?,
                    referrals_count=referrals_count+1
                WHERE user_id=?
                """,
                (bonus_credits, referrer_id),
            )
            return True

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "users": conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"],
                "generations": conn.execute("SELECT COALESCE(SUM(total_generations),0) AS c FROM users").fetchone()["c"],
                "paid_stars": conn.execute("SELECT COALESCE(SUM(total_paid_stars),0) AS c FROM users").fetchone()["c"],
            }


class OpenAIImageService:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key)

    def _edit_images(self, image_paths: list[str], prompt: str) -> bytes:
        files = [open(path, "rb") for path in image_paths]
        try:
            response = self.client.images.edit(
                model=settings.image_model,
                image=files if len(files) > 1 else files[0],
                prompt=prompt,
                size=settings.image_size,
                n=1,
            )
        finally:
            for f in files:
                try:
                    f.close()
                except Exception:
                    pass

        if not getattr(response, "data", None):
            raise RuntimeError("OpenAI не вернул data")

        item = response.data[0]
        b64_json = getattr(item, "b64_json", None)
        if not b64_json:
            raise RuntimeError("OpenAI не вернул b64_json")

        return base64.b64decode(b64_json)

    async def stylize_person(self, image_path: str, style_code: str) -> bytes:
        style = STYLE_PRESETS[style_code]
        prompt = (
            "Edit the uploaded portrait photo and keep the same real person recognizable. "
            "Preserve the person's natural face, expression, and overall likeness. "
            "Do not replace the face with a different person. "
            "Keep the result realistic, soft, and natural-looking. "
            "You may improve lighting, background, styling, and composition, "
            "but the final image must still clearly look like the original person. "
            f"{style.prompt_fragment}"
        )
        return await asyncio.to_thread(self._edit_images, [image_path], prompt)

    async def merge_two_people(self, image_paths: Iterable[str], style_code: str) -> bytes:
        style = STYLE_PRESETS[style_code]
        prompt = (
            "Create one combined realistic portrait using the two uploaded reference photos. "
            "These are two specific real people. Preserve both identities very accurately. "
            "Do not beautify them into different people. Do not change age, ethnicity, skin tone, "
            "face shape, eye shape, eyebrow shape, nose shape, lip shape, or jawline. "
            "Keep each person's face highly recognizable and faithful to their own reference. "
            "The first person must still look exactly like the first uploaded photo. "
            "The second person must still look exactly like the second uploaded photo. "
            "Do not invent new faces, do not blend their identities, and do not average facial features. "
            "Make them stand together naturally in one coherent scene with realistic proportions and lighting. "
            "Use premium composition, but identity accuracy is more important than style. "
            f"{style.prompt_fragment}"
        )
        return await asyncio.to_thread(self._edit_images, list(image_paths), prompt)


class Flow(StatesGroup):
    waiting_single_photo = State()
    waiting_duo_photo_1 = State()
    waiting_duo_photo_2 = State()


Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
db = Database(settings.db_path)
image_service = OpenAIImageService()

PAYLOADS: dict[str, dict[str, int | str]] = {
    "single": {"title": "1 генерация", "stars": settings.single_generation_stars, "credits": 1},
    "month": {"title": "Month Pro", "stars": settings.month_plan_stars, "credits": settings.month_plan_credits},
    "year": {"title": "Year Pro", "stars": settings.year_plan_stars, "credits": settings.year_plan_credits},
}

router = Router()
user_locks: dict[int, asyncio.Lock] = {}


def get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


def ensure_user_from_message(message: Message) -> None:
    if message.from_user:
        db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.full_name)


def ensure_user_from_callback(call: CallbackQuery) -> None:
    user = call.from_user
    db.ensure_user(user.id, user.username, user.full_name)


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def cabinet_text(user_id: int) -> str:
    if is_admin(user_id):
        return (
            "🛡 <b>Админ-кабинет</b>\n\n"
            "Статус: Админ\n"
            "Тестовый режим: безлимит\n"
            "Списания: отключены"
        )

    user = db.get_user(user_id)
    if not user:
        return "Пользователь не найден."

    free_left = max(0, settings.free_generations - user.free_used)
    return (
        "👤 <b>Личный кабинет</b>\n\n"
        f"Бесплатно осталось: <b>{free_left}</b>\n"
        f"Платных генераций: <b>{user.paid_credits}</b>\n"
        f"Всего генераций: <b>{user.total_generations}</b>\n"
        f"Всего оплачено Stars: <b>{user.total_paid_stars}</b>\n"
        f"Приглашено друзей: <b>{user.referrals_count}</b>"
    )


async def send_main(target: Message | CallbackQuery) -> None:
    text = (
        "✨ <b>AziBax AI</b>\n\n"
        "Премиальный AI-бот для фото-стилизации.\n\n"
        "Что умеет:\n"
        "• менять фон и атмосферу кадра\n"
        "• менять стиль и образ\n"
        "• сохранять лицо максимально похожим\n"
        "• объединять 2 фото в 1 красивый кадр\n\n"
        f"🎁 Новым пользователям доступно: <b>{settings.free_generations}</b> бесплатные генерации\n\n"
        "Выбери действие ниже 👇"
    )

    if isinstance(target, Message):
        await target.answer(text, reply_markup=home_reply_kb(), parse_mode="HTML")
    else:
        ensure_user_from_callback(target)
        if target.message:
            await target.message.answer(text, reply_markup=home_reply_kb(), parse_mode="HTML")
        await target.answer()


async def save_largest_photo(bot: Bot, message: Message, filename_prefix: str) -> str:
    if not message.photo:
        raise RuntimeError("Фото не найдено")
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    out_path = Path(settings.temp_dir) / f"{filename_prefix}_{uuid4().hex}.jpg"
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
                    referral_notice = (
                        "🎉 <b>Рефералка активирована</b>\n\n"
                        "Ты зашёл по приглашению.\n"
                        f"Пригласивший получил <b>{settings.referral_bonus_credits}</b> бонусных генераций."
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
    await message.answer(style_text(), reply_markup=home_reply_kb(), parse_mode="HTML")


@router.message(Command("cabinet"))
async def cabinet_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(cabinet_text(message.from_user.id), reply_markup=home_reply_kb(), parse_mode="HTML")


@router.message(Command("buy"))
async def buy_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(
        "💎 <b>Пакеты генераций</b>\n\nВыбери удобный тариф 👇",
        reply_markup=buy_kb(),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    data = await state.get_data()
    cleanup_paths([data.get("duo1")])
    await state.clear()
    await message.answer("Текущая операция отменена.", reply_markup=home_reply_kb())


@router.message(Command("admin_stats"))
async def admin_stats(message: Message) -> None:
    ensure_user_from_message(message)
    if not is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только админу.")
        return

    s = db.stats()
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"Пользователи: <b>{s['users']}</b>\n"
        f"Генерации: <b>{s['generations']}</b>\n"
        f"Оплачено Stars: <b>{s['paid_stars']}</b>",
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
            "Не удалось получить баланс Stars.\n\n"
            f"Ошибка: {str(e)[:700]}",
            reply_markup=home_reply_kb(),
        )


@router.message(F.text == "🖼 Создать фото")
async def open_create_menu(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧍 Один человек", callback_data="mode:single")],
            [InlineKeyboardButton(text="🧑‍🤝‍🧑 Два человека", callback_data="mode:duo")],
        ]
    )
    await message.answer("Выбери режим генерации:", reply_markup=kb)


@router.message(F.text == "🎨 Стили")
async def open_styles_menu(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(style_text(), reply_markup=home_reply_kb(), parse_mode="HTML")


@router.message(F.text == "💳 Купить")
async def open_buy_menu(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(
        "✨ <b>Открой больше генераций</b>\n\n"
        "Выбери пакет, который подходит тебе 👇",
        reply_markup=buy_kb(),
        parse_mode="HTML",
    )


@router.message(F.text == "⚡ 1 генерация")
async def buy_single_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    if is_admin(message.from_user.id):
        await message.answer("Ты админ. У тебя безлимитный тестовый режим.", reply_markup=home_reply_kb())
        return
    p = PAYLOADS["single"]
    await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title=str(p["title"]),
        description="Пакет для AI Photo Style Bot",
        payload="buy_single",
        currency="XTR",
        prices=[LabeledPrice(label=str(p["title"]), amount=int(p["stars"]))],
        start_parameter="buy-single",
    )


@router.message(F.text == "📅 Month Pro")
async def buy_month_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    if is_admin(message.from_user.id):
        await message.answer("Ты админ. У тебя безлимитный тестовый режим.", reply_markup=home_reply_kb())
        return
    p = PAYLOADS["month"]
    await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title=str(p["title"]),
        description="Пакет для AI Photo Style Bot",
        payload="buy_month",
        currency="XTR",
        prices=[LabeledPrice(label=str(p["title"]), amount=int(p["stars"]))],
        start_parameter="buy-month",
    )


@router.message(F.text == "🏆 Year Pro")
async def buy_year_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    if is_admin(message.from_user.id):
        await message.answer("Ты админ. У тебя безлимитный тестовый режим.", reply_markup=home_reply_kb())
        return
    p = PAYLOADS["year"]
    await message.bot.send_invoice(
        chat_id=message.from_user.id,
        title=str(p["title"]),
        description="Пакет для AI Photo Style Bot",
        payload="buy_year",
        currency="XTR",
        prices=[LabeledPrice(label=str(p["title"]), amount=int(p["stars"]))],
        start_parameter="buy-year",
    )


@router.message(F.text == "💰 Баланс")
async def balance_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(cabinet_text(message.from_user.id), reply_markup=home_reply_kb(), parse_mode="HTML")


@router.message(F.text == "⭐ Stars баланс")
async def stars_balance_from_button(message: Message) -> None:
    await star_balance_cmd(message)


@router.message(F.text == "👥 Рефералка")
async def referral_from_button(message: Message) -> None:
    ensure_user_from_message(message)
    bot_info = await message.bot.get_me()
    user = db.get_user(message.from_user.id)
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{message.from_user.id}"

    referrals_count = user.referrals_count if user else 0

    await message.answer(
        "👥 <b>Реферальная программа</b>\n\n"
        f"Твоя ссылка:\n{referral_link}\n\n"
        f"Ты пригласил: <b>{referrals_count}</b>\n"
        f"Бонус за приглашение: <b>{settings.referral_bonus_credits}</b> генераций\n\n"
        "Отправь ссылку друзьям 👇",
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "back:menu")
async def back_menu(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cleanup_paths([data.get("duo1")])
    await state.clear()
    await send_main(call)


@router.callback_query(F.data == "show:styles")
async def show_styles(call: CallbackQuery) -> None:
    ensure_user_from_callback(call)
    if call.message:
        await call.message.edit_text(style_text(), reply_markup=main_menu_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "show:cabinet")
async def show_cabinet(call: CallbackQuery) -> None:
    ensure_user_from_callback(call)
    if call.message:
        await call.message.edit_text(cabinet_text(call.from_user.id), reply_markup=main_menu_kb(), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "buy:menu")
async def buy_menu(call: CallbackQuery) -> None:
    ensure_user_from_callback(call)
    if call.message:
        await call.message.edit_text(
            "💎 <b>Пакеты генераций</b>\n\nВыбери удобный тариф 👇",
            reply_markup=buy_kb(),
            parse_mode="HTML",
        )
    await call.answer()


@router.callback_query(F.data.startswith("mode:"))
async def choose_mode(call: CallbackQuery) -> None:
    ensure_user_from_callback(call)
    mode = call.data.split(":", 1)[1]
    if call.message:
        await call.message.edit_text("Выбери стиль:", reply_markup=style_picker_kb(mode))
    await call.answer()


@router.callback_query(F.data.startswith("style:"))
async def choose_style(call: CallbackQuery, state: FSMContext) -> None:
    ensure_user_from_callback(call)
    _, mode, style_code = call.data.split(":", 2)

    if style_code not in STYLE_PRESETS:
        await call.answer("Стиль не найден", show_alert=True)
        return

    await state.update_data(mode=mode, style=style_code)

    if mode == "single":
        await state.set_state(Flow.waiting_single_photo)
        if call.message:
            await call.message.edit_text(
                f"Выбран стиль: {STYLE_PRESETS[style_code].title}\n\nОтправь 1 фото."
            )
    else:
        await state.set_state(Flow.waiting_duo_photo_1)
        if call.message:
            await call.message.edit_text(
                f"Выбран стиль: {STYLE_PRESETS[style_code].title}\n\nОтправь первое фото."
            )

    await call.answer()


async def process_generation(message: Message, state: FSMContext, paths: list[str]) -> None:
    ensure_user_from_message(message)
    user_id = message.from_user.id
    lock = get_lock(user_id)

    if lock.locked():
        await message.answer("⏳ У тебя уже идёт генерация. Дождись завершения.")
        cleanup_paths(paths)
        return

    if (not is_admin(user_id)) and (not db.can_generate(user_id)):
        await message.answer(
            "Бесплатные попытки закончились. Купи пакет, чтобы продолжить.",
            reply_markup=buy_kb(),
        )
        cleanup_paths(paths)
        await state.clear()
        return

    data = await state.get_data()
    style = data["style"]
    source = "admin" if is_admin(user_id) else db.consume_generation(user_id)

    async with lock:
        await message.answer("🪄 Генерирую фото... Это может занять немного времени.")
        result_path: Path | None = None

        try:
            if len(paths) == 1:
                image_bytes = await image_service.stylize_person(paths[0], style)
            else:
                image_bytes = await image_service.merge_two_people(paths, style)

            result_path = Path(settings.temp_dir) / f"result_{user_id}_{uuid4().hex}.jpg"
            result_path.write_bytes(image_bytes)

            await message.answer_photo(
                FSInputFile(result_path),
                caption=(
                    f"✅ <b>Готово</b>\n"
                    f"Стиль: <b>{STYLE_PRESETS[style].title}</b>\n\n"
                    f"{cabinet_text(user_id)}"
                ),
                reply_markup=home_reply_kb(),
                parse_mode="HTML",
            )

        except Exception as e:
            logger.exception("Generation failed: %s", e)
            if source != "admin":
                db.refund_generation(user_id, source)

            err_text = str(e)[:800]
            await message.answer(
                "⚠️ <b>Не удалось сгенерировать фото</b>\n\n"
                f"Техническая ошибка:\n{err_text}",
                reply_markup=home_reply_kb(),
                parse_mode="HTML",
            )

            for admin_id in settings.admin_ids:
                try:
                    await message.bot.send_message(
                        admin_id,
                        f"Ошибка генерации у user_id={user_id}:\n{err_text}",
                    )
                except Exception:
                    pass

        finally:
            cleanup_paths(paths + ([str(result_path)] if result_path else []))
            await state.clear()


@router.message(Flow.waiting_single_photo, F.content_type == ContentType.PHOTO)
async def single_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    path = await save_largest_photo(bot, message, f"{message.from_user.id}_single")
    await process_generation(message, state, [path])


@router.message(Flow.waiting_duo_photo_1, F.content_type == ContentType.PHOTO)
async def duo_photo_1(message: Message, state: FSMContext, bot: Bot) -> None:
    path = await save_largest_photo(bot, message, f"{message.from_user.id}_duo1")
    await state.update_data(duo1=path)
    await state.set_state(Flow.waiting_duo_photo_2)
    await message.answer("Отлично. Теперь отправь второе фото.")


@router.message(Flow.waiting_duo_photo_2, F.content_type == ContentType.PHOTO)
async def duo_photo_2(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    path1 = data.get("duo1")
    if not path1:
        await state.clear()
        await message.answer("Первое фото потерялось. Начни заново через /menu")
        return

    path2 = await save_largest_photo(bot, message, f"{message.from_user.id}_duo2")
    await process_generation(message, state, [path1, path2])


@router.message(Flow.waiting_single_photo)
@router.message(Flow.waiting_duo_photo_1)
@router.message(Flow.waiting_duo_photo_2)
async def wrong_content(message: Message) -> None:
    await message.answer("Нужно отправить именно фото.")


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
        description="Пакет для AI Photo Style Bot",
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
        f"✅ <b>Оплата прошла успешно</b>\n\n"
        f"Добавлено генераций: <b>{int(plan['credits'])}</b>\n\n"
        f"{cabinet_text(message.from_user.id)}",
        reply_markup=home_reply_kb(),
        parse_mode="HTML",
    )


async def main() -> None:
    bot = Bot(token=settings.bot_token)
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
