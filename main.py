from __future__ import annotations

import asyncio
import base64
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    return int(value)


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
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", os.getenv("BOT_TOKEN", "")).strip()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    admin_ids: set[int] = _parse_admin_ids(
        os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USER_ID") or ""
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip()

    free_generations: int = _env_int("FREE_TRIALS", _env_int("FREE_GENERATIONS", 3))

    single_generation_stars: int = _env_int("PRICE_SINGLE_XTR", _env_int("SINGLE_GENERATION_STARS", 45))
    month_plan_stars: int = _env_int("PRICE_MONTH_XTR", _env_int("MONTH_PLAN_STARS", 299))
    year_plan_stars: int = _env_int("PRICE_YEAR_XTR", _env_int("YEAR_PLAN_STARS", 1999))

    month_plan_credits: int = _env_int("MONTH_LIMIT", _env_int("MONTH_PLAN_CREDITS", 40))
    year_plan_credits: int = _env_int("YEAR_LIMIT", _env_int("YEAR_PLAN_CREDITS", 400))

    openai_model: str = os.getenv("OPENAI_IMAGE_MODEL", os.getenv("OPENAI_MODEL", "gpt-image-1")).strip()
    openai_quality: str = os.getenv("OPENAI_QUALITY", "high").strip()
    openai_size: str = os.getenv("OPENAI_SIZE", "1024x1024").strip()

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
logger = logging.getLogger("photo_bot")


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
        description="Чистый премиальный студийный портрет: свет, дорогой образ, натуральная кожа.",
        prompt_fragment=(
            "Create a clean premium studio portrait with soft cinematic lighting, tasteful elegant wardrobe, "
            "natural skin texture, premium editorial feel, and an Instagram-ready polished result."
        ),
    ),
    "anime": StylePreset(
        code="anime",
        title="Anime Cinematic",
        description="Аниме-стиль с кинематографичным светом, но лицо должно остаться узнаваемым.",
        prompt_fragment=(
            "Transform the scene into a cinematic anime-inspired portrait with beautiful lighting, dynamic atmosphere, "
            "detailed outfit styling and polished composition, while keeping the real person recognizable and preserving key facial identity traits."
        ),
    ),
    "dubai": StylePreset(
        code="dubai",
        title="Dubai Luxe",
        description="Dubai / old money / luxury aesthetic: дорого, статусно, premium fashion.",
        prompt_fragment=(
            "Create a luxury Dubai old-money aesthetic portrait with premium fashion styling, refined elegant pose, "
            "upscale background, sophisticated editorial lighting, and aspirational Instagram aesthetics."
        ),
    ),
}


