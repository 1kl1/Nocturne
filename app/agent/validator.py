from __future__ import annotations

from app.agent.types import ProposalCandidate, TextBlock, ValidationResult
from app.db import Database
from app.security import stable_hash


ISSUE_TYPES = {"error", "omission", "contradiction"}
APPLY_MODES = {"replace", "append"}


class ProposalValidator:
    def __init__(self, db: Database, minimum_confidence: float = 0.55):
        self.db = db
        self.minimum_confidence = minimum_confidence

    def validate(self, user_id: int, blocks: list[TextBlock], proposals: list[ProposalCandidate]) -> ValidationResult:
        result = ValidationResult()
        block_map = {block.block_id: block for block in blocks}

        for proposal in proposals:
            reason = self._validate_one(user_id, block_map, proposal)
            if reason:
                result.rejected.append((proposal, reason))
                continue
            if proposal.confidence < self.minimum_confidence:
                proposal.status = "보류"
                result.held.append(proposal)
                continue
            if proposal.issue_type == "error" and not proposal.source_urls and not any(kw in proposal.rationale for kw in ("내부", "문서", "본문", "문맥", "맥락", "표준", "논리", "계산", "오류", "잘못", "오타", "설명")):
                proposal.status = "보류"
                result.held.append(proposal)
                continue
            proposal.status = "대기"
            result.accepted.append(proposal)

        return result

    def _validate_one(self, user_id: int, block_map: dict[str, TextBlock], proposal: ProposalCandidate) -> str | None:
        proposal.issue_type = _normalize(proposal.issue_type, {"오류": "error", "누락": "omission", "모순": "contradiction"})
        proposal.apply_mode = _normalize(proposal.apply_mode, {"교체": "replace", "추가": "append"})

        if proposal.issue_type not in ISSUE_TYPES:
            return "지원하지 않는 문제 유형"
        if proposal.apply_mode not in APPLY_MODES:
            return "지원하지 않는 반영 방식"
        if not proposal.source_page_id or not proposal.block_id:
            return "원본 페이지 ID 또는 블록 ID 누락"
        if not proposal.suggested_sentence:
            return "제안 문장 누락"
        block = block_map.get(proposal.block_id)
        if block is None:
            return "block_id가 이번 페이지 블록에 없음"
        if block.page_id != proposal.source_page_id:
            return "source_page_id와 block_id의 페이지가 서로 다름"
        if proposal.original_sentence and proposal.original_sentence not in block.plain_text:
            return "원문 문장이 현재 블록에 없음"
        if proposal.apply_mode == "replace" and not proposal.original_sentence:
            return "교체 제안에 원문 문장이 없음"
        if proposal.apply_mode == "append" and proposal.original_sentence and proposal.original_sentence not in block.plain_text:
            return "추가 위치 앵커 문장이 현재 블록에 없음"
        if proposal.confidence < 0 or proposal.confidence > 1:
            return "확신도가 0~1 범위를 벗어남"
        if proposal.apply_mode == "replace" and len(proposal.suggested_sentence) > max(160, len(proposal.original_sentence) * 4):
            return "제안 문장이 원문보다 지나치게 김"
        original_hash = stable_hash(proposal.original_sentence)
        suggested_hash = stable_hash(proposal.suggested_sentence)
        if self.db.proposal_exists(user_id, proposal.source_page_id, proposal.block_id, original_hash, suggested_hash):
            return "중복 제안"
        return None


def _normalize(value: str, aliases: dict[str, str]) -> str:
    value = value.strip()
    return aliases.get(value, value)
