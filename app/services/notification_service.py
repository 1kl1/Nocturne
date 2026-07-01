from __future__ import annotations

from app.agent.types import NotificationResult
from app.config import Settings
from app.db import Database
from app.security import SecretBox
from app.services.email_service import EmailService
from app.services.slack_service import SlackService


class NotificationService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        secret_box: SecretBox,
        slack: SlackService,
        email: EmailService,
    ):
        self.settings = settings
        self.db = db
        self.secret_box = secret_box
        self.slack = slack
        self.email = email

    def send_run_summary(self, user_id: int, run: dict[str, object], inbox_url: str | None) -> NotificationResult:
        connection = self.db.connection_for_user(user_id)
        prefs = self.db.notification_settings_for_user(user_id)
        if int(prefs["notify_zero"]) == 0 and int(run.get("proposal_count") or 0) == 0 and int(run.get("held_count") or 0) == 0:
            return NotificationResult()

        text = self._message(run, inbox_url)
        result = NotificationResult()
        channel = prefs["default_channel"]

        if channel in {"both", "slack"} and connection["slack_webhook_url_encrypted"]:
            try:
                webhook = self.secret_box.decrypt(connection["slack_webhook_url_encrypted"])
                assert webhook is not None
                self.slack.send(webhook, text)
                result.slack_sent = True
            except Exception as exc:
                result.errors.append(f"slack:{exc}")

        if channel in {"both", "email"} and connection["notification_email_verified"] and connection["notification_email"]:
            try:
                self.email.send(connection["notification_email"], "Nocturne 점검 결과", text)
                result.email_sent = True
            except Exception as exc:
                result.errors.append(f"email:{exc}")

        return result

    def _message(self, run: dict[str, object], inbox_url: str | None) -> str:
        proposal_count = int(run.get("proposal_count") or 0)
        held_count = int(run.get("held_count") or 0)
        if proposal_count == 0 and held_count == 0:
            headline = "오늘은 문제 없음"
        else:
            headline = f"오늘 발견된 제안 {proposal_count}건"
        lines = [
            f"Nocturne · {headline}",
            f"- run_id: {run.get('run_id')}",
            f"- 점검 페이지: {run.get('scanned_page_count')} / 변경 페이지: {run.get('changed_page_count')}",
            f"- 오류/누락/모순: {run.get('error_count')}/{run.get('omission_count')}/{run.get('contradiction_count')}",
            f"- 보류: {run.get('held_count')}",
            f"- 승인 반영: {run.get('applied_count')} / 실패: {run.get('apply_failed_count')}",
        ]
        if inbox_url:
            lines.append(f"- 수정함: {inbox_url}")
        return "\n".join(lines)
