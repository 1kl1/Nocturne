from __future__ import annotations

import json
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
            openrouter_key = self.settings.openrouter_api_key
            has_channel = bool(connection["notification_email_verified"] and connection["notification_email"])
            if not notion_token:
                raise RuntimeError("먼저 Notion을 연결해야 합니다.")
            if not openrouter_key:
                raise RuntimeError("서버 OPENROUTER_API_KEY가 설정되어 있지 않습니다.")
            if not has_channel:
                raise RuntimeError("이메일 알림 채널을 먼저 연결해야 합니다.")
            if not self.db.active_targets(user_id):
                raise RuntimeError("점검 대상을 먼저 추가해야 합니다.")
            agent_settings = self.db.agent_settings_for_user(user_id)

            self._log_tool_call(user_id, run_id, "approval.apply_approved", "start")
            apply_result = self.applier.apply_approved(user_id, run_id)
            self._log_tool_call(
                user_id,
                run_id,
                "approval.apply_approved",
                "success",
                {"applied": apply_result.applied, "failed": apply_result.failed},
            )
            counts["applied_count"] = apply_result.applied
            counts["apply_failed_count"] = apply_result.failed

            self._log_tool_call(user_id, run_id, "notion.expand_targets", "start")
            pages = self.notion.expand_targets(user_id)
            if not pages:
                finished_at = utc_now_iso()
                msg = "점검할 페이지를 찾지 못했습니다. Notion에서 페이지가 삭제되었거나 접근 권한이 해제되었을 수 있습니다."
                self.db.update_run(run_id, status="partial_success", finished_at=finished_at, error_message=msg, **counts)
                self.db.log("run_finished", user_id=user_id, run_id=run_id, payload={"status": "partial_success", "error": msg, **counts})
                return run_id
            self._log_tool_call(user_id, run_id, "notion.expand_targets", "success", {"pages": len(pages)})
            counts["scanned_page_count"] = len(pages)
            self._log_tool_call(user_id, run_id, "agent.filter_recent", "start", {"last_success": last_success})
            retry_page_ids = self._failed_page_ids_from_previous_run(user_id, run_id)
            changed_pages = [
                page
                for page in pages
                if page.page_id in retry_page_ids
                or self._is_changed_since_last_success(user_id, page.page_id, page.last_edited_time, last_success)
            ]
            self._log_tool_call(
                user_id,
                run_id,
                "agent.filter_recent",
                "success",
                {"changed_pages": len(changed_pages), "retry_pages": len(retry_page_ids)},
            )
            counts["changed_page_count"] = len(changed_pages)
            page_lookup = {page.page_id: page for page in pages}

            self._log_tool_call(user_id, run_id, "notion.ensure_inbox_database", "start")
            inbox_id, inbox_url = self.notion.ensure_inbox_database(user_id, changed_pages[0].page_id if changed_pages else pages[0].page_id)
            if not inbox_id:
                raise RuntimeError("Nocturne 데이터베이스를 만들지 못했습니다.")
            self._log_tool_call(user_id, run_id, "notion.ensure_inbox_database", "success", {"database_id": inbox_id})

            for page in changed_pages:
                try:
                    self._log_tool_call(user_id, run_id, "notion.fetch_text_blocks", "start", {"page_id": page.page_id, "title": page.title})
                    blocks = self.notion.fetch_text_blocks(user_id, page)
                    self._log_tool_call(user_id, run_id, "notion.fetch_text_blocks", "success", {"page_id": page.page_id, "blocks": len(blocks)})
                    if not blocks:
                        continue
                    search_results = {}
                    if int(agent_settings["search_recent_trends"] or 0):
                        self._log_tool_call(user_id, run_id, "web_search.candidate_queries", "start", {"page_id": page.page_id})
                        queries = self.web_search.candidate_queries(blocks)
                        self._log_tool_call(user_id, run_id, "web_search.candidate_queries", "success", {"page_id": page.page_id, "queries": len(queries)})
                        self._log_tool_call(user_id, run_id, "web_search.search_many", "start", {"page_id": page.page_id, "queries": len(queries)})
                        search_results = self.web_search.search_many(queries)
                        self._log_tool_call(
                            user_id,
                            run_id,
                            "web_search.search_many",
                            "success",
                            {"page_id": page.page_id, "result_groups": len(search_results)},
                        )
                    self._log_tool_call(user_id, run_id, "openrouter.analyze_blocks", "start", {"page_id": page.page_id, "blocks": len(blocks)})
                    proposals = self.openrouter.analyze_blocks(openrouter_key, blocks, search_results, agent_settings)
                    self._log_tool_call(user_id, run_id, "openrouter.analyze_blocks", "success", {"page_id": page.page_id, "proposals": len(proposals)})
                    self._log_tool_call(user_id, run_id, "validator.validate", "start", {"page_id": page.page_id, "proposals": len(proposals)})
                    validation = self.validator.validate(user_id, blocks, proposals)
                    self._log_tool_call(
                        user_id,
                        run_id,
                        "validator.validate",
                        "success",
                        {
                            "page_id": page.page_id,
                            "accepted": len(validation.accepted),
                            "held": len(validation.held),
                            "rejected": len(validation.rejected),
                        },
                    )
                    self._log_tool_call(
                        user_id,
                        run_id,
                        "proposal_writer.write",
                        "start",
                        {"page_id": page.page_id, "proposals": len(validation.accepted) + len(validation.held)},
                    )
                    written = self.writer.write(user_id, inbox_id, run_id, page_lookup, validation.accepted + validation.held)
                    self._log_tool_call(user_id, run_id, "proposal_writer.write", "success", {"page_id": page.page_id, "written": written.written, "failed": written.failed})
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
                    self._log_tool_call(
                        user_id,
                        run_id,
                        "page.scan",
                        "error",
                        {"page_id": page.page_id, "title": page.title, "error": str(exc)},
                        level="warning",
                    )
                    self.db.log(
                        "page_scan_failed",
                        user_id=user_id,
                        run_id=run_id,
                        level="warning",
                        payload={"page_id": page.page_id, "title": page.title, "error": str(exc)},
                    )

            notification_payload = {"run_id": run_id, **counts}
            self._log_tool_call(user_id, run_id, "notification.send_run_summary", "start")
            notification_result = self.notifications.send_run_summary(user_id, notification_payload, inbox_url)
            self._log_tool_call(
                user_id,
                run_id,
                "notification.send_run_summary",
                "success" if not notification_result.errors else "error",
                {"status": notification_result.status_text, "errors": notification_result.errors[:5]},
                level="warning" if notification_result.errors else "info",
            )
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
        self._log_tool_call(user_id, None, "approval.apply_approved_manual", "start")
        result = self.applier.apply_approved(user_id)
        self._log_tool_call(user_id, None, "approval.apply_approved_manual", "success", {"applied": result.applied, "failed": result.failed})
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

    def _failed_page_ids_from_previous_run(self, user_id: int, current_run_id: str) -> set[str]:
        latest = self.db.row(
            """
            SELECT run_id FROM runs
            WHERE user_id = ?
              AND run_id != ?
              AND status IN ('success', 'partial_success', 'failed')
              AND finished_at IS NOT NULL
            ORDER BY finished_at DESC, created_at DESC
            LIMIT 1
            """,
            (user_id, current_run_id),
        )
        if not latest:
            return set()
        logs = self.db.rows(
            """
            SELECT payload FROM audit_logs
            WHERE user_id = ? AND run_id = ? AND event = 'page_scan_failed'
            """,
            (user_id, latest["run_id"]),
        )
        page_ids: set[str] = set()
        for log in logs:
            try:
                payload = json.loads(log["payload"] or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("page_id"):
                page_ids.add(str(payload["page_id"]))
        return page_ids

    def _mark_targets_checked(self, user_id: int, status: str) -> None:
        self.db.update(
            """
            UPDATE scan_targets
            SET last_checked_at = ?, last_result = ?, updated_at = ?
            WHERE user_id = ? AND active = 1
            """,
            (utc_now_iso(), status, utc_now_iso(), user_id),
        )

    def _log_tool_call(
        self,
        user_id: int,
        run_id: str | None,
        action: str,
        status: str,
        payload: dict[str, object] | None = None,
        *,
        level: str = "info",
    ) -> None:
        body = {"action": action, "status": status}
        if payload:
            body.update(payload)
        self.db.log("agent_tool_call", user_id=user_id, run_id=run_id, level=level, payload=body)
