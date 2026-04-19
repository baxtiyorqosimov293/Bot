from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


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
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()

    admin_ids: set[int] = field(
        default_factory=lambda: _parse_admin_ids(
            os.getenv("ADMIN_IDS") or os.getenv("ADMIN_USER_ID") or ""
        )
    )

    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip()

    free_trials: int = _env_int("FREE_TRIALS", 2)

    price_single_xtr: int = _env_int("PRICE_SINGLE_XTR", 39)
    price_month_xtr: int = _env_int("PRICE_MONTH_XTR", 249)

    month_limit: int = _env_int("MONTH_LIMIT", 30)
    referral_bonus_credits: int = _env_int("REFERRAL_BONUS_CREDITS", 1)

    openai_image_model: str = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1").strip()
    openai_image_size: str = os.getenv("OPENAI_SIZE", "768x768").strip()

    db_path: str = os.getenv("DB_PATH", "/var/data/bot.db").strip()
    temp_dir: str = os.getenv("TEMP_DIR", "tmp").strip()

    def validate(self) -> None:
        if not self.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN не найден в переменных окружения")
        if not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY не найден в переменных окружения")

    @property
    def temp_path(self) -> Path:
        return Path(self.temp_dir)

    @property
    def database_path(self) -> Path:
        return Path(self.db_path)


settings = Settings()
settings.validate()
settings.temp_path.mkdir(parents=True, exist_ok=True)
settings.database_path.parent.mkdir(parents=True, exist_ok=True)
