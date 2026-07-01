from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agent.types import ProposalCandidate, TextBlock
from app.agent.validator import ProposalValidator
from app.config import Settings
from app.db import Database
from app.security import SecretBox, stable_hash
from app.services.notion_service import replace_rich_text_sentence
from app.time_utils import utc_now_iso


def settings_for(path: Path) -> Settings:
    return Settings(
        app_url="http://localhost:8000",
        database_url=f"sqlite:///{path}",
        encryption_key="test-secret",
        notion_client_id="",
        notion_client_secret="",
        notion_redirect_uri="",
        openrouter_default_model="openai/gpt-4.1-mini",
        openrouter_app_name="Nocturne",
        openrouter_app_url="http://localhost:8000",
        skip_external_validation=True,
        email_provider="console",
        email_from="nocturne@example.com",
        email_api_key="",
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


if __name__ == "__main__":
    unittest.main()
