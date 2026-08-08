"""RunContext dataclass and session-layer helpers for authenticated users."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from src.auth import crypto
from src.auth.google_oauth import GoogleUser
from src.auth.hash_util import sha256_hash


@dataclass(frozen=True)
class RunContext:
    email: str
    name: str
    picture_url: str
    user_hash: str
    is_admin: bool
    secrets: dict[str, str] = field(default_factory=dict)
    config: dict = field(default_factory=dict)


def upsert_user_from_google(google_user: GoogleUser, conn: sqlite3.Connection) -> None:
    email = google_user.email.lower()
    user_hash = sha256_hash(email)
    # ON CONFLICT preserves contact + created_at; we only refresh name/picture/updated_at.
    conn.execute(
        """
        INSERT INTO users (email, name, picture_url, user_hash)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            name = excluded.name,
            picture_url = excluded.picture_url,
            updated_at = CURRENT_TIMESTAMP
        """,
        (email, google_user.name, google_user.picture_url, user_hash),
    )
    conn.commit()


def load_run_context(
    email: str, conn: sqlite3.Connection, admin_emails: list[str]
) -> RunContext | None:
    email_lc = email.lower()
    user_row = conn.execute(
        "SELECT email, name, picture_url, user_hash FROM users WHERE email = ?",
        (email_lc,),
    ).fetchone()
    if user_row is None:
        return None

    secret_rows = conn.execute(
        "SELECT key_name, value_encrypted FROM user_secrets WHERE email = ?",
        (email_lc,),
    ).fetchall()
    secrets = {row["key_name"]: crypto.decrypt(row["value_encrypted"]) for row in secret_rows}

    config_row = conn.execute(
        "SELECT config_json FROM user_configs WHERE email = ?",
        (email_lc,),
    ).fetchone()
    config = json.loads(config_row["config_json"]) if config_row is not None else {}

    admin_lc = {a.lower() for a in admin_emails}
    is_admin = email_lc in admin_lc

    return RunContext(
        email=user_row["email"],
        name=user_row["name"] or "",
        picture_url=user_row["picture_url"] or "",
        user_hash=user_row["user_hash"],
        is_admin=is_admin,
        secrets=secrets,
        config=config,
    )


def save_user_secret(
    email: str, key_name: str, value: str, conn: sqlite3.Connection
) -> None:
    ciphertext = crypto.encrypt(value)
    conn.execute(
        """
        INSERT INTO user_secrets (email, key_name, value_encrypted)
        VALUES (?, ?, ?)
        ON CONFLICT(email, key_name) DO UPDATE SET
            value_encrypted = excluded.value_encrypted,
            updated_at = CURRENT_TIMESTAMP
        """,
        (email.lower(), key_name, ciphertext),
    )
    conn.commit()


def delete_user_secret(email: str, key_name: str, conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM user_secrets WHERE email = ? AND key_name = ?",
        (email.lower(), key_name),
    )
    conn.commit()


def save_user_config(email: str, config: dict, conn: sqlite3.Connection) -> None:
    payload = json.dumps(config)
    conn.execute(
        """
        INSERT INTO user_configs (email, config_json)
        VALUES (?, ?)
        ON CONFLICT(email) DO UPDATE SET
            config_json = excluded.config_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (email.lower(), payload),
    )
    conn.commit()


def update_user_contact(email: str, contact: str, conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE users SET contact = ?, updated_at = CURRENT_TIMESTAMP WHERE email = ?",
        (contact, email.lower()),
    )
    conn.commit()
