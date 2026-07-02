from __future__ import annotations

from app.agent.types import ApplyResult
from app.db import Database
from app.security import stable_hash
from app.services.notion_service import NotionService
from app.time_utils import utc_now_iso


class ApprovalApplier:
    def __init__(self, db: Database, notion: NotionService):
        self.db = db
        self.notion = notion

    def apply_approved(self, user_id: int, run_id: str | None = None) -> ApplyResult:
        result = ApplyResult()
        for proposal in self.notion.query_approved_proposals(user_id):
            try:
                if proposal.apply_mode == "replace":
                    before, after = self.notion.replace_sentence_in_block(
                        user_id,
                        proposal.block_id,
                        proposal.original_sentence,
                        proposal.suggested_sentence,
                    )
                elif proposal.apply_mode == "append":
                    before, after = self.notion.append_sentence_after_block(user_id, proposal.block_id, proposal.suggested_sentence)
                else:
                    raise ValueError(f"지원하지 않는 반영 방식: {proposal.apply_mode}")

                self.notion.update_proposal_status(user_id, proposal.notion_page_id, "반영됨")
                self.db.execute(
                    """
                    INSERT INTO nocturne_edits
                        (user_id, source_page_id, block_id, proposal_id, applied_at, before_text_hash, after_text_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        proposal.source_page_id,
                        proposal.block_id,
                        proposal.notion_page_id,
                        utc_now_iso(),
                        stable_hash(before),
                        stable_hash(after),
                    ),
                )
                self.db.update(
                    "UPDATE proposals_cache SET status = '반영됨', updated_at = ? WHERE notion_proposal_page_id = ?",
                    (utc_now_iso(), proposal.notion_page_id),
                )
                result.applied += 1
            except Exception as exc:
                message = str(exc)
                result.failed += 1
                result.errors.append(f"{proposal.proposal_title}: {message}")
                try:
                    self.notion.update_proposal_status(user_id, proposal.notion_page_id, "반영 실패", message)
                finally:
                    self.db.update(
                        "UPDATE proposals_cache SET status = '반영 실패', updated_at = ? WHERE notion_proposal_page_id = ?",
                        (utc_now_iso(), proposal.notion_page_id),
                    )
                    self.db.log(
                        "approval_apply_failed",
                        user_id=user_id,
                        run_id=run_id,
                        level="warning",
                        payload={"proposal_id": proposal.notion_page_id, "error": message},
                    )
        return result
