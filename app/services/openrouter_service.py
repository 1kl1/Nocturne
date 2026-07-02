from __future__ import annotations

import json
import re
from typing import Any

from app.agent.types import ProposalCandidate, SearchResult, TextBlock
from app.config import Settings
from app.services.http_client import ExternalAPIError, request_json


class OpenRouterError(RuntimeError):
    pass


class OpenRouterService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def validate_key(self, api_key: str) -> tuple[bool, str | None]:
        if self.settings.skip_external_validation:
            return (bool(api_key.strip()), None if api_key.strip() else "API 키가 비어 있습니다.")
        try:
            request_json(
                "GET",
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                retries=0,
                timeout=20,
            )
            return True, None
        except ExternalAPIError as exc:
            return False, exc.body or str(exc)

    def analyze_blocks(
        self,
        api_key: str,
        blocks: list[TextBlock],
        search_results: dict[str, list[SearchResult]],
    ) -> list[ProposalCandidate]:
        if not blocks:
            return []
        prompt = self._prompt(blocks, search_results)
        payload: dict[str, Any] = {
            "model": self.settings.openrouter_default_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Nocturne, an agent that audits Notion notes. "
                        "Return only a JSON array matching the requested schema. "
                        "Do not modify source text without evidence. Prefer small edits."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        if self.settings.openrouter_web_search_enabled:
            plugin: dict[str, Any] = {
                "id": "web",
                "max_results": self.settings.openrouter_web_search_max_results,
            }
            if self.settings.openrouter_web_search_engine:
                plugin["engine"] = self.settings.openrouter_web_search_engine
            payload["plugins"] = [plugin]
        try:
            response = request_json(
                "POST",
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": self.settings.openrouter_app_url,
                    "X-Title": self.settings.openrouter_app_name,
                },
                payload=payload,
                retries=2,
                timeout=90,
            )
        except ExternalAPIError as exc:
            raise OpenRouterError(exc.body or str(exc)) from exc
        content = self._message_content(response.data)
        parsed = self._parse_json(content)
        return [ProposalCandidate.from_mapping(item) for item in parsed if isinstance(item, dict)]

    def _prompt(self, blocks: list[TextBlock], search_results: dict[str, list[SearchResult]]) -> str:
        block_lines = []
        for block in blocks:
            heading = " > ".join(block.heading_path)
            block_lines.append(
                {
                    "page_id": block.page_id,
                    "page_title": block.page_title,
                    "page_url": block.page_url,
                    "block_id": block.block_id,
                    "block_type": block.block_type,
                    "heading_path": heading,
                    "plain_text": block.plain_text,
                }
            )
        sources: dict[str, Any] = {
            query: [
                {
                    "title": result.title,
                    "url": result.url,
                    "snippet": result.snippet,
                    "accessed_at": result.accessed_at,
                }
                for result in results
            ]
            for query, results in search_results.items()
        }
        live_web_rule = ""
        if self.settings.openrouter_web_search_enabled:
            live_web_rule = "- OpenRouter 웹 검색 컨텍스트가 제공되면, 근거로 쓴 URL을 source_urls에 반드시 포함한다.\n"
        return (
            "다음 Notion 블록에서 사실 오류, 누락, 모순만 찾아라.\n"
            "출력은 JSON 배열만 허용한다. 스키마:\n"
            "[{\"issue_type\":\"error|omission|contradiction\",\"source_page_id\":\"...\","
            "\"block_id\":\"...\",\"original_sentence\":\"...\",\"suggested_sentence\":\"...\","
            "\"apply_mode\":\"replace|append\",\"rationale\":\"...\",\"source_urls\":[\"...\"],\"confidence\":0.0}]\n"
            "규칙:\n"
            "- 원문은 필요한 최소 범위만 고친다.\n"
            "- replace는 original_sentence가 해당 block plain_text에 정확히 포함되어야 한다.\n"
            "- append는 original_sentence를 추가 위치의 앵커 문장으로 둔다.\n"
            "- 사실 오류는 source_urls 또는 명확한 내부 근거가 있어야 한다.\n"
            f"{live_web_rule}"
            "- 확신이 낮으면 confidence를 낮게 둔다.\n\n"
            f"BLOCKS:\n{json.dumps(block_lines, ensure_ascii=False)}\n\n"
            f"WEB_SEARCH_RESULTS:\n{json.dumps(sources, ensure_ascii=False)}"
        )

    def _message_content(self, data: Any) -> str:
        try:
            return str(data["choices"][0]["message"]["content"])
        except Exception as exc:
            raise OpenRouterError("OpenRouter 응답에서 message content를 찾을 수 없습니다.") from exc

    def _parse_json(self, content: str) -> list[Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise OpenRouterError(f"LLM이 JSON 배열을 반환하지 않았습니다: {content[:300]}") from exc
        if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
            return parsed["proposals"]
        if not isinstance(parsed, list):
            raise OpenRouterError("LLM JSON 최상위 값이 배열이 아닙니다.")
        return parsed
