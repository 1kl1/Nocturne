from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanStep:
    name: str
    description: str


class Planner:
    def build_nightly_plan(self) -> list[PlanStep]:
        return [
            PlanStep("pre_apply", "이전 실행에서 승인된 수정함 항목을 먼저 반영"),
            PlanStep("expand_targets", "선택한 페이지/데이터베이스와 하위 페이지 확장"),
            PlanStep("filter_recent", "마지막 성공 실행 이후 수정된 페이지만 선택"),
            PlanStep("scan_blocks", "Notion 블록을 LLM 입력용 텍스트로 변환"),
            PlanStep("analyze", "웹 검색 결과와 OpenRouter 모델로 오류/누락/모순 후보 생성"),
            PlanStep("validate", "원문 위치, 중복, 확신도, 최소 수정 원칙 검증"),
            PlanStep("write_proposals", "검증된 제안을 Nocturne 수정함에 저장"),
            PlanStep("notify", "Slack/이메일로 0건 포함 결과 알림 발송"),
        ]
