from __future__ import annotations

import base64
import copy
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any

from app.agent.types import PageCandidate, ProposalCandidate, TextBlock
from app.config import Settings
from app.db import Database
from app.security import SecretBox
from app.services.http_client import ExternalAPIError, request_json
from app.time_utils import utc_now_iso


NOTION_VERSION = "2022-06-28"

ISSUE_LABELS = {
    "error": "오류",
    "omission": "누락",
    "contradiction": "모순",
}

REVERSE_ISSUE_LABELS = {value: key for key, value in ISSUE_LABELS.items()}

APPLY_LABELS = {
    "replace": "교체",
    "append": "추가",
}

REVERSE_APPLY_LABELS = {value: key for key, value in APPLY_LABELS.items()}

TEXT_BLOCK_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
    "quote",
    "callout",
    "code",
}


class NotionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovedProposal:
    notion_page_id: str
    source_page_id: str
    source_page_url: str
    block_id: str
    original_sentence: str
    suggested_sentence: str
    apply_mode: str
    proposal_title: str


class NotionService:
    def __init__(self, settings: Settings, db: Database, secret_box: SecretBox):
        self.settings = settings
        self.db = db
        self.secret_box = secret_box

    def oauth_start_url(self, state: str) -> str:
        if not self.settings.notion_oauth_configured:
            raise NotionError("Notion OAuth 환경변수가 설정되어 있지 않습니다.")
        query = urllib.parse.urlencode(
            {
                "client_id": self.settings.notion_client_id,
                "response_type": "code",
                "owner": "user",
                "redirect_uri": self.settings.notion_redirect_uri,
                "state": state,
            }
        )
        return f"https://api.notion.com/v1/oauth/authorize?{query}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        credentials = f"{self.settings.notion_client_id}:{self.settings.notion_client_secret}"
        basic = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        response = request_json(
            "POST",
            "https://api.notion.com/v1/oauth/token",
            headers={"Authorization": f"Basic {basic}"},
            payload={"grant_type": "authorization_code", "code": code, "redirect_uri": self.settings.notion_redirect_uri},
            retries=1,
        )
        if not isinstance(response.data, dict) or "access_token" not in response.data:
            raise NotionError("Notion OAuth 응답에 access_token이 없습니다.")
        return response.data

    def save_oauth_connection(self, user_id: int, oauth_data: dict[str, Any]) -> None:
        access_token = str(oauth_data["access_token"])
        workspace_id = oauth_data.get("workspace_id")
        workspace_name = oauth_data.get("workspace_name")
        bot_id = oauth_data.get("bot_id")
        owner = oauth_data.get("owner")
        now = utc_now_iso()
        self.db.update(
            """
            UPDATE connections
            SET notion_access_token_encrypted = ?,
                notion_workspace_id = ?,
                notion_workspace_name = ?,
                notion_bot_id = ?,
                notion_owner_info = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                self.secret_box.encrypt(access_token),
                workspace_id,
                workspace_name,
                bot_id,
                json.dumps(owner, ensure_ascii=False, default=str) if owner else None,
                now,
                user_id,
            ),
        )

    def token_for_user(self, user_id: int) -> str | None:
        connection = self.db.connection_for_user(user_id)
        return self.secret_box.decrypt(connection["notion_access_token_encrypted"])

    def _headers(self, user_id: int) -> dict[str, str]:
        token = self.token_for_user(user_id)
        if not token:
            raise NotionError("먼저 Notion을 연결해야 합니다.")
        return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}

    def _request(self, user_id: int, method: str, path: str, payload: Any = None, retries: int = 2) -> Any:
        try:
            response = request_json(
                method,
                f"https://api.notion.com/v1{path}",
                headers=self._headers(user_id),
                payload=payload,
                retries=retries,
            )
        except ExternalAPIError as exc:
            raise NotionError(f"Notion API 오류: {exc.status or ''} {exc.body or exc}") from exc
        return response.data

    def retrieve_page(self, user_id: int, page_id: str) -> dict[str, Any]:
        return self._request(user_id, "GET", f"/pages/{page_id}")

    def retrieve_database(self, user_id: int, database_id: str) -> dict[str, Any]:
        return self._request(user_id, "GET", f"/databases/{database_id}")

    def list_block_children(self, user_id: int, block_id: str) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            cursor_query = f"&start_cursor={urllib.parse.quote(cursor)}" if cursor else ""
            data = self._request(user_id, "GET", f"/blocks/{block_id}/children?page_size=100{cursor_query}")
            children.extend(data.get("results", []))
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                return children

    def query_database_pages(self, user_id: int, database_id: str) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request(user_id, "POST", f"/databases/{database_id}/query", payload)
            pages.extend(data.get("results", []))
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                return pages

    def expand_targets(self, user_id: int) -> list[PageCandidate]:
        targets = self.db.active_targets(user_id)
        pages: dict[str, PageCandidate] = {}
        for target in targets:
            excluded = set(Database.decode_json_array(target["excluded_page_ids"]))
            try:
                if target["notion_object_type"] == "page":
                    self._add_page_with_children(user_id, target, target["notion_object_id"], excluded, pages)
                else:
                    for page in self.query_database_pages(user_id, target["notion_object_id"]):
                        page_id = page["id"]
                        if page_id in excluded:
                            continue
                        pages[page_id] = self._page_candidate(page, target["id"])
                        if target["include_children"]:
                            self._walk_child_pages(user_id, page_id, excluded, pages, target["id"])
            except Exception as exc:
                self.db.log(
                    "target_expand_failed",
                    user_id=user_id,
                    level="warning",
                    payload={"target_id": target["id"], "error": str(exc)},
                )
        return list(pages.values())

    def _add_page_with_children(
        self,
        user_id: int,
        target: Any,
        page_id: str,
        excluded: set[str],
        pages: dict[str, PageCandidate],
    ) -> None:
        if page_id in excluded:
            return
        page = self.retrieve_page(user_id, page_id)
        pages[page_id] = self._page_candidate(page, target["id"])
        if target["include_children"]:
            self._walk_child_pages(user_id, page_id, excluded, pages, target["id"])

    def _walk_child_pages(
        self,
        user_id: int,
        parent_block_id: str,
        excluded: set[str],
        pages: dict[str, PageCandidate],
        source_target_id: int | None,
    ) -> None:
        for block in self.list_block_children(user_id, parent_block_id):
            block_type = block.get("type")
            if block_type == "child_page":
                page_id = block["id"]
                if page_id not in excluded and page_id not in pages:
                    try:
                        page = self.retrieve_page(user_id, page_id)
                        pages[page_id] = self._page_candidate(page, source_target_id)
                        self._walk_child_pages(user_id, page_id, excluded, pages, source_target_id)
                    except Exception as exc:
                        self.db.log(
                            "child_page_skipped",
                            user_id=user_id,
                            level="warning",
                            payload={"page_id": page_id, "error": str(exc)},
                        )
            elif block_type == "child_database":
                try:
                    for page in self.query_database_pages(user_id, block["id"]):
                        page_id = page["id"]
                        if page_id not in excluded and page_id not in pages:
                            pages[page_id] = self._page_candidate(page, source_target_id)
                except Exception as exc:
                    self.db.log(
                        "child_database_skipped",
                        user_id=user_id,
                        level="warning",
                        payload={"database_id": block["id"], "error": str(exc)},
                    )
            elif block.get("has_children"):
                self._walk_child_pages(user_id, block["id"], excluded, pages, source_target_id)

    def _page_candidate(self, page: dict[str, Any], source_target_id: int | None) -> PageCandidate:
        return PageCandidate(
            page_id=page["id"],
            title=self._title_from_page(page),
            url=page.get("url") or "",
            last_edited_time=page.get("last_edited_time"),
            source_target_id=source_target_id,
        )

    def fetch_text_blocks(self, user_id: int, page: PageCandidate) -> list[TextBlock]:
        blocks: list[TextBlock] = []
        self._collect_text_blocks(user_id, page, page.page_id, None, [], blocks)
        return blocks

    def _collect_text_blocks(
        self,
        user_id: int,
        page: PageCandidate,
        block_id: str,
        parent_block_id: str | None,
        heading_path: list[str],
        blocks: list[TextBlock],
    ) -> None:
        for block in self.list_block_children(user_id, block_id):
            block_type = block.get("type", "")
            next_heading_path = list(heading_path)
            if block_type in TEXT_BLOCK_TYPES:
                rich_text = block.get(block_type, {}).get("rich_text", [])
                plain_text = "".join(part.get("plain_text", "") for part in rich_text).strip()
                if plain_text:
                    blocks.append(
                        TextBlock(
                            page_id=page.page_id,
                            page_title=page.title,
                            page_url=page.url,
                            block_id=block["id"],
                            block_type=block_type,
                            plain_text=plain_text,
                            rich_text=rich_text,
                            parent_block_id=parent_block_id,
                            heading_path=heading_path,
                            last_edited_time=block.get("last_edited_time"),
                        )
                    )
                if block_type in {"heading_1", "heading_2", "heading_3"} and plain_text:
                    level = int(block_type[-1])
                    next_heading_path = heading_path[: level - 1] + [plain_text]
            elif block_type == "table_row":
                cells = block.get("table_row", {}).get("cells", [])
                plain_text = " | ".join("".join(part.get("plain_text", "") for part in cell) for cell in cells).strip()
                if plain_text:
                    blocks.append(
                        TextBlock(
                            page_id=page.page_id,
                            page_title=page.title,
                            page_url=page.url,
                            block_id=block["id"],
                            block_type=block_type,
                            plain_text=plain_text,
                            rich_text=[],
                            parent_block_id=parent_block_id,
                            heading_path=heading_path,
                            last_edited_time=block.get("last_edited_time"),
                        )
                    )
            if block.get("has_children"):
                self._collect_text_blocks(user_id, page, block["id"], block["id"], next_heading_path, blocks)

    def ensure_inbox_database(self, user_id: int, parent_page_id: str | None) -> tuple[str | None, str | None]:
        connection = self.db.connection_for_user(user_id)
        if connection["notion_inbox_database_id"]:
            return connection["notion_inbox_database_id"], connection["notion_inbox_url"]
        if not parent_page_id:
            return None, None
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": "Nocturne 수정함"}}],
            "properties": self._inbox_properties(),
        }
        data = self._request(user_id, "POST", "/databases", payload)
        database_id = data.get("id")
        url = data.get("url")
        self.db.update(
            """
            UPDATE connections
            SET notion_inbox_database_id = ?, notion_inbox_url = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (database_id, url, utc_now_iso(), user_id),
        )
        return database_id, url

    def _inbox_properties(self) -> dict[str, Any]:
        return {
            "제목": {"title": {}},
            "원본 페이지": {"url": {}},
            "원본 페이지 ID": {"rich_text": {}},
            "블록 ID": {"rich_text": {}},
            "문제 유형": {"select": {"options": [{"name": "오류"}, {"name": "누락"}, {"name": "모순"}]}},
            "원문 문장": {"rich_text": {}},
            "제안 문장": {"rich_text": {}},
            "반영 방식": {"select": {"options": [{"name": "교체"}, {"name": "추가"}]}},
            "근거": {"rich_text": {}},
            "출처 URL": {"url": {}},
            "확신도": {"number": {"format": "number"}},
            "상태": {
                "select": {
                    "options": [
                        {"name": "대기", "color": "yellow"},
                        {"name": "승인", "color": "green"},
                        {"name": "거절", "color": "red"},
                        {"name": "보류", "color": "gray"},
                        {"name": "반영됨", "color": "blue"},
                        {"name": "반영 실패", "color": "red"},
                    ]
                }
            },
            "실행 ID": {"rich_text": {}},
            "생성 시각": {"date": {}},
            "반영 시각": {"date": {}},
            "반영 오류": {"rich_text": {}},
        }

    def create_proposal_page(
        self,
        user_id: int,
        database_id: str,
        run_id: str,
        page: PageCandidate,
        proposal: ProposalCandidate,
    ) -> str:
        source_url = proposal.source_urls[0] if proposal.source_urls else None
        title = self._proposal_title(proposal)
        payload = {
            "parent": {"database_id": database_id},
            "properties": {
                "제목": {"title": [_text(title)]},
                "원본 페이지": {"url": page.url or None},
                "원본 페이지 ID": {"rich_text": [_text(proposal.source_page_id)]},
                "블록 ID": {"rich_text": [_text(proposal.block_id)]},
                "문제 유형": {"select": {"name": ISSUE_LABELS[proposal.issue_type]}},
                "원문 문장": {"rich_text": [_text(_limit(proposal.original_sentence, 1900))]},
                "제안 문장": {"rich_text": [_text(_limit(proposal.suggested_sentence, 1900))]},
                "반영 방식": {"select": {"name": APPLY_LABELS[proposal.apply_mode]}},
                "근거": {"rich_text": [_text(_limit(proposal.rationale, 1900))]},
                "출처 URL": {"url": source_url},
                "확신도": {"number": proposal.confidence},
                "상태": {"select": {"name": proposal.status}},
                "실행 ID": {"rich_text": [_text(run_id)]},
                "생성 시각": {"date": {"start": utc_now_iso()}},
            },
        }
        data = self._request(user_id, "POST", "/pages", payload)
        return data["id"]

    def _proposal_title(self, proposal: ProposalCandidate) -> str:
        label = ISSUE_LABELS.get(proposal.issue_type, proposal.issue_type)
        base = proposal.suggested_sentence or proposal.original_sentence or proposal.rationale
        return _limit(f"{label}: {base}", 80)

    def _title_from_page(self, page: dict[str, Any]) -> str:
        return _page_title_property(page.get("properties", {}))

    def query_approved_proposals(self, user_id: int) -> list[ApprovedProposal]:
        connection = self.db.connection_for_user(user_id)
        database_id = connection["notion_inbox_database_id"]
        if not database_id:
            return []
        data = self._request(
            user_id,
            "POST",
            f"/databases/{database_id}/query",
            {"filter": {"property": "상태", "select": {"equals": "승인"}}, "page_size": 100},
        )
        return [proposal for row in data.get("results", []) if (proposal := self._approved_from_page(row))]

    def _approved_from_page(self, page: dict[str, Any]) -> ApprovedProposal | None:
        props = page.get("properties", {})
        source_page_id = _rich_text_value(props.get("원본 페이지 ID"))
        block_id = _rich_text_value(props.get("블록 ID"))
        original = _rich_text_value(props.get("원문 문장"))
        suggested = _rich_text_value(props.get("제안 문장"))
        mode_label = (props.get("반영 방식", {}).get("select") or {}).get("name", "")
        if not source_page_id or not block_id or not suggested:
            return None
        return ApprovedProposal(
            notion_page_id=page["id"],
            source_page_id=source_page_id,
            source_page_url=(props.get("원본 페이지", {}) or {}).get("url") or "",
            block_id=block_id,
            original_sentence=original,
            suggested_sentence=suggested,
            apply_mode=REVERSE_APPLY_LABELS.get(mode_label, mode_label),
            proposal_title=_title_value(props.get("제목")) or page["id"],
        )

    def update_proposal_status(self, user_id: int, notion_page_id: str, status: str, error: str | None = None) -> None:
        properties: dict[str, Any] = {
            "상태": {"select": {"name": status}},
        }
        if status == "반영됨":
            properties["반영 시각"] = {"date": {"start": utc_now_iso()}}
        if error:
            properties["반영 오류"] = {"rich_text": [_text(_limit(error, 1900))]}
        self._request(user_id, "PATCH", f"/pages/{notion_page_id}", {"properties": properties})

    def retrieve_block(self, user_id: int, block_id: str) -> dict[str, Any]:
        return self._request(user_id, "GET", f"/blocks/{block_id}")

    def replace_sentence_in_block(self, user_id: int, block_id: str, original: str, suggested: str) -> tuple[str, str]:
        block = self.retrieve_block(user_id, block_id)
        block_type = block.get("type")
        if block_type not in TEXT_BLOCK_TYPES:
            raise NotionError(f"{block_type} 블록은 교체 반영을 지원하지 않습니다.")
        rich_text = block.get(block_type, {}).get("rich_text", [])
        plain_before = "".join(part.get("plain_text", "") for part in rich_text)
        if original not in plain_before:
            raise NotionError("원문 문장이 현재 블록에 없습니다.")
        updated_rich_text = replace_rich_text_sentence(rich_text, original, suggested)
        payload = {block_type: {"rich_text": updated_rich_text}}
        self._request(user_id, "PATCH", f"/blocks/{block_id}", payload)
        plain_after = plain_before.replace(original, suggested, 1)
        return plain_before, plain_after

    def append_sentence_after_block(self, user_id: int, block_id: str, suggested: str) -> tuple[str, str]:
        if not suggested.strip():
            raise NotionError("제안 문장이 비어 있습니다.")
        block = self.retrieve_block(user_id, block_id)
        parent = block.get("parent") or {}
        parent_id = parent.get("page_id") or parent.get("block_id")
        if not parent_id:
            raise NotionError("추가 위치를 확인하지 못했습니다.")
        payload = {
            "after": block_id,
            "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [_text(suggested)]}}],
        }
        self._request(user_id, "PATCH", f"/blocks/{parent_id}/children", payload)
        return "", suggested


