from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PageCandidate:
    page_id: str
    title: str
    url: str
    last_edited_time: str | None
    source_target_id: int | None = None


@dataclass(frozen=True)
class TextBlock:
    page_id: str
    page_title: str
    page_url: str
    block_id: str
    block_type: str
    plain_text: str
    rich_text: list[dict[str, Any]]
    parent_block_id: str | None
    heading_path: list[str]
    last_edited_time: str | None


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    accessed_at: str


@dataclass
class ProposalCandidate:
    issue_type: str
    source_page_id: str
    block_id: str
    original_sentence: str
    suggested_sentence: str
    apply_mode: str
    rationale: str
    source_urls: list[str]
    confidence: float
    status: str = "대기"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ProposalCandidate":
        source_urls = data.get("source_urls") or data.get("source_url") or []
        if isinstance(source_urls, str):
            source_urls = [source_urls]
        return cls(
            issue_type=str(data.get("issue_type", "")).strip(),
            source_page_id=str(data.get("source_page_id", "")).strip(),
            block_id=str(data.get("block_id", "")).strip(),
            original_sentence=str(data.get("original_sentence", "")).strip(),
            suggested_sentence=str(data.get("suggested_sentence", "")).strip(),
            apply_mode=str(data.get("apply_mode", "")).strip(),
            rationale=str(data.get("rationale", "")).strip(),
            source_urls=[str(url).strip() for url in source_urls if str(url).strip()],
            confidence=float(data.get("confidence") or 0),
        )


@dataclass
class ValidationResult:
    accepted: list[ProposalCandidate] = field(default_factory=list)
    held: list[ProposalCandidate] = field(default_factory=list)
    rejected: list[tuple[ProposalCandidate, str]] = field(default_factory=list)


@dataclass
class ApplyResult:
    applied: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class NotificationResult:
    slack_sent: bool = False
    email_sent: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def status_text(self) -> str:
        delivered = []
        if self.slack_sent:
            delivered.append("slack")
        if self.email_sent:
            delivered.append("email")
        if self.errors and delivered:
            return f"partial:{','.join(delivered)}"
        if self.errors:
            return "failed"
        return ",".join(delivered) if delivered else "skipped"
