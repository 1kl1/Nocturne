from __future__ import annotations

from app.services.http_client import ExternalAPIError, request_json


class SlackService:
    def test_webhook(self, webhook_url: str) -> tuple[bool, str | None]:
        try:
            self.send(webhook_url, "Nocturne Slack 알림이 연결되었습니다.")
            return True, None
        except ExternalAPIError as exc:
            return False, exc.body or str(exc)

    def send(self, webhook_url: str, text: str) -> None:
        request_json("POST", webhook_url, payload={"text": text}, retries=1, timeout=20)
