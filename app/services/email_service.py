from __future__ import annotations

import random
import smtplib
import string
from email.message import EmailMessage
from hashlib import sha256

from app.config import Settings
from app.db import Database
from app.time_utils import parse_iso, utc_now, utc_now_iso


class EmailError(RuntimeError):
    pass


class EmailService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def send_verification(self, user_id: int, email: str) -> str | None:
        code = "".join(random.choice(string.digits) for _ in range(6))
        expires_at = utc_now().timestamp() + 15 * 60
        self.db.execute(
            """
            INSERT INTO email_verifications (user_id, email, code_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, email, _code_hash(code), str(expires_at), utc_now_iso()),
        )
        subject = "Nocturne 이메일 인증 코드"
        body = f"인증 코드: {code}"
        self.send(email, subject, body)
        if self.settings.email_provider == "console":
            return code
        return None

    def verify_code(self, user_id: int, code: str) -> tuple[bool, str | None]:
        rows = self.db.rows(
            """
            SELECT * FROM email_verifications
            WHERE user_id = ? AND verified_at IS NULL
            ORDER BY created_at DESC LIMIT 5
            """,
            (user_id,),
        )
        now_ts = utc_now().timestamp()
        for row in rows:
            try:
                expires_ts = float(row["expires_at"])
            except ValueError:
                expires_dt = parse_iso(row["expires_at"])
                expires_ts = expires_dt.timestamp() if expires_dt else 0
            if expires_ts < now_ts:
                continue
            if row["code_hash"] == _code_hash(code):
                self.db.update(
                    "UPDATE email_verifications SET verified_at = ? WHERE id = ?",
                    (utc_now_iso(), row["id"]),
                )
                self.db.update(
                    """
                    UPDATE connections
                    SET notification_email = ?, notification_email_verified = 1, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (row["email"], utc_now_iso(), user_id),
                )
                return True, None
        return False, "인증 코드가 만료되었거나 일치하지 않습니다."

    def send(self, to_email: str, subject: str, body: str) -> None:
        provider = self.settings.email_provider
        if provider == "console":
            self.db.log("email_console", payload={"to": to_email, "subject": subject, "body": body})
            return
        if provider == "smtp":
            self._smtp(to_email, subject, body)
            return
        raise EmailError(f"지원하지 않는 이메일 provider입니다: {provider}")

    def _smtp(self, to_email: str, subject: str, body: str) -> None:
        if not self.settings.smtp_host:
            raise EmailError("SMTP_HOST가 설정되어 있지 않습니다.")
        if not self.settings.email_from:
            raise EmailError("EMAIL_FROM이 설정되어 있지 않습니다.")
        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if self.settings.smtp_username:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            smtp.send_message(message)


def _code_hash(code: str) -> str:
    return sha256(code.strip().encode("utf-8")).hexdigest()
