from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import ui
from app.agent.harness import AgentHarness
from app.agent.proposal_writer import ProposalWriter
from app.agent.types import ProposalCandidate, TextBlock
from app.agent.validator import ProposalValidator
from app.config import Settings
from app.db import Database
from app.routes import web
from app.scheduler.scheduler import SchedulerLoop
from app.security import SecretBox, stable_hash
from app.services.notion_service import NotionService, replace_rich_text_sentence
from app.services.openrouter_service import OpenRouterService
from app.time_utils import local_hhmm, utc_now_iso


def settings_for(path: Path) -> Settings:
    return Settings(
        app_url="http://localhost:8000",
        database_url=f"sqlite:///{path}",
        encryption_key="test-secret",
        notion_client_id="",
        notion_client_secret="",
        notion_redirect_uri="",
        openrouter_api_key="test-openrouter-key",
        openrouter_default_model="deepseek/deepseek-v4-flash",
        openrouter_app_name="Nocturne",
        openrouter_app_url="http://localhost:8000",
        openrouter_web_search_enabled=False,
        openrouter_web_search_engine="",
        openrouter_web_search_max_results=5,
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

    def test_database_creates_knowledge_graph_cache_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(settings_for(Path(tmp) / "nocturne.sqlite3"))
            db.initialize()
            tables = {
                row["name"]
                for row in db.rows(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            }
            proposal_columns = {row["name"] for row in db.rows("PRAGMA table_info(proposals_cache)")}

            self.assertIn("knowledge_graph_nodes", tables)
            self.assertIn("knowledge_graph_edges", tables)
            self.assertIn("knowledge_graph_syncs", tables)
            self.assertIn("agent_settings", tables)
            self.assertIn("suggested_sentence", proposal_columns)
            self.assertIn("source_urls", proposal_columns)

    def test_database_uses_notion_workspace_as_user_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(settings_for(Path(tmp) / "nocturne.sqlite3"))
            db.initialize()

            first = db.user_for_notion_oauth(
                {
                    "workspace_id": "workspace-a",
                    "workspace_name": "Workspace A",
                    "bot_id": "bot-a",
                    "owner": {"user": {"person": {"email": "a@example.com"}}},
                }
            )
            same_workspace = db.user_for_notion_oauth(
                {
                    "workspace_id": "workspace-a",
                    "workspace_name": "Workspace A",
                    "bot_id": "bot-a-reinstalled",
                }
            )
            second = db.user_for_notion_oauth(
                {
                    "workspace_id": "workspace-b",
                    "workspace_name": "Workspace B",
                    "bot_id": "bot-b",
                }
            )

            self.assertEqual(first["id"], same_workspace["id"])
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(first["email"], "a@example.com")

    def test_session_token_round_trips_and_rejects_tampering(self) -> None:
        token = web._make_session_token(42, "test-secret")

        payload = web._read_session_token(token, "test-secret")

        self.assertIsNotNone(payload)
        self.assertEqual(payload["user_id"], 42)
        self.assertIsNone(web._read_session_token(f"{token}x", "test-secret"))
        self.assertIsNone(web._read_session_token(token, "other-secret"))

    def test_current_user_reads_signed_browser_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = settings_for(Path(tmp) / "nocturne.sqlite3")
            db = Database(settings)
            db.initialize()
            user = db.user_for_notion_oauth({"workspace_id": "workspace-a", "bot_id": "bot-a"})
            token = web._make_session_token(user["id"], settings.encryption_key)
            request = SimpleNamespace(
                cookies={web.SESSION_COOKIE: token},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings, db=db)),
            )

            current = web._current_user(request)

            self.assertIsNotNone(current)
            self.assertEqual(current["id"], user["id"])
            request.cookies = {}
            self.assertIsNone(web._current_user(request))

    def test_intro_start_button_opens_tutorial_before_oauth(self) -> None:
        html = ui.intro_page()

        self.assertIn('href="/onboarding"', html)
        self.assertNotIn('href="/auth/notion/start', html)

    def test_anonymous_onboarding_shows_tutorial_oauth_button(self) -> None:
        request = SimpleNamespace(cookies={}, query_params={}, app=SimpleNamespace(state=SimpleNamespace(db=None)))

        html = web.onboarding(request)

        self.assertIn("먼저 Notion 작업실을 연결합니다.", html)
        self.assertIn('href="/auth/notion/start?next=/onboarding?step=1"', html)

    def test_anonymous_onboarding_later_steps_require_session(self) -> None:
        request = SimpleNamespace(cookies={}, query_params={"step": "1"}, app=SimpleNamespace(state=SimpleNamespace(db=None)))

        response = web.onboarding(request, step=1)

        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/?notice="))

    def test_completed_onboarding_redirects_to_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = settings_for(Path(tmp) / "nocturne.sqlite3")
            db = Database(settings)
            db.initialize()
            user = db.user_for_notion_oauth({"workspace_id": "workspace-a", "bot_id": "bot-a"})
            db.update(
                """
                UPDATE connections
                SET notion_access_token_encrypted = ?, notification_email = ?, notification_email_verified = 1
                WHERE user_id = ?
                """,
                ("encrypted-token", "me@example.com", user["id"]),
            )
            db.execute(
                """
                INSERT INTO scan_targets
                    (user_id, notion_object_id, notion_object_type, title, url, include_children, excluded_page_ids, active, created_at, updated_at)
                VALUES (?, 'page-1', 'page', 'Roadmap', '', 1, '[]', 1, ?, ?)
                """,
                (user["id"], utc_now_iso(), utc_now_iso()),
            )
            token = web._make_session_token(user["id"], settings.encryption_key)
            request = SimpleNamespace(
                cookies={web.SESSION_COOKIE: token},
                query_params={},
                app=SimpleNamespace(state=SimpleNamespace(settings=settings, db=db)),
            )

            response = web.onboarding(request)

            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/dashboard")

    def test_scheduler_only_runs_oauth_connected_users(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = settings_for(Path(tmp) / "nocturne.sqlite3")
            db = Database(settings)
            db.initialize()
            oauth_user = db.user_for_notion_oauth({"workspace_id": "workspace-a", "bot_id": "bot-a"})
            db.update(
                "UPDATE connections SET notion_access_token_encrypted = ? WHERE user_id = ?",
                ("encrypted-token", oauth_user["id"]),
            )
            db.update(
                "UPDATE notification_settings SET scan_time = ? WHERE user_id = ?",
                (local_hhmm("Asia/Seoul"), oauth_user["id"]),
            )
            calls: list[tuple[int, bool]] = []
            harness = SimpleNamespace(run_for_user=lambda user_id, manual=False: calls.append((user_id, manual)))

            asyncio.run(SchedulerLoop(db, harness)._tick())

            self.assertEqual(calls, [(oauth_user["id"], False)])

    def test_proposal_writer_cache_matches_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(settings_for(Path(tmp) / "nocturne.sqlite3"))
            db.initialize()
            user = db.default_user()
            proposal = ProposalCandidate(
                issue_type="error",
                source_page_id="page-1",
                block_id="block-1",
                original_sentence="Old sentence.",
                suggested_sentence="New sentence.",
                apply_mode="replace",
                rationale="Verified.",
                source_urls=["https://example.com"],
                confidence=0.87,
            )

            ProposalWriter(db, SimpleNamespace())._cache(user["id"], "run-1", "proposal-page-1", proposal)

            row = db.row("SELECT * FROM proposals_cache WHERE user_id = ? AND run_id = ?", (user["id"], "run-1"))
            self.assertIsNotNone(row)
            self.assertEqual(row["notion_proposal_page_id"], "proposal-page-1")
            self.assertEqual(row["suggested_sentence"], "New sentence.")

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

    def test_openrouter_web_search_plugin_is_added_when_enabled(self) -> None:
        settings = Settings(
            app_url="http://localhost:8000",
            database_url="sqlite:///./data/nocturne.sqlite3",
            encryption_key="test-secret",
            notion_client_id="",
            notion_client_secret="",
            notion_redirect_uri="",
            openrouter_api_key="test-openrouter-key",
            openrouter_default_model="deepseek/deepseek-v4-flash",
            openrouter_app_name="Nocturne",
            openrouter_app_url="http://localhost:8000",
            openrouter_web_search_enabled=True,
            openrouter_web_search_engine="parallel",
            openrouter_web_search_max_results=3,
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
        captured: dict[str, object] = {}

        def fake_request_json(*args: object, **kwargs: object) -> SimpleNamespace:
            captured["payload"] = kwargs["payload"]
            return SimpleNamespace(data={"choices": [{"message": {"content": "[]"}}]})

        with patch("app.services.openrouter_service.request_json", fake_request_json):
            proposals = OpenRouterService(settings).analyze_blocks("test-openrouter-key", [block], {})

        self.assertEqual(proposals, [])
        payload = captured["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["plugins"], [{"id": "web", "max_results": 3, "engine": "parallel"}])

    def test_openrouter_agent_settings_extend_prompt_and_live_search(self) -> None:
        settings = settings_for(Path("unused.sqlite3"))
        block = TextBlock(
            page_id="page-1",
            page_title="Page",
            page_url="https://notion.so/page-1",
            block_id="block-1",
            block_type="paragraph",
            plain_text="Use the old API.",
            rich_text=[],
            parent_block_id=None,
            heading_path=[],
            last_edited_time=utc_now_iso(),
        )
        captured: dict[str, object] = {}

        def fake_request_json(*args: object, **kwargs: object) -> SimpleNamespace:
            captured["payload"] = kwargs["payload"]
            return SimpleNamespace(data={"choices": [{"message": {"content": "[]"}}]})

        with patch("app.services.openrouter_service.request_json", fake_request_json):
            OpenRouterService(settings).analyze_blocks(
                "test-openrouter-key",
                [block],
                {},
                {
                    "additional_context": "시간복잡도와 최신 API 변경을 우선 확인해줘.",
                    "search_recent_trends": 1,
                    "openrouter_live_search": 1,
                    "strict_source_mode": 1,
                },
            )

        payload = captured["payload"]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["plugins"], [{"id": "web", "max_results": 5}])
        user_prompt = payload["messages"][1]["content"]
        self.assertIn("USER_AGENT_CONTEXT", user_prompt)
        self.assertIn("시간복잡도와 최신 API 변경", user_prompt)
        self.assertIn("최근 동향", user_prompt)

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
        self.assertIn("data-knowledge-graph", html)
        self.assertIn("knowledge-graph.js", html)
        self.assertIn("data-graph-empty-state", html)
        self.assertIn("Graph 만드는 중", html)
        self.assertIn("워크스페이스 기준", html)
        self.assertNotIn("캐시 대기", html)
        self.assertNotIn("data-graph-sync", html)
        self.assertNotIn("data-graph-fit", html)
        self.assertNotIn('value="/dashboard"', html)
        self.assertIn("run-board-row", html)
        self.assertIn("data-proposal-dialog", html)
        self.assertIn("home.js", html)
        self.assertIn("Roadmap", html)
        self.assertNotIn("proposal-page", html)
        self.assertNotIn("page-head home-head", html)
        self.assertNotIn("다음 실행", html)
        self.assertNotIn("보완 필요 페이지", html)
        self.assertNotIn("승인 항목 반영", html)

    def test_proposal_detail_payload_and_reject_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = settings_for(Path(tmp) / "nocturne.sqlite3")
            db = Database(settings)
            db.initialize()
            user = db.default_user()
            db.execute(
                """
                INSERT INTO knowledge_graph_nodes
                    (user_id, notion_object_id, object_type, title, url, first_seen_at, updated_at)
                VALUES (?, 'page-1', 'page', 'Readable Title', '', ?, ?)
                """,
                (user["id"], utc_now_iso(), utc_now_iso()),
            )
            proposal_id = db.execute(
                """
                INSERT INTO proposals_cache
                    (user_id, run_id, source_page_id, block_id, issue_type, apply_mode,
                     original_sentence_hash, suggested_sentence_hash, original_sentence, suggested_sentence,
                     rationale, source_urls, status, confidence, created_at)
                VALUES (?, 'run-1', 'page-1', 'block-1', 'error', 'replace',
                        'old', 'new', 'Old sentence.', 'New sentence.', 'Because.', '["https://example.com"]', '대기', 0.9, ?)
                """,
                (user["id"], utc_now_iso()),
            )

            detail = web._proposal_detail_payload(db, user["id"], proposal_id)

            self.assertEqual(detail["title"], "Readable Title")
            self.assertIn("## 추천 변경", detail["markdown"])
            self.assertEqual(detail["sourceUrls"], ["https://example.com"])

            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db, notion=SimpleNamespace())))
            message, status_code = web._reject_proposal_for_user(request, user["id"], proposal_id)

            self.assertEqual(status_code, 200)
            self.assertIn("거절", message)
            self.assertEqual(db.row("SELECT status FROM proposals_cache WHERE id = ?", (proposal_id,))["status"], "거절")

    def test_runs_page_limit_selector_and_agent_error_summary(self) -> None:
        run = {
            "run_id": "run_20260702_abcdef12",
            "status": "partial_success",
            "started_at": utc_now_iso(),
            "finished_at": utc_now_iso(),
            "scanned_page_count": 2,
            "changed_page_count": 1,
            "proposal_count": 0,
            "error_count": 0,
            "omission_count": 0,
            "contradiction_count": 0,
            "held_count": 0,
            "applied_count": 0,
            "apply_failed_count": 0,
            "agent_error_count": 1,
            "error_message": "Roadmap: OpenRouter 호출 실패",
            "notification_status": "sent",
        }

        html = ui.runs_page([run], [], timezone_name="Asia/Seoul", selected_limit=100)

        self.assertIn('name="limit"', html)
        self.assertIn('<option value="100" selected>최근 100개</option>', html)
        self.assertIn('/runs?limit=100', html)
        self.assertIn("data-runs-live", html)
        self.assertIn("data-runs-refresh", html)
        self.assertIn("data-runs-limit", html)
        self.assertIn("data-run-key", html)
        self.assertIn("data-run-error-dialog", html)
        self.assertIn("data-run-error-run-id", html)
        self.assertIn("runs.js", html)
        self.assertNotIn(">보기</button>", html)
        self.assertIn("0건", html)
        self.assertIn("점검 오류 1건", html)
        self.assertNotIn("실패 없음", html)

    def test_failed_pages_from_previous_run_are_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = settings_for(Path(tmp) / "nocturne.sqlite3")
            db = Database(settings)
            db.initialize()
            user = db.default_user()
            db.create_run(user["id"], "run_previous", None)
            db.update_run("run_previous", status="partial_success", started_at=utc_now_iso(), finished_at=utc_now_iso())
            db.log(
                "page_scan_failed",
                user_id=user["id"],
                run_id="run_previous",
                level="warning",
                payload={"page_id": "page-failed", "title": "Broken", "error": "timeout"},
            )
            db.create_run(user["id"], "run_current", db.last_successful_scan_at(user["id"]))

            harness = AgentHarness(
                settings,
                db,
                SecretBox("unit-test-secret"),
                SimpleNamespace(),
                SimpleNamespace(),
                SimpleNamespace(),
                SimpleNamespace(),
            )

            self.assertEqual(harness._failed_page_ids_from_previous_run(user["id"], "run_current"), {"page-failed"})

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
        self.assertIn('action="/logout"', page_html)
        self.assertIn("data-target-exclusions", page_html)
        self.assertNotIn("제외 페이지 ID", page_html)
        self.assertNotIn("알림 설정", page_html)
        self.assertNotIn("계정/API", page_html)
        self.assertNotIn("OpenRouter", page_html)

        notification_html = ui.settings_page(settings, connection, [], selected_section="notifications")
        self.assertIn("알림 설정", notification_html)
        self.assertNotIn("페이지 설정", notification_html)
        self.assertNotIn("target-picker", notification_html)
        self.assertNotIn("계정/API", notification_html)

        agent_html = ui.settings_page(
            settings,
            connection,
            [],
            {
                "additional_context": "문서 톤을 유지해줘.",
                "search_recent_trends": 1,
                "openrouter_live_search": 0,
                "strict_source_mode": 1,
            },
            selected_section="agent",
        )
        self.assertIn("Agent 설정", agent_html)
        self.assertIn("문서 톤을 유지해줘.", agent_html)
        self.assertIn("최근 동향", agent_html)
        self.assertIn('name="search_recent_trends"', agent_html)
        self.assertNotIn("페이지 설정", agent_html)

    def test_inbox_database_404_renewal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.sqlite3"
            settings = settings_for(db_path)
            db = Database(settings)
            db.initialize()
            service = NotionService(settings, db, SecretBox("unit-test-secret"))
            user = db.user_for_notion_oauth({"workspace_id": "workspace-a", "bot_id": "bot-id"})
            service.save_oauth_connection(user["id"], {"access_token": "token", "workspace_name": "workspace", "bot_id": "bot-id"})
            db.update(
                "UPDATE connections SET notion_inbox_database_id = ?, notion_inbox_url = ? WHERE user_id = ?",
                ("deleted-db-id", "http://notion.so/deleted", user["id"]),
            )

            calls: list[dict[str, Any]] = []

            def fake_request(user_id: int, method: str, path: str, payload: Any = None, retries: int = 2) -> Any:
                calls.append({"method": method, "path": path, "payload": payload})
                if path == "/databases/deleted-db-id" or path == "/databases/deleted-db-id/query":
                    from app.services.notion_service import NotionError
                    raise NotionError('Notion API 오류: 404 {"object":"error","status":404,"code":"object_not_found","message":"Could not find database"}')
                if method == "POST" and path == "/databases":
                    return {"id": "new-db-id", "url": "http://notion.so/new-db"}
                return {}

            service._request = fake_request  # type: ignore[method-assign]

            proposals = service.query_approved_proposals(user["id"])
            self.assertEqual(proposals, [])
            conn = db.connection_for_user(user["id"])
            self.assertIsNone(conn["notion_inbox_database_id"])

            new_id, new_url = service.ensure_inbox_database(user["id"], "parent-page-id")
            self.assertEqual(new_id, "new-db-id")
            self.assertEqual(new_url, "http://notion.so/new-db")


if __name__ == "__main__":
    unittest.main()
