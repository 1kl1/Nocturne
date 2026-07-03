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
        agent_settings: Any | None = None,
    ) -> list[ProposalCandidate]:
        if not blocks:
            return []
        prompt = self._prompt(blocks, search_results, agent_settings)
        payload: dict[str, Any] = {
            "model": self.settings.openrouter_default_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Nocturne, an expert AI agent that thoroughly and proactively audits Notion notes. "
                        "Actively detect factual errors, technical mistakes, logical contradictions, outdated explanations, or critical omissions. "
                        "Return only a JSON array matching the requested schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if self.settings.openrouter_web_search_enabled or _agent_bool(agent_settings, "openrouter_live_search"):
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

    def _prompt(
        self,
        blocks: list[TextBlock],
        search_results: dict[str, list[SearchResult]],
        agent_settings: Any | None = None,
    ) -> str:
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
        if self.settings.openrouter_web_search_enabled or _agent_bool(agent_settings, "openrouter_live_search"):
            live_web_rule = "- OpenRouter 웹 검색 컨텍스트가 제공되면, 근거로 쓴 URL을 source_urls에 반드시 포함한다.\n"
        recent_trend_rule = ""
        if _agent_bool(agent_settings, "search_recent_trends"):
            recent_trend_rule = "- 문서 주제와 관련된 최근 동향, 변경된 표준, 최신 API/정책을 우선 확인한다.\n"
        strict_source_rule = ""
        if _agent_bool(agent_settings, "strict_source_mode", default=True):
            strict_source_rule = "- 외부 출처 URL이나 문서 내부 문맥/기술 표준에 따른 논리적 근거가 명확한 사항을 우선하여 제안한다.\n"
        additional_context = str(_agent_value(agent_settings, "additional_context", "") or "").strip()
        context_block = f"\n\nUSER_AGENT_CONTEXT:\n{additional_context}" if additional_context else ""
        return (
            "다음 Notion 블록을 적극적이고 철저하게 검토하여 사실 오류, 기술적/논리적 오류, 설명 누락, 내부 모순을 빠짐없이 찾아내어 제안하라.\n"
            "출력은 JSON 배열만 허용한다. 스키마:\n"
            "[{\"issue_type\":\"error|omission|contradiction\",\"source_page_id\":\"...\","
            "\"block_id\":\"...\",\"original_sentence\":\"...\",\"suggested_sentence\":\"...\","
            "\"apply_mode\":\"replace|append\",\"rationale\":\"...\",\"source_urls\":[\"...\"],\"confidence\":0.0}]\n"
            "규칙:\n"
            "- 틀린 내용, 부정확한 개념, 계산 오류나 오해의 소지가 있는 문장이 있다면 소극적으로 넘기지 말고 적극적으로 수정 제안을 작성한다.\n"
            "- replace는 original_sentence가 해당 block plain_text에 정확히 포함되어야 한다.\n"
            "- append는 original_sentence를 추가 위치의 앵커 문장으로 둔다.\n"
            "- 사실 오류(error)나 모순의 rationale에는 왜 오류인지 판단한 이유(문서 내부 문맥, 논리적 오류, 기술 표준 등)를 구체적으로 기술한다.\n"
            f"{live_web_rule}"
            f"{recent_trend_rule}"
            f"{strict_source_rule}"
            "- 발견한 오류나 문제점이 명확하다면 높은 confidence(0.7~0.95)를 부여한다.\n\n"
            f"BLOCKS:\n{json.dumps(block_lines, ensure_ascii=False)}\n\n"
            f"WEB_SEARCH_RESULTS:\n{json.dumps(sources, ensure_ascii=False)}"
            f"{context_block}"
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


def _agent_value(agent_settings: Any | None, key: str, default: Any = None) -> Any:
    if agent_settings is None:
        return default
    try:
        return agent_settings[key]
    except (KeyError, IndexError, TypeError):
        if isinstance(agent_settings, dict):
            return agent_settings.get(key, default)
        return default


def _agent_bool(agent_settings: Any | None, key: str, default: bool = False) -> bool:
    return bool(_agent_value(agent_settings, key, 1 if default else 0))
