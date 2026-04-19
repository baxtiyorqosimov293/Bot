from __future__ import annotations

from config import settings


WELCOME_TEXT = (
    "✨ <b>AziBax AI</b>\n\n"
    "Премиальный AI-бот для стильной обработки портретов.\n\n"
    "Что умеет:\n"
    "• менять атмосферу и стиль фото\n"
    "• делать premium-портреты\n"
    "• сохранять лицо максимально близким\n"
    "• давать красивый результат за пару кликов\n\n"
    f"🎁 Новым пользователям доступно: <b>{settings.free_trials}</b> бесплатные генерации\n\n"
    "Выбери действие ниже 👇"
)

STYLE_TEXT = (
    "🎨 <b>Доступные стили</b>\n\n"
    "• <b>Classic Studio</b> — чистый premium-портрет с мягким светом\n"
    "• <b>Dubai Soft</b> — мягкий luxury-образ без сильного искажения лица"
)

BUY_TEXT = (
    "💎 <b>Пакеты генераций</b>\n\n"
    "Выбери удобный тариф 👇"
)

PHOTO_HINT_TEXT = (
    "Отправь 1 фото.\n\n"
    "Лучше всего работают фото, где:\n"
    "• лицо видно крупно\n"
    "• хороший свет\n"
    "• взгляд ближе к камере\n"
    "• без сильного наклона головы"
)

SERVICE_ERROR_TEXT = (
    "⚠️ Не удалось обработать фото.\n"
    "Попробуй ещё раз чуть позже или отправь другое фото."
)

TEMPORARY_UNAVAILABLE_TEXT = (
    "⚠️ Сервис временно недоступен. Попробуй чуть позже."
)

PAYMENT_SUCCESS_TEMPLATE = (
    "✅ <b>Оплата прошла успешно</b>\n\n"
    "Добавлено генераций: <b>{credits}</b>\n\n"
    "{cabinet_text}"
)

REFERRAL_TEMPLATE = (
    "👥 <b>Реферальная программа</b>\n\n"
    "Твоя ссылка:\n{referral_link}\n\n"
    "Ты пригласил: <b>{referrals_count}</b>\n"
    "Бонус за приглашение: <b>{bonus}</b> генераций\n\n"
    "Отправь ссылку друзьям 👇"
)

REFERRAL_ACTIVATED_TEMPLATE = (
    "🎉 <b>Рефералка активирована</b>\n\n"
    "Ты зашёл по приглашению.\n"
    "Пригласивший получил <b>{bonus}</b> бонусных генераций."
)

ADMIN_CABINET_TEXT = (
    "🛡 <b>Админ-кабинет</b>\n\n"
    "Статус: Админ\n"
    "Тестовый режим: безлимит\n"
    "Списания: отключены"
)


def user_cabinet_text(
    free_left: int,
    paid_credits: int,
    total_generations: int,
    total_paid_stars: int,
    referrals_count: int,
) -> str:
    return (
        "👤 <b>Личный кабинет</b>\n\n"
        f"Бесплатно осталось: <b>{free_left}</b>\n"
        f"Платных генераций: <b>{paid_credits}</b>\n"
        f"Всего генераций: <b>{total_generations}</b>\n"
        f"Всего оплачено Stars: <b>{total_paid_stars}</b>\n"
        f"Приглашено друзей: <b>{referrals_count}</b>"
    )


def ready_caption(style_title: str, cabinet_text: str) -> str:
    return (
        f"✅ <b>Готово</b>\n"
        f"Стиль: <b>{style_title}</b>\n\n"
        f"{cabinet_text}"
    )
