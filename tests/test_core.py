from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import ui
from app.agent.types import ProposalCandidate, TextBlock
from app.agent.validator import ProposalValidator
from app.config import Settings
from app.db import Database
from app.security import SecretBox, stable_hash
from app.services.notion_service import NotionService, replace_rich_text_sentence
from app.time_utils import utc_now_iso


def settings_for(path: Path) -> Settings:
    return Settings(
        app_url="http://localhost:8000",
        database_url=f"sqlite:///{path}",
        encryption_key="test-secret",
        notion_client_id="",
        notion_client_secret="",
        notion_redirect_uri="",
        openrouter_api_key="test-openrouter-key",
        openrouter_default_model="openai/gpt-4.1-mini",
        openrouter_app_name="Nocturne",
        openrouter_app_url="http://localhost:8000",
        skip_external_validation=True,
        email_provider="console",
        email_from="nocturne@example.com",
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        web_search_provider="none",
        web_search_api_key="",
        scheduler_enabled=False,
        default_user_email="test@example.com",
    )


class CoreTest(unittest.TestCase):
    def test_secret_box_round_trips_without_plaintext(self) -> None:
        box = SecretBox("unit-test-secret")
        encrypted = box.encrypt("sk-or-secret-value")
        self.assertIsNotNone(encrypted)
        self.assertNotIn("sk-or-secret-value", encrypted or "")
        self.assertEqual(box.decrypt(encrypted), "sk-or-secret-value")

    def test_validator_accepts_precise_replace_and_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(settings_for(Path(tmp) / "nocturne.sqlite3"))
            db.initialize()
            user = db.default_user()
            block = TextBlock(
                page_id="page-1",
                page_title="Page",
                page_url="https://notion.so/page-1",
                block_id="block-1",
                block_type="paragraph",
                plain_text="The product launched in 2024.",
                rich_text=[],
                parent_block_id=None,
                heading_path=[],
                last_edited_time=utc_now_iso(),
            )
            proposal = ProposalCandidate(
                issue_type="error",
                source_page_id="page-1",
                block_id="block-1",
                original_sentence="The product launched in 2024.",
                suggested_sentence="The product launched in 2025.",
                apply_mode="replace",
                rationale="Verified against source URL.",
                source_urls=["https://example.com"],
                confidence=0.91,
            )
            result = ProposalValidator(db).validate(user["id"], [block], [proposal])
            self.assertEqual(len(result.accepted), 1)

            db.execute(
                """
                INSERT INTO proposals_cache
                    (user_id, run_id, source_page_id, block_id, issue_type, apply_mode,
                     original_sentence_hash, suggested_sentence_hash, status, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["id"],
                    "run-1",
                    "page-1",
                    "block-1",
                    "error",
                    "replace",
                    stable_hash(proposal.original_sentence),
                    stable_hash(proposal.suggested_sentence),
                    "대기",
                    0.91,
                    utc_now_iso(),
                ),
            )
            duplicate = ProposalValidator(db).validate(user["id"], [block], [proposal])
            self.assertEqual(duplicate.rejected[0][1], "중복 제안")

    def test_replace_rich_text_sentence_preserves_surrounding_text(self) -> None:
        rich_text = [
            {
                "type": "text",
                "plain_text": "Alpha ",
                "text": {"content": "Alpha "},
                "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"},
            },
            {
                "type": "text",
                "plain_text": "old sentence",
                "text": {"content": "old sentence"},
                "annotations": {"bold": True, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"},
            },
            {
                "type": "text",
                "plain_text": " Omega",
                "text": {"content": " Omega"},
                "annotations": {"bold": False, "italic": False, "strikethrough": False, "underline": False, "code": False, "color": "default"},
            },
        ]
        updated = replace_rich_text_sentence(rich_text, "old sentence", "new sentence")
        plain = "".join(part["text"]["content"] for part in updated)
        self.assertEqual(plain, "Alpha new sentence Omega")
        self.assertEqual(updated[1]["annotations"]["bold"], True)

    def test_notion_search_selectable_objects_formats_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(settings_for(Path(tmp) / "nocturne.sqlite3"))
            db.initialize()
            user = db.default_user()
            service = NotionService(settings_for(Path(tmp) / "nocturne.sqlite3"), db, SecretBox("unit-test-secret"))
            calls = []

            def fake_request(user_id: int, method: str, path: str, payload: object = None, retries: int = 2) -> dict:
                calls.append({"user_id": user_id, "method": method, "path": path, "payload": payload})
                if path.startswith("/blocks/page-1/children"):
                    return {
                        "results": [
                            {
                                "type": "child_page",
                                "id": "child-page",
                                "has_children": False,
                                "last_edited_time": "2026-07-02T00:00:00.000Z",
                                "child_page": {"title": "Child Page"},
                            },
                            {
                                "type": "child_database",
                                "id": "child-db",
                                "has_children": True,
                                "last_edited_time": "2026-07-02T00:00:00.000Z",
                                "child_database": {"title": "Child DB"},
                            },
                        ],
                        "has_more": False,
                    }
                if path == "/databases/db-1/query":
                    return {
                        "results": [
                            {
                                "object": "page",
                                "id": "db-page",
                                "url": "https://notion.so/db-page",
                                "last_edited_time": "2026-07-02T00:00:00.000Z",
                                "properties": {
                                    "Name": {
                                        "type": "title",
                                        "title": [{"plain_text": "DB Page"}],
                                    }
                                },
                            }
                        ],
                        "has_more": False,
                    }
                return {
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "url": "https://notion.so/page-1",
                            "last_edited_time": "2026-07-01T00:00:00.000Z",
                            "parent": {"type": "page_id", "page_id": "parent-page"},
                            "properties": {
                                "Name": {
                                    "type": "title",
                                    "title": [{"plain_text": "Roadmap"}],
                                }
                            },
                        },
                        {
                            "object": "database",
                            "id": "db-1",
                            "url": "https://notion.so/db-1",
                            "last_edited_time": "2026-06-30T00:00:00.000Z",
                            "parent": {"type": "workspace", "workspace": True},
                            "title": [{"plain_text": "Knowledge Base"}],
                        },
                        {"object": "block", "id": "block-1"},
                    ]
                }

            service._request = fake_request  # type: ignore[method-assign]
            all_results = service.search_selectable_objects(user["id"], query="Roadmap", limit=10)
            page_results = service.search_selectable_objects(user["id"], object_type="page", limit=80)
            root_results = service.search_selectable_objects(user["id"], limit=50, root_only=True)
            page_children = service.list_selectable_children(user["id"], "page-1", "page")
            database_children = service.list_selectable_children(user["id"], "db-1", "database")
            search_calls = [call for call in calls if call["path"] == "/search"]

            self.assertEqual(search_calls[0]["payload"]["query"], "Roadmap")
            self.assertNotIn("filter", search_calls[0]["payload"])
            self.assertEqual(all_results[0]["title"], "Roadmap")
            self.assertEqual(all_results[0]["parent_type"], "page")
            self.assertEqual(all_results[0]["parent_title"], "상위 페이지")
            self.assertEqual(all_results[1]["title"], "Knowledge Base")
            self.assertEqual(all_results[1]["parent_type"], "workspace")
            self.assertEqual(search_calls[1]["payload"]["page_size"], 80)
            self.assertEqual(search_calls[1]["payload"]["filter"], {"value": "page", "property": "object"})
            self.assertEqual([result["object_type"] for result in page_results], ["page"])
            self.assertEqual([result["object_id"] for result in root_results], ["db-1"])
            self.assertEqual([result["title"] for result in page_children], ["Child Page", "Child DB"])
            self.assertEqual(page_children[0]["has_children"], False)
            self.assertEqual(database_children[0]["title"], "DB Page")
            self.assertNotIn("/pages/parent-page", [call["path"] for call in calls])

    def test_onboarding_skips_scope_and_stages_email_flow(self) -> None:
        settings = {"scan_time": "02:00", "notify_time": "08:00", "timezone": "Asia/Seoul"}
        connection = {
            "notion_access_token_encrypted": "token",
            "notion_workspace_name": "My Life",
            "notion_workspace_id": "workspace-1",
            "notification_email": "",
            "notification_email_verified": 0,
        }
        target = {
            "title": "PS 문제풀이",
            "notion_object_type": "page",
            "include_children": 1,
        }

        html = ui.onboarding_page(connection, settings, [target], False, step=1)
        self.assertIn('data-start-step="2"', html)
        self.assertIn('data-skip-steps="1"', html)
        self.assertNotIn("setup tasks", html)
        self.assertNotIn("승인 경계 확인", html)
        self.assertNotIn("OpenRouter", html)
        self.assertIn('name="include_children" value="1"', html)
        self.assertNotIn("하위 페이지 포함", html)
        self.assertIn('name="email"', html)
        self.assertNotIn('name="code"', html)
        self.assertNotIn("SMTP", html)
        self.assertNotIn("시간 저장하고 계속", html)

        pending = {**connection, "notification_email": "me@example.com"}
        pending_html = ui.onboarding_page(pending, settings, [target], False, step=2)
        self.assertIn('name="code"', pending_html)
        self.assertNotIn("주소로 보낸 코드를 입력", pending_html)
        self.assertNotIn("시간 저장하고 계속", pending_html)

        verified = {**pending, "notification_email_verified": 1}
        verified_html = ui.onboarding_page(verified, settings, [target], False, step=2)
        self.assertIn("시간 저장하고 계속", verified_html)
        self.assertIn('name="notify_zero" value="1"', verified_html)
        self.assertNotIn("0건 알림", verified_html)
        self.assertNotIn('placeholder="000000"', verified_html)

    def test_dashboard_centers_recent_run_board(self) -> None:
        run = {
            "status": "success",
            "finished_at": utc_now_iso(),
            "started_at": utc_now_iso(),
            "created_at": utc_now_iso(),
            "proposal_count": 1,
            "applied_count": 0,
            "apply_failed_count": 0,
        }
        item = {
            "item_kind": "proposal",
            "source_page_id": "page-1",
            "source_title": "Roadmap",
            "issue_type": "error",
            "apply_mode": "replace",
            "status": "대기",
            "happened_at": utc_now_iso(),
            "notion_proposal_page_id": "proposal-page",
        }
        html = ui.dashboard(
            {},
            {},
            {"timezone": "Asia/Seoul"},
            [],
            [run],
            [item],
        )

        self.assertIn("1건", html)
        self.assertIn("0건", html)
        self.assertIn("run-board-row", html)
        self.assertIn("Roadmap", html)
        self.assertNotIn("다음 실행", html)
        self.assertNotIn("보완 필요 페이지", html)
        self.assertNotIn("승인 항목 반영", html)

    def test_settings_switches_one_section_and_nav_uses_icons(self) -> None:
        settings = {"scan_time": "02:00", "notify_time": "08:00", "timezone": "Asia/Seoul"}
        connection = {
            "notion_access_token_encrypted": "token",
            "notion_workspace_name": "My Life",
            "notion_workspace_id": "workspace-1",
            "notification_email": "me@example.com",
            "notification_email_verified": 1,
        }

        page_html = ui.settings_page(settings, connection, [], selected_section="pages")
        self.assertIn("icon-nav", page_html)
        self.assertIn("<svg", page_html)
        self.assertIn("페이지 설정", page_html)
        self.assertIn("로그아웃", page_html)
        self.assertNotIn("알림 설정", page_html)
        self.assertNotIn("계정/API", page_html)
        self.assertNotIn("OpenRouter", page_html)

        notification_html = ui.settings_page(settings, connection, [], selected_section="notifications")
        self.assertIn("알림 설정", notification_html)
        self.assertNotIn("페이지 설정", notification_html)
        self.assertNotIn("target-picker", notification_html)
        self.assertNotIn("계정/API", notification_html)


if __name__ == "__main__":
    unittest.main()
