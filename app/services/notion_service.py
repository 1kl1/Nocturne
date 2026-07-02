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
INBOX_DATABASE_TITLE = "Nocturne"

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


@dataclass(frozen=True)
class KnowledgeGraphSyncResult:
    status: str
    node_count: int
    edge_count: int
    errors: tuple[str, ...] = ()


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

    @staticmethod
    def is_not_found_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "404" in message or "object_not_found" in message or "could not find" in message or "archived" in message

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

    def query_database_pages(self, user_id: int, database_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page_size = min(100, max(1, limit - len(pages))) if limit else 100
            payload: dict[str, Any] = {"page_size": page_size}
            if cursor:
                payload["start_cursor"] = cursor
            data = self._request(user_id, "POST", f"/databases/{database_id}/query", payload)
            pages.extend(data.get("results", []))
            if limit and len(pages) >= limit:
                return pages[:limit]
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                return pages

    def search_selectable_objects(
        self,
        user_id: int,
        *,
        query: str = "",
        object_type: str | None = None,
        limit: int = 25,
        root_only: bool = False,
        resolve_parent_titles: bool = False,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(limit, 100))
        requested_type = object_type if object_type in {"page", "database"} else None
        payload: dict[str, Any] = {
            "page_size": page_size,
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
        if query.strip():
            payload["query"] = query.strip()
        if requested_type:
            payload["filter"] = {"value": requested_type, "property": "object"}
        data = self._request(user_id, "POST", "/search", payload)
        results: list[dict[str, Any]] = []
        parent_cache: dict[tuple[str, str], str] = {}
        for item in data.get("results", []):
            result = self._selectable_object(user_id, item, parent_cache, resolve_parent_titles)
            if not result:
                continue
            if requested_type and result["object_type"] != requested_type:
                continue
            if root_only and result["parent_type"] != "workspace":
                continue
            results.append(result)
        return results

    def list_selectable_children(
        self,
        user_id: int,
        parent_id: str,
        parent_type: str,
        *,
        object_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        requested_type = object_type if object_type in {"page", "database"} else None
        page_size = max(1, min(limit, 100))
        if parent_type == "workspace":
            return self.search_selectable_objects(
                user_id,
                object_type=requested_type,
                limit=page_size,
                root_only=True,
            )
        if parent_type == "page":
            return self._page_child_selectable_objects(user_id, parent_id, requested_type, page_size)
        if parent_type == "database":
            if requested_type == "database":
                return []
            return self._database_child_selectable_objects(user_id, parent_id, page_size)
        return []

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

    def sync_knowledge_graph(self, user_id: int) -> KnowledgeGraphSyncResult:
        targets = self.db.active_targets(user_id)
        if not targets:
            self._replace_graph_cache(user_id, {}, set(), "success", None)
            return KnowledgeGraphSyncResult(status="success", node_count=0, edge_count=0)

        nodes: dict[str, dict[str, Any]] = {}
        edges: set[tuple[str, str, str]] = set()
        visited_pages: set[str] = set()
        visited_databases: set[str] = set()
        errors: list[str] = []

        for target in targets:
            excluded = set(Database.decode_json_array(target["excluded_page_ids"]))
            object_id = str(target["notion_object_id"])
            try:
                if target["notion_object_type"] == "page":
                    self._sync_graph_page(
                        user_id,
                        object_id,
                        nodes,
                        edges,
                        visited_pages,
                        visited_databases,
                        excluded,
                        target["id"],
                    )
                else:
                    self._sync_graph_database(
                        user_id,
                        object_id,
                        nodes,
                        edges,
                        visited_pages,
                        visited_databases,
                        excluded,
                        target["id"],
                    )
            except Exception as exc:
                message = f'{target["title"]}: {exc}'
                errors.append(message)
                self.db.log(
                    "knowledge_graph_sync_target_failed",
                    user_id=user_id,
                    level="warning",
                    payload={"target_id": target["id"], "error": str(exc)},
                )

        if not nodes and errors:
            error_message = "\n".join(errors[:5])
            self._record_graph_sync(user_id, "failed", 0, 0, error_message)
            raise NotionError(f"지식 그래프 동기화 실패: {error_message}")

        self._replace_graph_cache(user_id, nodes, edges, "partial_success" if errors else "success", "\n".join(errors[:5]) or None)
        return KnowledgeGraphSyncResult(
            status="partial_success" if errors else "success",
            node_count=len(nodes),
            edge_count=len(edges),
            errors=tuple(errors),
        )

    def _sync_graph_page(
        self,
        user_id: int,
        page_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: set[tuple[str, str, str]],
        visited_pages: set[str],
        visited_databases: set[str],
        excluded: set[str],
        source_target_id: int | None,
    ) -> None:
        if page_id in excluded:
            return
        if page_id not in nodes:
            page = self.retrieve_page(user_id, page_id)
            nodes[page_id] = self._graph_page_node(page, source_target_id)
        if page_id in visited_pages:
            return
        visited_pages.add(page_id)
        self._walk_graph_blocks(
            user_id,
            page_id,
            page_id,
            nodes,
            edges,
            visited_pages,
            visited_databases,
            excluded,
            source_target_id,
        )

    def _sync_graph_database(
        self,
        user_id: int,
        database_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: set[tuple[str, str, str]],
        visited_pages: set[str],
        visited_databases: set[str],
        excluded: set[str],
        source_target_id: int | None,
    ) -> None:
        if database_id not in nodes:
            database = self.retrieve_database(user_id, database_id)
            nodes[database_id] = self._graph_database_node(database, source_target_id)
        if database_id in visited_databases:
            return
        visited_databases.add(database_id)
        for page in self.query_database_pages(user_id, database_id):
            page_id = str(page.get("id") or "")
            if not page_id or page_id in excluded:
                continue
            nodes[page_id] = self._graph_page_node(page, source_target_id)
            edges.add((database_id, page_id, "database_page"))
            self._sync_graph_page(
                user_id,
                page_id,
                nodes,
                edges,
                visited_pages,
                visited_databases,
                excluded,
                source_target_id,
            )

    def _walk_graph_blocks(
        self,
        user_id: int,
        owner_page_id: str,
        block_id: str,
        nodes: dict[str, dict[str, Any]],
        edges: set[tuple[str, str, str]],
        visited_pages: set[str],
        visited_databases: set[str],
        excluded: set[str],
        source_target_id: int | None,
    ) -> None:
        for block in self.list_block_children(user_id, block_id):
            block_type = block.get("type")
            if block_type == "child_page":
                child_id = str(block.get("id") or "")
                if not child_id or child_id in excluded:
                    continue
                try:
                    page = self.retrieve_page(user_id, child_id)
                    nodes[child_id] = self._graph_page_node(page, source_target_id)
                except Exception:
                    nodes.setdefault(child_id, self._graph_child_page_node(block, source_target_id))
                edges.add((owner_page_id, child_id, "child_page"))
                self._sync_graph_page(
                    user_id,
                    child_id,
                    nodes,
                    edges,
                    visited_pages,
                    visited_databases,
                    excluded,
                    source_target_id,
                )
            elif block_type == "child_database":
                database_id = str(block.get("id") or "")
                if not database_id:
                    continue
                try:
                    database = self.retrieve_database(user_id, database_id)
                    nodes[database_id] = self._graph_database_node(database, source_target_id)
                except Exception:
                    nodes.setdefault(database_id, self._graph_child_database_node(block, source_target_id))
                edges.add((owner_page_id, database_id, "child_database"))
                try:
                    self._sync_graph_database(
                        user_id,
                        database_id,
                        nodes,
                        edges,
                        visited_pages,
                        visited_databases,
                        excluded,
                        source_target_id,
                    )
                except Exception as exc:
                    self.db.log(
                        "knowledge_graph_child_database_skipped",
                        user_id=user_id,
                        level="warning",
                        payload={"database_id": database_id, "error": str(exc)},
                    )
            elif block.get("has_children"):
                self._walk_graph_blocks(
                    user_id,
                    owner_page_id,
                    str(block.get("id") or ""),
                    nodes,
                    edges,
                    visited_pages,
                    visited_databases,
                    excluded,
                    source_target_id,
                )

    def _graph_page_node(self, page: dict[str, Any], source_target_id: int | None) -> dict[str, Any]:
        parent = page.get("parent") or {}
        parent_type = str(parent.get("type") or "").replace("_id", "")
        parent_id = str(parent.get(f"{parent_type}_id") or parent.get("block_id") or "")
        return {
            "notion_object_id": str(page.get("id") or ""),
            "object_type": "page",
            "title": self._title_from_page(page),
            "url": str(page.get("url") or ""),
            "parent_id": parent_id,
            "parent_type": parent_type,
            "source_target_id": source_target_id,
            "last_edited_time": str(page.get("last_edited_time") or ""),
        }

    def _graph_database_node(self, database: dict[str, Any], source_target_id: int | None) -> dict[str, Any]:
        parent = database.get("parent") or {}
        parent_type = str(parent.get("type") or "").replace("_id", "")
        parent_id = str(parent.get(f"{parent_type}_id") or parent.get("block_id") or "")
        return {
            "notion_object_id": str(database.get("id") or ""),
            "object_type": "database",
            "title": _database_title(database),
            "url": str(database.get("url") or ""),
            "parent_id": parent_id,
            "parent_type": parent_type,
            "source_target_id": source_target_id,
            "last_edited_time": str(database.get("last_edited_time") or ""),
        }

    def _graph_child_page_node(self, block: dict[str, Any], source_target_id: int | None) -> dict[str, Any]:
        return {
            "notion_object_id": str(block.get("id") or ""),
            "object_type": "page",
            "title": str((block.get("child_page") or {}).get("title") or "Untitled"),
            "url": "",
            "parent_id": "",
            "parent_type": "page",
            "source_target_id": source_target_id,
            "last_edited_time": str(block.get("last_edited_time") or ""),
        }

    def _graph_child_database_node(self, block: dict[str, Any], source_target_id: int | None) -> dict[str, Any]:
        return {
            "notion_object_id": str(block.get("id") or ""),
            "object_type": "database",
            "title": str((block.get("child_database") or {}).get("title") or "Untitled database"),
            "url": "",
            "parent_id": "",
            "parent_type": "page",
            "source_target_id": source_target_id,
            "last_edited_time": str(block.get("last_edited_time") or ""),
        }

    def _replace_graph_cache(
        self,
        user_id: int,
        nodes: dict[str, dict[str, Any]],
        edges: set[tuple[str, str, str]],
        status: str,
        error_message: str | None,
    ) -> None:
        now = utc_now_iso()
        valid_node_ids = set(nodes)
        valid_edges = [edge for edge in edges if edge[0] in valid_node_ids and edge[1] in valid_node_ids]
        with self.db.connection() as conn:
            conn.execute("DELETE FROM knowledge_graph_edges WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM knowledge_graph_nodes WHERE user_id = ?", (user_id,))
            for node in nodes.values():
                conn.execute(
                    """
                    INSERT INTO knowledge_graph_nodes
                        (user_id, notion_object_id, object_type, title, url, parent_id, parent_type,
                         source_target_id, last_edited_time, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        node["notion_object_id"],
                        node["object_type"],
                        node["title"],
                        node["url"],
                        node["parent_id"],
                        node["parent_type"],
                        node["source_target_id"],
                        node["last_edited_time"],
                        now,
                        now,
                    ),
                )
            for source, target, relation_type in valid_edges:
                conn.execute(
                    """
                    INSERT INTO knowledge_graph_edges
                        (user_id, source_object_id, target_object_id, relation_type, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, source, target, relation_type, now, now),
                )
            conn.execute(
                """
                INSERT INTO knowledge_graph_syncs
                    (user_id, status, node_count, edge_count, last_synced_at, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    status = excluded.status,
                    node_count = excluded.node_count,
                    edge_count = excluded.edge_count,
                    last_synced_at = excluded.last_synced_at,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (user_id, status, len(nodes), len(valid_edges), now, error_message, now),
            )
            conn.commit()

    def _record_graph_sync(self, user_id: int, status: str, node_count: int, edge_count: int, error_message: str | None) -> None:
        now = utc_now_iso()
        self.db.update(
            """
            INSERT INTO knowledge_graph_syncs
                (user_id, status, node_count, edge_count, last_synced_at, error_message, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                status = excluded.status,
                node_count = excluded.node_count,
                edge_count = excluded.edge_count,
                last_synced_at = excluded.last_synced_at,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (user_id, status, node_count, edge_count, now, error_message, now),
        )

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

    def _selectable_object(
        self,
        user_id: int,
        item: dict[str, Any],
        parent_cache: dict[tuple[str, str], str],
        resolve_parent_titles: bool = False,
    ) -> dict[str, Any] | None:
        object_type = item.get("object")
        object_id = str(item.get("id") or "")
        if not object_id or object_type not in {"page", "database"}:
            return None
        if object_type == "page":
            title = self._title_from_page(item)
        else:
            title = _database_title(item)
        parent = self._parent_context(user_id, item.get("parent") or {}, parent_cache, resolve_parent_titles)
        return {
            "object_id": object_id,
            "object_type": object_type,
            "title": title,
            "url": str(item.get("url") or ""),
            "last_edited_time": str(item.get("last_edited_time") or ""),
            "has_children": True,
            **parent,
        }

    def _page_child_selectable_objects(
        self,
        user_id: int,
        page_id: str,
        requested_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for block in self.list_block_children(user_id, page_id):
            block_type = block.get("type")
            if block_type == "child_page" and requested_type in {None, "page"}:
                child = block.get("child_page") or {}
                results.append(
                    {
                        "object_id": str(block.get("id") or ""),
                        "object_type": "page",
                        "title": str(child.get("title") or "Untitled"),
                        "url": "",
                        "last_edited_time": str(block.get("last_edited_time") or ""),
                        "has_children": bool(block.get("has_children")),
                        "parent_id": page_id,
                        "parent_type": "page",
                        "parent_title": "상위 페이지",
                    }
                )
            elif block_type == "child_database" and requested_type in {None, "database"}:
                child = block.get("child_database") or {}
                results.append(
                    {
                        "object_id": str(block.get("id") or ""),
                        "object_type": "database",
                        "title": str(child.get("title") or "Untitled database"),
                        "url": "",
                        "last_edited_time": str(block.get("last_edited_time") or ""),
                        "has_children": True,
                        "parent_id": page_id,
                        "parent_type": "page",
                        "parent_title": "상위 페이지",
                    }
                )
            if len(results) >= limit:
                return results[:limit]
        return results

    def _database_child_selectable_objects(self, user_id: int, database_id: str, limit: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for page in self.query_database_pages(user_id, database_id, limit=limit):
            results.append(
                {
                    "object_id": str(page.get("id") or ""),
                    "object_type": "page",
                    "title": self._title_from_page(page),
                    "url": str(page.get("url") or ""),
                    "last_edited_time": str(page.get("last_edited_time") or ""),
                    "has_children": True,
                    "parent_id": database_id,
                    "parent_type": "database",
                    "parent_title": "상위 데이터베이스",
                }
            )
        return results

    def _parent_context(
        self,
        user_id: int,
        parent: dict[str, Any],
        parent_cache: dict[tuple[str, str], str],
        resolve_parent_titles: bool = False,
    ) -> dict[str, str]:
        parent_type = str(parent.get("type") or "")
        if parent_type == "page_id":
            parent_id = str(parent.get("page_id") or "")
            return {
                "parent_id": parent_id,
                "parent_type": "page",
                "parent_title": self._parent_title(user_id, "page", parent_id, parent_cache) if resolve_parent_titles else "상위 페이지",
            }
        if parent_type == "database_id":
            parent_id = str(parent.get("database_id") or "")
            return {
                "parent_id": parent_id,
                "parent_type": "database",
                "parent_title": self._parent_title(user_id, "database", parent_id, parent_cache) if resolve_parent_titles else "상위 데이터베이스",
            }
        if parent_type == "block_id":
            return {
                "parent_id": str(parent.get("block_id") or ""),
                "parent_type": "block",
                "parent_title": "상위 블록",
            }
        if parent_type == "workspace":
            return {"parent_id": "", "parent_type": "workspace", "parent_title": "워크스페이스"}
        return {"parent_id": "", "parent_type": "", "parent_title": ""}

    def _parent_title(
        self,
        user_id: int,
        parent_type: str,
        parent_id: str,
        parent_cache: dict[tuple[str, str], str],
    ) -> str:
        if not parent_id:
            return "상위 항목"
        cache_key = (parent_type, parent_id)
        if cache_key in parent_cache:
            return parent_cache[cache_key]
        fallback = "상위 페이지" if parent_type == "page" else "상위 데이터베이스"
        try:
            if parent_type == "page":
                title = self._title_from_page(self.retrieve_page(user_id, parent_id))
            else:
                title = _database_title(self.retrieve_database(user_id, parent_id))
        except Exception:
            title = fallback
        parent_cache[cache_key] = title
        return title

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
            try:
                self.retrieve_database(user_id, connection["notion_inbox_database_id"])
                self._ensure_inbox_database_title(user_id, connection["notion_inbox_database_id"])
                return connection["notion_inbox_database_id"], connection["notion_inbox_url"]
            except Exception as exc:
                if self.is_not_found_error(exc):
                    self.db.log(
                        "inbox_database_missing_renewing",
                        user_id=user_id,
                        level="warning",
                        payload={"database_id": connection["notion_inbox_database_id"], "error": str(exc)},
                    )
                    self.db.update(
                        "UPDATE connections SET notion_inbox_database_id = NULL, notion_inbox_url = NULL, updated_at = ? WHERE user_id = ?",
                        (utc_now_iso(), user_id),
                    )
                else:
                    raise
        if not parent_page_id:
            return None, None
        payload = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": INBOX_DATABASE_TITLE}}],
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

    def _ensure_inbox_database_title(self, user_id: int, database_id: str) -> None:
        try:
            self._request(
                user_id,
                "PATCH",
                f"/databases/{database_id}",
                {"title": [{"type": "text", "text": {"content": INBOX_DATABASE_TITLE}}]},
            )
        except Exception as exc:
            self.db.log("inbox_title_update_failed", user_id=user_id, level="warning", payload={"error": str(exc)})

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
        try:
            data = self._request(
                user_id,
                "POST",
                f"/databases/{database_id}/query",
                {"filter": {"property": "상태", "select": {"equals": "승인"}}, "page_size": 100},
            )
        except Exception as exc:
            if self.is_not_found_error(exc):
                self.db.log(
                    "inbox_database_not_found_reset",
                    user_id=user_id,
                    level="warning",
                    payload={"database_id": database_id, "error": str(exc)},
                )
                self.db.update(
                    "UPDATE connections SET notion_inbox_database_id = NULL, notion_inbox_url = NULL, updated_at = ? WHERE user_id = ?",
                    (utc_now_iso(), user_id),
                )
                return []
            raise
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
        try:
            self._request(user_id, "PATCH", f"/pages/{notion_page_id}", {"properties": properties})
        except Exception as exc:
            if self.is_not_found_error(exc):
                self.db.log(
                    "proposal_page_missing",
                    user_id=user_id,
                    level="warning",
                    payload={"page_id": notion_page_id, "error": str(exc)},
                )
            else:
                raise

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


def _database_title(database: dict[str, Any]) -> str:
    title = "".join(item.get("plain_text", "") for item in database.get("title", []))
    return title or "Untitled database"