def style_text() -> str:
    lines = ["Доступные стили:"]
    for preset in STYLE_PRESETS.values():
        lines.append(f"• {preset.title} — {preset.description}")
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
    rows = []
    for style in STYLE_PRESETS.values():
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✨ {style.title}",
                    callback_data=f"style:{mode}:{style.code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 1 generation", callback_data="invoice:single")],
            [InlineKeyboardButton(text="📅 Month Pro", callback_data="invoice:month")],
            [InlineKeyboardButton(text="🏆 Year Pro", callback_data="invoice:year")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:menu")],
        ]
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


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
                """
            )

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
                "SELECT user_id, username, full_name, free_used, paid_credits, total_paid_stars, total_generations FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            return UserRecord(**dict(row))

    def can_generate(self, user_id: int, free_limit: int) -> bool:
        user = self.get_user(user_id)
        if not user:
            return False
        return user.free_used < free_limit or user.paid_credits > 0

    def consume_generation(self, user_id: int, free_limit: int) -> str:
        with self.connect() as conn:
            user = conn.execute(
                "SELECT free_used, paid_credits FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if not user:
                raise RuntimeError("Пользователь не найден")

            if user["free_used"] < free_limit:
                conn.execute(
                    "UPDATE users SET free_used = free_used + 1, total_generations = total_generations + 1 WHERE user_id=?",
                    (user_id,),
                )
                return "free"

            if user["paid_credits"] > 0:
                conn.execute(
                    "UPDATE users SET paid_credits = paid_credits - 1, total_generations = total_generations + 1 WHERE user_id=?",
                    (user_id,),
                )
                return "paid"

        raise RuntimeError("Нет доступных генераций")

    def refund_generation(self, user_id: int, source: str) -> None:
        with self.connect() as conn:
            if source == "free":
                conn.execute(
                    "UPDATE users SET free_used = CASE WHEN free_used > 0 THEN free_used - 1 ELSE 0 END, "
                    "total_generations = CASE WHEN total_generations > 0 THEN total_generations - 1 ELSE 0 END "
                    "WHERE user_id=?",
                    (user_id,),
                )
            elif source == "paid":
                conn.execute(
                    "UPDATE users SET paid_credits = paid_credits + 1, "
                    "total_generations = CASE WHEN total_generations > 0 THEN total_generations - 1 ELSE 0 END "
                    "WHERE user_id=?",
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
                    user_id, payload, stars, credits_added, telegram_payment_charge_id, provider_payment_charge_id
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
                "UPDATE users SET paid_credits = paid_credits + ?, total_paid_stars = total_paid_stars + ? WHERE user_id=?",
                (credits_added, stars, user_id),
            )
            return True

    def stats(self) -> dict[str, int]:
        with self.connect() as conn:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            generations = conn.execute("SELECT COALESCE(SUM(total_generations), 0) AS c FROM users").fetchone()["c"]
            paid_stars = conn.execute("SELECT COALESCE(SUM(total_paid_stars), 0) AS c FROM users").fetchone()["c"]
            return {"users": users, "generations": generations, "paid_stars": paid_stars}


class OpenAIImageService:
    def __init__(self, api_key: str, model: str, quality: str, size: str) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.quality = quality
        self.size = size

    @staticmethod
    def _encode_image(path: str | Path) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    async def stylize_person(self, image_path: str | Path, style_code: str, extra_request: str | None = None) -> bytes:
        style = STYLE_PRESETS[style_code]
        base64_image = self._encode_image(image_path)
        prompt = (
            "Edit the uploaded portrait photo. Preserve the same person identity and keep the face highly recognizable. "
            "Keep eye shape, nose shape, lips, face proportions, skin tone, and overall identity consistent with the original person. "
            "Do not replace the person with a new face. Improve composition and styling while keeping the person recognizable. "
            f"{style.prompt_fragment} "
            "You may change background, outfit styling, pose refinement, lighting, and scene mood, but the person must still look like the original person. "
            "Output one high-quality polished image."
        )
        if extra_request:
            prompt += f" Extra user request: {extra_request.strip()}."
        return await asyncio.to_thread(self._responses_image_call, [base64_image], prompt)

    async def merge_two_people(
        self,
        image_paths: Iterable[str | Path],
        style_code: str,
        extra_request: str | None = None,
    ) -> bytes:
        style = STYLE_PRESETS[style_code]
        encoded = [self._encode_image(p) for p in image_paths]
        prompt = (
            "Create one combined photorealistic image using the uploaded reference photos. "
            "If there are two different people, keep both identities recognizable and faithful to their real faces. "
            "Do not invent new faces. Make them appear naturally together in one well-composed scene with believable pose, lighting, and proportions. "
            f"{style.prompt_fragment} "
            "The result must look premium, social-media ready, and coherent as one image."
        )
        if extra_request:
            prompt += f" Extra user request: {extra_request.strip()}."
        return await asyncio.to_thread(self._responses_image_call, encoded, prompt)

    def _responses_image_call(self, base64_images: list[str], prompt: str) -> bytes:
        content = [{"type": "input_text", "text": prompt}]
        for img in base64_images:
            content.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{img}"})

        response = self.client.responses.create(
            model="gpt-4.1",
            input=[{"role": "user", "content": content}],
            tools=[
                {
                    "type": "image_generation",
                    "model": self.model,
                    "quality": self.quality,
                    "size": self.size,
                }
            ],
        )

        image_calls = [o for o in response.output if getattr(o, "type", "") == "image_generation_call"]
        if not image_calls:
            raise RuntimeError("OpenAI не вернул изображение")
        return base64.b64decode(image_calls[0].result)


class Flow(StatesGroup):
    waiting_single_photo = State()
    waiting_duo_photo_1 = State()
    waiting_duo_photo_2 = State()


Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
db = Database(settings.db_path)
image_service = OpenAIImageService(
    api_key=settings.openai_api_key,
    model=settings.openai_model,
    quality=settings.openai_quality,
    size=settings.openai_size,
)

PAYLOADS: dict[str, dict[str, Any]] = {
    "single": {"title": "1 generation", "stars": settings.single_generation_stars, "credits": 1},
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
    user = message.from_user
    if not user:
        return
    db.ensure_user(user.id, user.username, user.full_name)


def cabinet_text(user_id: int) -> str:
    user = db.get_user(user_id)
    if not user:
        return "Пользователь не найден."
    free_left = max(0, settings.free_generations - user.free_used)
    return (
        "👤 Ваш кабинет\n\n"
        f"Бесплатно осталось: {free_left}\n"
        f"Платных генераций: {user.paid_credits}\n"
        f"Всего генераций: {user.total_generations}\n"
        f"Всего оплачено Stars: {user.total_paid_stars}"
    )


async def send_main(message: Message | CallbackQuery) -> None:
    text = (
        "🔥 AI Photo Style Bot\n\n"
        "Что умеет:\n"
        "• изменить фон\n"
        "• изменить одежду и стиль\n"
        "• сохранить лицо максимально похожим\n"
        "• объединить 2 фото в 1 кадр\n\n"
        f"У нового пользователя {settings.free_generations} бесплатные генерации."
    )
    if isinstance(message, Message):
        await message.answer(text, reply_markup=main_menu_kb())
    else:
        if message.message:
            await message.message.edit_text(text, reply_markup=main_menu_kb())
        await message.answer()


async def save_largest_photo(bot: Bot, message: Message, filename_prefix: str) -> str:
    if not message.photo:
        raise RuntimeError("Фото не найдено в сообщении")
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
async def start_cmd(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    await state.clear()
    await send_main(message)


@router.message(Command("menu"))
async def menu_cmd(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    await state.clear()
    await send_main(message)


@router.message(Command("styles"))
async def styles_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(style_text())


@router.message(Command("cabinet"))
async def cabinet_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(cabinet_text(message.from_user.id))


@router.message(Command("buy"))
async def buy_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer("Выберите пакет:", reply_markup=buy_kb())


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    ensure_user_from_message(message)
    await message.answer(
        "Как пользоваться:\n"
        "1) Нажми «Один человек» или «Два человека»\n"
        "2) Выбери стиль\n"
        "3) Отправь фото\n"
        "4) Получи готовый результат\n\n"
        "Совет: лучше работает фото с хорошим светом и видимым лицом.\n"
        "Команда /cancel отменяет текущую загрузку."
    )


@router.message(Command("cancel"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    ensure_user_from_message(message)
    data = await state.get_data()
    cleanup_paths([data.get("duo1")])
    await state.clear()
    await message.answer("Текущая операция отменена.", reply_markup=main_menu_kb())


@router.message(Command("admin_stats"))
async def admin_stats(message: Message) -> None:
    ensure_user_from_message(message)
    if message.from_user.id not in settings.admin_ids:
        return
    s = db.stats()
    await message.answer(
        f"📊 Статистика\n\nПользователи: {s['users']}\nГенерации: {s['generations']}\nОплачено Stars: {s['paid_stars']}"
    )


@router.callback_query(F.data == "back:menu")
async def back_menu(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cleanup_paths([data.get("duo1")])
    await state.clear()
    await send_main(call)


@router.callback_query(F.data == "show:styles")
async def show_styles(call: CallbackQuery) -> None:
    if call.message:
        await call.message.edit_text(style_text(), reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "show:cabinet")
async def show_cabinet(call: CallbackQuery) -> None:
    if call.message:
        await call.message.edit_text(cabinet_text(call.from_user.id), reply_markup=main_menu_kb())
    await call.answer()


@router.callback_query(F.data == "buy:menu")
async def buy_menu(call: CallbackQuery) -> None:
    if call.message:
        await call.message.edit_text("Выберите пакет:", reply_markup=buy_kb())
    await call.answer()


@router.callback_query(F.data.startswith("mode:"))
async def choose_mode(call: CallbackQuery) -> None:
    mode = call.data.split(":", 1)[1]
    if call.message:
        await call.message.edit_text("Выберите стиль:", reply_markup=style_picker_kb(mode))
    await call.answer()


@router.callback_query(F.data.startswith("style:"))
async def choose_style(call: CallbackQuery, state: FSMContext) -> None:
    _, mode, style_code = call.data.split(":", 2)
    if style_code not in STYLE_PRESETS:
        await call.answer("Стиль не найден", show_alert=True)
        return

    await state.update_data(mode=mode, style=style_code)
    if mode == "single":
        await state.set_state(Flow.waiting_single_photo)
        if call.message:
            await call.message.edit_text(
                f"Выбран стиль: {STYLE_PRESETS[style_code].title}\n\nОтправь 1 фото.",
                reply_markup=None,
            )
    else:
        await state.set_state(Flow.waiting_duo_photo_1)
        if call.message:
            await call.message.edit_text(
                f"Выбран стиль: {STYLE_PRESETS[style_code].title}\n\nОтправь первое фото.",
                reply_markup=None,
            )
    await call.answer()


async def process_generation(message: Message, state: FSMContext, paths: list[str]) -> None:
    ensure_user_from_message(message)
    user_id = message.from_user.id
    lock = get_lock(user_id)
    if lock.locked():
        await message.answer("⏳ У тебя уже идет генерация. Дождись завершения.")
        cleanup_paths(paths)
        return

    if not db.can_generate(user_id, settings.free_generations):
        await message.answer(
            "Бесплатные попытки закончились. Купи пакет, чтобы продолжить.",
            reply_markup=buy_kb(),
        )
        cleanup_paths(paths)
        await state.clear()
        return

    data = await state.get_data()
    style = data["style"]
    source = db.consume_generation(user_id, settings.free_generations)

    async with lock:
        await message.answer("🪄 Генерирую фото... Это может занять немного времени.")
        result_path: Path | None = None
        try:
            if len(paths) == 1:
                image_bytes = await image_service.stylize_person(paths[0], style)
            else:
                image_bytes = await image_service.merge_two_people(paths, style)

            result_path = Path(settings.temp_dir) / f"result_{user_id}_{uuid4().hex}.png"
            result_path.write_bytes(image_bytes)
            await message.answer_photo(
                FSInputFile(result_path),
                caption=(
                    f"Готово ✅\nСтиль: {STYLE_PRESETS[style].title}\n\n"
                    f"{cabinet_text(user_id)}\n\n"
                    "Чтобы сделать ещё — открой /menu"
                ),
            )
        except Exception as e:
            logger.exception("Generation failed: %s", e)
            db.refund_generation(user_id, source)
            await message.answer(
                "Не удалось сгенерировать изображение. Попытка не списана. Попробуй другое фото с более четким лицом."
            )
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


@router.message((Flow.waiting_single_photo | Flow.waiting_duo_photo_1 | Flow.waiting_duo_photo_2))
async def wrong_content(message: Message) -> None:
    await message.answer("Нужно отправить именно фото.")


@router.callback_query(F.data.startswith("invoice:"))
async def send_invoice_handler(call: CallbackQuery) -> None:
    package = call.data.split(":", 1)[1]
    if package not in PAYLOADS:
        await call.answer("Пакет не найден", show_alert=True)
        return

    p = PAYLOADS[package]
    await call.bot.send_invoice(
        chat_id=call.from_user.id,
        title=p["title"],
        description=f"Пакет {p['title']} для AI Photo Style Bot",
        payload=f"buy_{package}",
        currency="XTR",
        prices=[LabeledPrice(label=p["title"], amount=p["stars"])],
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
        await message.answer("Платеж прошел, но пакет не распознан. Проверь настройки.")
        return

    plan = PAYLOADS[payload]
    added = db.add_payment(
        user_id=message.from_user.id,
        payload=payload,
        stars=plan["stars"],
        credits_added=plan["credits"],
        telegram_payment_charge_id=getattr(payment, "telegram_payment_charge_id", None),
        provider_payment_charge_id=getattr(payment, "provider_payment_charge_id", None),
    )
    if not added:
        await message.answer(
            "Этот платеж уже был зачислен раньше. Баланс не изменен повторно.",
            reply_markup=main_menu_kb(),
        )
        return

    await message.answer(
        f"Оплата прошла ✅\n\nДобавлено генераций: {plan['credits']}\nТекущий баланс:\n{cabinet_text(message.from_user.id)}",
        reply_markup=main_menu_kb(),
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
