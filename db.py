from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from config import settings


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
        self.init_db()
        self.migrate_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
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
                conn.execute(
                    "ALTER TABLE users ADD COLUMN referral_bonus_given INTEGER NOT NULL DEFAULT 0"
                )
            if "referrals_count" not in columns:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN referrals_count INTEGER NOT NULL DEFAULT 0"
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
                """
                SELECT user_id, username, full_name, free_used, paid_credits,
                       total_paid_stars, total_generations, referrer_id,
                       referral_bonus_given, referrals_count
                FROM users
                WHERE user_id=?
                """,
                (user_id,),
            ).fetchone()

            return UserRecord(**dict(row)) if row else None

    def can_generate(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return bool(user and (user.free_used < settings.free_trials or user.paid_credits > 0))

    def consume_generation(self, user_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT free_used, paid_credits FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()

            if not row:
                raise RuntimeError("Пользователь не найден")

            if row["free_used"] < settings.free_trials:
                conn.execute(
                    """
                    UPDATE users
                    SET free_used=free_used+1,
                        total_generations=total_generations+1
                    WHERE user_id=?
                    """,
                    (user_id,),
                )
                return "free"

            if row["paid_credits"] > 0:
                conn.execute(
                    """
                    UPDATE users
                    SET paid_credits=paid_credits-1,
                        total_generations=total_generations+1
                    WHERE user_id=?
                    """,
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
                "SELECT referrer_id FROM users WHERE user_id=?",
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
                "generations": conn.execute(
                    "SELECT COALESCE(SUM(total_generations),0) AS c FROM users"
                ).fetchone()["c"],
                "paid_stars": conn.execute(
                    "SELECT COALESCE(SUM(total_paid_stars),0) AS c FROM users"
                ).fetchone()["c"],
            }


db = Database(str(settings.database_path))
