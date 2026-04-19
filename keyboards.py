from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
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
            ],
            [
                KeyboardButton(text="👥 Рефералка"),
                KeyboardButton(text="⭐ Stars баланс"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери действие 👇",
    )


def style_picker_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Classic Studio", callback_data="style:classic")],
            [InlineKeyboardButton(text="💎 Dubai Soft", callback_data="style:dubai")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:menu")],
        ]
    )


def buy_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 1 генерация", callback_data="invoice:single")],
            [InlineKeyboardButton(text="📅 Month Pro", callback_data="invoice:month")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back:menu")],
        ]
    )
