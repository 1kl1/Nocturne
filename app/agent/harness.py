from __future__ import annotations

import uuid
from datetime import timedelta

from app.agent.approval_applier import ApprovalApplier
from app.agent.planner import Planner
from app.agent.proposal_writer import ProposalWriter
from app.agent.validator import ProposalValidator
from app.config import Settings
from app.db import Database
from app.security import SecretBox
from app.services.notification_service import NotificationService
from app.services.notion_service import NotionService
from app.services.openrouter_service import OpenRouterService
from app.services.web_search_service import WebSearchService
from app.time_utils import parse_iso, utc_now_iso


class AgentHarness:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        secret_box: SecretBox,
        notion: NotionService,
        openrouter: OpenRouterService,
        web_search: WebSearchService,
        notifications: NotificationService,
    ):
        self.settings = settings
        self.db = db
        self.secret_box = secret_box
        self.notion = notion
        self.openrouter = openrouter
        self.web_search = web_search
        self.notifications = notifications
        self.planner = Planner()
        self.validator = ProposalValidator(db)
        self.writer = ProposalWriter(db, notion)
        self.applier = ApprovalApplier(db, notion)

    def run_for_user(self, user_id: int, *, manual: bool = False) -> str:
        run_id = f"run_{utc_now_iso().replace(':', '').replace('+', 'Z')}_{uuid.uuid4().hex[:8]}"
        last_success = self.db.last_successful_scan_at(user_id)
        self.db.create_run(user_id, run_id, last_success)
        self.db.update_run(run_id, status="running", started_at=utc_now_iso())
        self.db.log(
            "run_started",
            user_id=user_id,
            run_id=run_id,
            payload={"manual": manual, "plan": [step.name for step in self.planner.build_nightly_plan()]},
        )

        counts: dict[str, int | str | None] = {
            "scanned_page_count": 0,
            "changed_page_count": 0,
            "proposal_count": 0,
            "error_count": 0,
            "omission_count": 0,
            "contradiction_count": 0,
            "held_count": 0,
            "applied_count": 0,
            "apply_failed_count": 0,
        }
        page_errors: list[str] = []
        notification_errors: list[str] = []

        try:
            connection = self.db.connection_for_user(user_id)
            notion_token = self.secret_box.decrypt(connection["notion_access_token_encrypted"])
            openrouter_key = self.secret_box.decrypt(connection["openrouter_api_key_encrypted"])
            has_channel = bool(connection["slack_webhook_url_encrypted"]) or bool(connection["notification_email_verified"])
            if not notion_token:
                raise RuntimeError("먼저 Notion을 연결해야 합니다.")
            if not openrouter_key:
                raise RuntimeError("OpenRouter API 키를 먼저 등록해야 합니다.")
            if not has_channel:
                raise RuntimeError("Slack 또는 이메일 알림 채널이 하나 이상 필요합니다.")
            if not self.db.active_targets(user_id):
                raise RuntimeError("점검 대상을 먼저 추가해야 합니다.")

            apply_result = self.applier.apply_approved(user_id, run_id)
            counts["applied_count"] = apply_result.applied
            counts["apply_failed_count"] = apply_result.failed

            pages = self.notion.expand_targets(user_id)
            if not pages:
                raise RuntimeError("점검할 페이지를 찾지 못했습니다.")
            counts["scanned_page_count"] = len(pages)
            changed_pages = [page for page in pages if self._is_changed_since_last_success(user_id, page.page_id, page.last_edited_time, last_success)]
            counts["changed_page_count"] = len(changed_pages)
            page_lookup = {page.page_id: page for page in pages}

            inbox_id, inbox_url = self.notion.ensure_inbox_database(user_id, changed_pages[0].page_id if changed_pages else pages[0].page_id)
            if not inbox_id:
                raise RuntimeError("Nocturne 수정함 데이터베이스를 만들지 못했습니다.")

            for page in changed_pages:
                try:
                    blocks = self.notion.fetch_text_blocks(user_id, page)
                    if not blocks:
                        continue
                    queries = self.web_search.candidate_queries(blocks)
                    search_results = self.web_search.search_many(queries)
                    proposals = self.openrouter.analyze_blocks(openrouter_key, blocks, search_results)
                    validation = self.validator.validate(user_id, blocks, proposals)
                    written = self.writer.write(user_id, inbox_id, run_id, page_lookup, validation.accepted + validation.held)
                    counts["proposal_count"] = int(counts["proposal_count"] or 0) + len(validation.accepted)
                    counts["held_count"] = int(counts["held_count"] or 0) + len(validation.held)
                    counts["error_count"] = int(counts["error_count"] or 0) + sum(1 for item in validation.accepted if item.issue_type == "error")
                    counts["omission_count"] = int(counts["omission_count"] or 0) + sum(1 for item in validation.accepted if item.issue_type == "omission")
                    counts["contradiction_count"] = int(counts["contradiction_count"] or 0) + sum(1 for item in validation.accepted if item.issue_type == "contradiction")
                    if validation.rejected:
                        self.db.log(
                            "proposal_rejected",
                            user_id=user_id,
                            run_id=run_id,
                            payload=[
                                {"block_id": candidate.block_id, "reason": reason}
                                for candidate, reason in validation.rejected[:20]
                            ],
                        )
                    if written.failed:
                        page_errors.append(f"{page.title}: 제안 저장 실패 {written.failed}건")
                except Exception as exc:
                    page_errors.append(f"{page.title}: {exc}")
                    self.db.log(
                        "page_scan_failed",
                        user_id=user_id,
                        run_id=run_id,
                        level="warning",
                        payload={"page_id": page.page_id, "title": page.title, "error": str(exc)},
                    )

            notification_payload = {"run_id": run_id, **counts}
            notification_result = self.notifications.send_run_summary(user_id, notification_payload, inbox_url)
            notification_errors.extend(notification_result.errors)
            counts["notification_status"] = notification_result.status_text

            status = "success"
            if page_errors or notification_errors or int(counts["apply_failed_count"] or 0) > 0:
                status = "partial_success"
            finished_at = utc_now_iso()
            self.db.update_run(run_id, status=status, finished_at=finished_at, error_message="\n".join(page_errors + notification_errors) or None, **counts)
            self.db.update("UPDATE users SET last_successful_scan_at = ? WHERE id = ?", (finished_at, user_id))
            self._mark_targets_checked(user_id, status)
            self.db.log("run_finished", user_id=user_id, run_id=run_id, payload={"status": status, **counts})
            return run_id
        except Exception as exc:
            finished_at = utc_now_iso()
            self.db.update_run(run_id, status="failed", finished_at=finished_at, error_message=str(exc), **counts)
            self.db.log("run_failed", user_id=user_id, run_id=run_id, level="error", payload={"error": str(exc)})
            return run_id

    def apply_approved_for_user(self, user_id: int) -> tuple[int, int]:
        result = self.applier.apply_approved(user_id)
        return result.applied, result.failed

    def _is_changed_since_last_success(
        self,
        user_id: int,
        page_id: str,
        last_edited_time: str | None,
        last_success: str | None,
    ) -> bool:
        if not last_success:
            return True
        edited = parse_iso(last_edited_time)
        last = parse_iso(last_success)
        if not edited or not last or edited <= last:
            return False
        edit_row = self.db.row(
            """
            SELECT applied_at FROM nocturne_edits
            WHERE user_id = ? AND source_page_id = ?
            ORDER BY applied_at DESC LIMIT 1
            """,
            (user_id, page_id),
        )
        if not edit_row:
            return True
        applied = parse_iso(edit_row["applied_at"])
        if applied and applied > last and edited <= applied + timedelta(minutes=5):
            return False
        return True

    def _mark_targets_checked(self, user_id: int, status: str) -> None:
        self.db.update(
            """
            UPDATE scan_targets
            SET last_checked_at = ?, last_result = ?, updated_at = ?
            WHERE user_id = ? AND active = 1
            """,
            (utc_now_iso(), status, utc_now_iso(), user_id),
        )
