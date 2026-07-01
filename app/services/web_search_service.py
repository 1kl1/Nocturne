from __future__ import annotations

import re

from app.agent.types import SearchResult, TextBlock
from app.config import Settings
from app.services.http_client import ExternalAPIError, request_json
from app.time_utils import utc_now_iso


class WebSearchService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def candidate_queries(self, blocks: list[TextBlock], limit: int = 4) -> list[str]:
        queries: list[str] = []
        seen: set[str] = set()
        for block in blocks:
            sentences = re.split(r"(?<=[.!?。！？다])\s+", block.plain_text)
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) < 15:
                    continue
                if not re.search(r"\d{4}|\d+[%$₩원억만]?|출시|발표|인수|설립|CEO|가격|매출|논문|published|released", sentence, re.I):
                    continue
                query = re.sub(r"\s+", " ", sentence[:140])
                if query not in seen:
                    seen.add(query)
                    queries.append(query)
                if len(queries) >= limit:
                    return queries
        return queries

    def search_many(self, queries: list[str]) -> dict[str, list[SearchResult]]:
        return {query: self.search(query) for query in queries}

    def search(self, query: str) -> list[SearchResult]:
        provider = self.settings.web_search_provider
        if provider in {"", "none", "disabled"}:
            return []
        try:
            if provider == "tavily":
                return self._tavily(query)
            if provider == "brave":
                return self._brave(query)
            if provider == "serper":
                return self._serper(query)
        except ExternalAPIError:
            return []
        return []

    def _tavily(self, query: str) -> list[SearchResult]:
        data = request_json(
            "POST",
            "https://api.tavily.com/search",
            payload={"api_key": self.settings.web_search_api_key, "query": query, "max_results": 5},
            retries=1,
        ).data
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("content", "")),
                accessed_at=utc_now_iso(),
            )
            for item in data.get("results", [])
            if item.get("url")
        ]

    def _brave(self, query: str) -> list[SearchResult]:
        data = request_json(
            "GET",
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": self.settings.web_search_api_key, "Accept": "application/json"},
            query={"q": query, "count": "5"},
            retries=1,
        ).data
        results = data.get("web", {}).get("results", [])
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=str(item.get("description", "")),
                accessed_at=utc_now_iso(),
            )
            for item in results
            if item.get("url")
        ]

    def _serper(self, query: str) -> list[SearchResult]:
        data = request_json(
            "POST",
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.settings.web_search_api_key},
            payload={"q": query, "num": 5},
            retries=1,
        ).data
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("link", "")),
                snippet=str(item.get("snippet", "")),
                accessed_at=utc_now_iso(),
            )
            for item in data.get("organic", [])
            if item.get("link")
        ]