def replace_rich_text_sentence(rich_text: list[dict[str, Any]], original: str, suggested: str) -> list[dict[str, Any]]:
    plain = "".join(part.get("plain_text", "") for part in rich_text)
    start = plain.find(original)
    if start < 0:
        raise NotionError("원문 문장이 현재 블록에 없습니다.")
    end = start + len(original)
    output: list[dict[str, Any]] = []
    position = 0
    suggestion_inserted = False
    suggestion_template: dict[str, Any] | None = None

    for part in rich_text:
        content = part.get("plain_text", "")
        part_start = position
        part_end = position + len(content)
        position = part_end
        if not content:
            continue
        before_start = part_start
        before_end = min(part_end, start)
        if before_start < before_end:
            output.append(_slice_rich_text(part, before_start - part_start, before_end - part_start))
        if part_start < end and part_end > start and suggestion_template is None:
            suggestion_template = part
        if not suggestion_inserted and part_end > start:
            template = suggestion_template or part
            output.append(_content_rich_text(template, suggested))
            suggestion_inserted = True
        after_start = max(part_start, end)
        after_end = part_end
        if after_start < after_end:
            output.append(_slice_rich_text(part, after_start - part_start, after_end - part_start))

    return [part for part in output if part.get("text", {}).get("content")]


def _slice_rich_text(part: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    return _content_rich_text(part, part.get("plain_text", "")[start:end])


def _content_rich_text(template: dict[str, Any], content: str) -> dict[str, Any]:
    item = copy.deepcopy(template)
    item["type"] = "text"
    item["plain_text"] = content
    item["href"] = None
    item["text"] = {"content": content}
    annotations = template.get("annotations")
    if annotations:
        item["annotations"] = annotations
    return item


def _text(content: str) -> dict[str, Any]:
    return {"type": "text", "text": {"content": content}}


def _limit(value: str, limit: int) -> str:
    value = value or ""
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _rich_text_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(item.get("plain_text", "") for item in prop.get("rich_text", []))


def _title_value(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    return "".join(item.get("plain_text", "") for item in prop.get("title", []))


def _page_title_property(properties: dict[str, Any]) -> str:
    for prop in properties.values():
        if prop.get("type") == "title":
            title = _title_value(prop)
            if title:
                return title
    return "Untitled"
