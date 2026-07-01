from __future__ import annotations

from dataclasses import dataclass

from app.agent.types import PageCandidate, ProposalCandidate
from app.db import Database
from app.security import stable_hash
from app.services.notion_service import NotionService
from app.time_utils import utc_now_iso


@dataclass
class WriteResult:
    written: int = 0
    failed: int = 0


class ProposalWriter:
    def __init__(self, db: Database, notion: NotionService):
        self.db = db
        self.notion = notion

    def write(
        self,
        user_id: int,
        database_id: str,
        run_id: str,
        pages: dict[str, PageCandidate],
        proposals: list[ProposalCandidate],
    ) -> WriteResult:
        result = WriteResult()
        for proposal in proposals:
            page = pages.get(proposal.source_page_id)
            if page is None:
                result.failed += 1
                self.db.log(
                    "proposal_page_missing",
                    user_id=user_id,
                    run_id=run_id,
                    level="warning",
                    payload={"source_page_id": proposal.source_page_id, "block_id": proposal.block_id},
                )
                continue
            try:
                notion_page_id = self.notion.create_proposal_page(user_id, database_id, run_id, page, proposal)
                self._cache(user_id, run_id, notion_page_id, proposal)
                result.written += 1
            except Exception as exc:
                result.failed += 1
                self.db.log(
                    "proposal_write_failed",
                    user_id=user_id,
                    run_id=run_id,
                    level="warning",
                    payload={"block_id": proposal.block_id, "error": str(exc)},
                )
        return result

    def _cache(self, user_id: int, run_id: str, notion_page_id: str, proposal: ProposalCandidate) -> None:
        self.db.execute(
            """
            INSERT OR IGNORE INTO proposals_cache
                (user_id, run_id, notion_proposal_page_id, source_page_id, block_id, issue_type,
                 apply_mode, original_sentence_hash, suggested_sentence_hash, status, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                run_id,
                notion_page_id,
                proposal.source_page_id,
                proposal.block_id,
                proposal.issue_type,
                proposal.apply_mode,
                stable_hash(proposal.original_sentence),
                stable_hash(proposal.suggested_sentence),
                proposal.status,
                proposal.confidence,
                utc_now_iso(),
            ),
        )
