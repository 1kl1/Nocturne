from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.security import mask_secret
from app.time_utils import parse_iso

ASSET_VERSION = "20260702i"


EVENT_LABELS = {
    "agent_tool_call": "Agent 액션",
    "approval_apply_failed": "승인 반영 실패",
    "child_database_skipped": "하위 DB 건너뜀",
    "child_page_skipped": "하위 페이지 건너뜀",
    "email_console": "이메일 콘솔",
    "email_verified": "이메일 인증",
    "inbox_ensure_after_target_failed": "수정함 확인 실패",
    "notion_connected": "Notion 연결",
    "notion_disconnected": "Notion 해제",
    "onboarding_review_boundary_acknowledged": "승인 경계 확인",
    "page_scan_failed": "페이지 점검 실패",
    "proposal_page_missing": "원본 페이지 누락",
    "proposal_rejected": "제안 제외",
    "proposal_write_failed": "제안 저장 실패",
    "run_failed": "점검 실패",
    "run_finished": "점검 완료",
    "run_started": "점검 시작",
    "scheduler_started": "스케줄러 시작",
    "scheduler_tick_failed": "스케줄러 실패",
    "target_expand_failed": "대상 확장 실패",
}

STATUS_LABELS = {
    "pending": "대기",
    "running": "실행 중",
    "success": "성공",
    "partial_success": "부분 성공",
    "failed": "실패",
}

ISSUE_LABELS = {
    "error": "오류",
    "omission": "누락",
    "contradiction": "모순",
}

APPLY_LABELS = {
    "replace": "교체",
    "append": "추가",
}


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, active: str = "home", notice: str | None = None) -> str:
    nav = [
        ("home", "/dashboard", "홈", "home"),
        ("logs", "/runs", "로그", "logs"),
        ("settings", "/settings", "설정", "settings"),
    ]
    nav_html = "".join(
        f'<a class="nav-link icon-nav {"active" if key == active else ""}" href="{href}" aria-label="{label}" title="{label}">{_nav_icon(icon)}<span class="sr-only">{label}</span></a>'
        for key, href, label, icon in nav
    )
    notice_html = f'<div class="notice">{_escape(notice)}</div>' if notice else ""
    target_picker_script = f'<script src="/static/target-picker.js?v={ASSET_VERSION}"></script>' if "data-target-picker" in body else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)} · Nocturne</title>
  <link rel="icon" href="/static/nocturne-icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/styles.css?v={ASSET_VERSION}">
</head>
<body class="app-body">
  <header class="app-capsule">
    <a class="brand" href="/dashboard"><img src="/static/nocturne-icon.svg" alt=""><span>Nocturne</span></a>
    <nav>{nav_html}</nav>
  </header>
  <main class="shell app-shell">
    {notice_html}
    {body}
  </main>
  {target_picker_script}
</body>
</html>"""


def _nav_icon(name: str) -> str:
    paths = {
        "home": '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-8.5Z"/>',
        "logs": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
        "settings": '<path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.05.05a2 2 0 1 1-2.83 2.83l-.05-.05A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21a2 2 0 1 1-4 0v-.07A1.7 1.7 0 0 0 8.6 19a1.7 1.7 0 0 0-1.88.34l-.05.05a2 2 0 1 1-2.83-2.83l.05-.05A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H2.8a2 2 0 1 1 0-4h.07A1.7 1.7 0 0 0 4.6 8a1.7 1.7 0 0 0-.34-1.88l-.05-.05a2 2 0 1 1 2.83-2.83l.05.05A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V2.8a2 2 0 1 1 4 0v.07A1.7 1.7 0 0 0 15.4 5a1.7 1.7 0 0 0 1.88-.34l.05-.05a2 2 0 1 1 2.83 2.83l-.05.05A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 .6 1 1.7 1.7 0 0 0 1.1.4h.1a2 2 0 1 1 0 4h-.07a1.7 1.7 0 0 0-1.73.6Z"/>',
    }
    return f'<svg viewBox="0 0 24 24" aria-hidden="true">{paths.get(name, "")}</svg>'


def status_pill(connected: bool, yes: str = "연결됨", no: str = "필요") -> str:
    cls = "ok" if connected else "warn"
    label = yes if connected else no
    return f'<span class="pill {cls}">{label}</span>'


def intro_page(notice: str | None = None) -> str:
    notice_html = f'<div class="intro-notice">{_escape(notice)}</div>' if notice else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nocturne</title>
  <link rel="icon" href="/static/nocturne-icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/styles.css?v={ASSET_VERSION}">
</head>
<body class="intro-body">
  <main class="intro-shell">
    {notice_html}
    <section class="intro-hero" aria-labelledby="intro-title">
      <img class="intro-icon" src="/static/nocturne-icon.svg" alt="">
      <h1 id="intro-title">Nocturne</h1>
      <a class="start-button" href="/onboarding">
        <span>시작하기</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l8 7-8 7V5Z"/></svg>
      </a>
    </section>
  </main>
  <script src="/static/intro.js?v={ASSET_VERSION}"></script>
</body>
</html>"""


def onboarding_page(
    connection: sqlite3.Row,
    settings: sqlite3.Row,
    targets: list[sqlite3.Row],
    review_acknowledged: bool,
    notice: str | None = None,
    step: int = 0,
) -> str:
    notion_connected = bool(connection["notion_access_token_encrypted"])
    email_connected = bool(connection["notification_email_verified"])
    email_pending = bool(connection["notification_email"]) and not email_connected
    has_targets = bool(targets)
    max_allowed = 0
    if notion_connected:
        max_allowed = 2 if has_targets else 1
    if notion_connected and has_targets and email_connected:
        max_allowed = 3
    safe_step = max(0, min(step, max_allowed, 3))
    if has_targets and safe_step == 1:
        safe_step = 2
    notice_html = f'<div class="tutorial-notice">{_escape(notice)}</div>' if notice else ""
    target_rows = "".join(
        f'<li><strong>{_escape(target["title"])}</strong><span>{_escape(target["notion_object_type"])} · 하위 포함</span></li>'
        for target in targets[:4]
    ) or "<li><strong>아직 비어 있음</strong><span>첫 점검 범위를 추가하면 여기에 남습니다.</span></li>"
    skipped_steps = "1" if has_targets else ""
    email_flow = _onboarding_email_flow(connection, settings, email_pending, email_connected)
    back_button = """
<button class="icon-back" type="button" data-prev aria-label="이전 단계">
  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>
</button>
"""
    body = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>온보딩 · Nocturne</title>
  <link rel="icon" href="/static/nocturne-icon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/styles.css?v={ASSET_VERSION}">
</head>
<body class="tutorial-body">
  <main class="tutorial" data-start-step="{safe_step}" data-max-step="{max_allowed}" data-skip-steps="{_escape(skipped_steps)}">
    <header class="tutorial-head">
      <a class="tutorial-brand" href="/"><img src="/static/nocturne-icon.svg" alt=""><span>Nocturne</span></a>
    </header>

    {notice_html}

    <section class="tutorial-card">
        <article class="onboarding-step" data-step="0">
          <p class="kicker">01 · workspace</p>
          <h1>먼저 Notion 작업실을 연결합니다.</h1>
          <p class="step-lede">Nocturne은 사용자가 허용한 Notion 범위 안에서만 시작합니다.</p>
          <div class="setup-line">
            <span>Notion</span>
            <strong>{_escape(connection["notion_workspace_name"] or connection["notion_workspace_id"] or "연결 대기")}</strong>
            {status_pill(notion_connected)}
          </div>
          <div class="step-actions">
            <a class="button primary ink-button" href="/auth/notion/start?next=/onboarding?step=1">Notion 연결</a>
          </div>
        </article>

        <article class="onboarding-step" data-step="1">
          {back_button}
          <p class="kicker">02 · scope</p>
          <h1>처음 점검할 페이지를 정합니다.</h1>
          <p class="step-lede">선택한 페이지의 하위 페이지는 기본으로 포함하고 데이터베이스의 페이지도 점검 범위에 넣습니다.</p>
          <form class="onboarding-form compact-form target-picker-form" method="post" action="/targets" data-target-form>
            <input type="hidden" name="return_to" value="/onboarding?step=2">
            {target_picker_fields()}
            <button class="primary ink-button" type="submit">대상 추가</button>
          </form>
          <ul class="target-chip-list">{target_rows}</ul>
        </article>

        <article class="onboarding-step" data-step="2">
          {back_button}
          <p class="kicker">03 · morning brief</p>
          <h1>아침 알림은 이메일로 받습니다.</h1>
          {email_flow}
        </article>

        <article class="onboarding-step" data-step="3">
          {back_button}
          <p class="kicker">04 · launch</p>
          <h1>밤의 점검 루프를 켭니다.</h1>
          <p class="step-lede">첫 실행에서는 기준선을 잡으려고 선택 범위 전체를 읽습니다.</p>
          <div class="setup-checklist">
            <div>{status_pill(notion_connected)}<strong>Notion</strong></div>
            <div>{status_pill(has_targets)}<strong>점검 대상</strong></div>
            <div>{status_pill(email_connected)}<strong>이메일</strong></div>
          </div>
          <div class="step-actions">
            <form method="post" action="/runs/manual"><button class="primary ink-button" type="submit">첫 점검 실행</button></form>
            <a class="button" href="/dashboard">대시보드</a>
          </div>
        </article>
    </section>
  </main>
  <script src="/static/target-picker.js?v={ASSET_VERSION}"></script>
  <script src="/static/onboarding.js?v={ASSET_VERSION}"></script>
</body>
</html>"""
    return body


def _onboarding_email_flow(
    connection: sqlite3.Row,
    settings: sqlite3.Row,
    email_pending: bool,
    email_connected: bool,
) -> str:
    if email_connected:
        return f"""
<div class="setup-line">
  <span>이메일</span>
  <strong>{_escape(connection["notification_email"] or "인증됨")}</strong>
  {status_pill(True)}
</div>
<form class="onboarding-form time-strip" method="post" action="/notifications">
  <input type="hidden" name="return_to" value="/onboarding?step=3">
  <input type="hidden" name="default_channel" value="email">
  <input type="hidden" name="notify_zero" value="1">
  <label>점검 <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}"></label>
  <label>알림 <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}"></label>
  <label>타임존 <input name="timezone" value="{_escape(settings["timezone"])}"></label>
  <button class="primary ink-button" type="submit">시간 저장하고 계속</button>
</form>
"""

    verify_form = ""
    if email_pending:
        verify_form = f"""
<form class="onboarding-form inline-form" method="post" action="/settings/email/verify">
  <input type="hidden" name="return_to" value="/onboarding?step=2">
  <label>인증 코드
    <input name="code" inputmode="numeric" placeholder="000000">
  </label>
  <button type="submit">확인</button>
</form>
"""

    return f"""
<form class="onboarding-form inline-form" method="post" action="/settings/email">
  <input type="hidden" name="return_to" value="/onboarding?step=2">
  <label>이메일
    <input name="email" type="email" value="{_escape(connection["notification_email"] or "")}" placeholder="me@example.com">
  </label>
  <button type="submit">코드 받기</button>
</form>
{verify_form}
"""


def dashboard(
    user: sqlite3.Row,
    connection: sqlite3.Row,
    settings: sqlite3.Row,
    targets: list[sqlite3.Row],
    runs: list[sqlite3.Row],
    run_items: list[sqlite3.Row],
    notice: str | None = None,
) -> str:
    latest_run = runs[0] if runs else None
    timezone_name = settings["timezone"] or "Asia/Seoul"
    body = f"""
<section class="page-head home-head">
  <div>
    <p class="eyebrow">최근 실행과 제안 내역</p>
    <h1>홈</h1>
  </div>
  <div class="actions">
    <form method="post" action="/runs/manual">
      <input type="hidden" name="return_to" value="/dashboard">
      <button class="primary" type="submit">수동 점검 실행</button>
    </form>
  </div>
</section>

<section class="panel home-run-panel">
  <div class="panel-head"><h2>최근 실행</h2><a href="/runs">로그 보기</a></div>
  {_home_run_summary(latest_run, timezone_name)}
  {_run_item_board(run_items, timezone_name)}
</section>
"""
    return page("홈", body, "home", notice)


def _home_run_summary(run: sqlite3.Row | None, timezone_name: str) -> str:
    if not run:
        return '<p class="empty">아직 실행 기록이 없습니다.</p>'
    event_time = run["finished_at"] or run["started_at"] or run["created_at"]
    duration = _run_duration_text(run["started_at"], run["finished_at"])
    return f"""
<div class="run-overview">
  <div class="run-status-line">
    <span>현재 상태</span>
    {_status_badge(run["status"] or "")}
  </div>
  <dl class="run-facts">
    <div><dt>시각</dt><dd>{_relative_time_html(event_time, timezone_name)}<small>{_escape(duration)}</small></dd></div>
    <div><dt>제안</dt><dd>{_count_text(run["proposal_count"])}</dd></div>
    <div><dt>반영</dt><dd>{_count_text(run["applied_count"])}</dd></div>
    <div><dt>실패</dt><dd>{_count_text(run["apply_failed_count"])}</dd></div>
  </dl>
</div>
"""


def _run_item_board(rows: list[sqlite3.Row], timezone_name: str) -> str:
    if not rows:
        return """
<div class="run-board-empty">
  <strong>표시할 제안/반영 항목이 없습니다.</strong>
  <span>최근 실행에서 새 항목이 생기면 이곳에 게시됩니다.</span>
</div>
"""
    return f"""
<div class="run-board" role="list">
  <div class="run-board-head">
    <span>구분</span>
    <span>페이지</span>
    <span>내용</span>
    <span>상태</span>
    <span>시각</span>
  </div>
  {"".join(_run_item_row(row, timezone_name) for row in rows)}
</div>
"""


def _run_item_row(row: sqlite3.Row, timezone_name: str) -> str:
    kind = row["item_kind"] or "proposal"
    kind_label = "반영" if kind == "applied" else "제안"
    page_id = row["source_page_id"] or ""
    title = row["source_title"] or f"페이지 {_short_page_id(page_id)}"
    issue = ISSUE_LABELS.get(row["issue_type"] or "", row["issue_type"] or "항목")
    apply = APPLY_LABELS.get(row["apply_mode"] or "", row["apply_mode"] or "")
    detail = " · ".join(part for part in [issue, apply] if part)
    status = row["status"] or ("반영됨" if kind == "applied" else "-")
    href = _notion_page_url(row["notion_proposal_page_id"]) or _notion_page_url(page_id)
    tag_cls = "applied" if kind == "applied" else "proposal"
    inner = f"""
  <span class="run-item-kind {tag_cls}">{_escape(kind_label)}</span>
  <span class="run-item-title"><strong>{_escape(title)}</strong><small>{_escape(_short_page_id(page_id))}</small></span>
  <span class="run-item-detail">{_escape(detail)}</span>
  <span class="run-item-status">{_escape(status)}</span>
  <span class="run-item-time">{_relative_time_html(row["happened_at"], timezone_name)}</span>
"""
    if href:
        return f'<a class="run-board-row" role="listitem" href="{_escape(href)}" target="_blank" rel="noreferrer">{inner}</a>'
    return f'<div class="run-board-row" role="listitem">{inner}</div>'


def _count_text(value: object) -> str:
    return f"{_coerce_int(value)}건"


def _run_duration_text(started_at: str | None, finished_at: str | None) -> str:
    start = parse_iso(started_at)
    finish = parse_iso(finished_at)
    if not start:
        return ""
    if not finish:
        return "진행 중"
    seconds = max(0, int((finish - start).total_seconds()))
    if seconds >= 3600:
        return f"{seconds // 3600}시간 {(seconds % 3600) // 60}분"
    if seconds >= 60:
        return f"{seconds // 60}분 {seconds % 60}초"
    return f"{seconds}초"


def _relative_time_html(value: str | None, timezone_name: str) -> str:
    parsed = parse_iso(value)
    if not parsed:
        return '<span class="muted">-</span>'
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    local = parsed.astimezone(tz)
    now = datetime.now(tz)
    seconds = int((local - now).total_seconds())
    label = _relative_time_label(seconds)
    title = local.strftime("%Y.%m.%d %H:%M")
    return f'<time datetime="{_escape(value or parsed.isoformat())}" title="{_escape(title)}">{_escape(label)}</time>'


def _relative_time_label(seconds: int) -> str:
    future = seconds > 0
    amount = abs(seconds)
    if amount < 60:
        return "잠시 후" if future else "방금 전"
    minutes = amount // 60
    if minutes < 60:
        return f"{minutes}분 {'후' if future else '전'}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}시간 {'후' if future else '전'}"
    days = hours // 24
    return f"{days}일 {'후' if future else '전'}"


def _notion_page_url(page_id: str | None) -> str:
    if not page_id:
        return ""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _short_page_id(page_id: str) -> str:
    if not page_id:
        return "-"
    compact = page_id.replace("-", "")
    return compact[-8:] if len(compact) > 8 else compact


def run_summary(run: sqlite3.Row | None) -> str:
    if not run:
        return '<p class="empty">아직 실행 기록이 없습니다.</p>'
    return f"""
<dl class="kv">
  <dt>상태</dt><dd><span class="pill">{_escape(run["status"])}</span></dd>
  <dt>시작</dt><dd>{_escape(run["started_at"])}</dd>
  <dt>제안</dt><dd>{_escape(run["proposal_count"])}</dd>
  <dt>반영</dt><dd>{_escape(run["applied_count"])} / 실패 {_escape(run["apply_failed_count"])}</dd>
</dl>
"""


def settings_page(
    settings: sqlite3.Row,
    connection: sqlite3.Row,
    targets: list[sqlite3.Row],
    notice: str | None = None,
    openrouter_configured: bool = False,
    openrouter_model: str = "",
    selected_section: str = "pages",
) -> str:
    rows = "".join(target_row(target) for target in targets) or '<tr><td colspan="7" class="empty">등록된 대상이 없습니다.</td></tr>'
    current = "notifications" if selected_section == "notifications" else "pages"
    settings_view = (
        _settings_notifications_view(settings, connection)
        if current == "notifications"
        else _settings_pages_view(connection, rows)
    )
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">필요한 설정만 열어 둡니다</p>
    <h1>설정</h1>
  </div>
</section>

<nav class="settings-switch" aria-label="설정 섹션">
  <a class="{"active" if current == "pages" else ""}" href="/settings?section=pages">페이지</a>
  <a class="{"active" if current == "notifications" else ""}" href="/settings?section=notifications">알림</a>
</nav>

{settings_view}
"""
    return page("설정", body, "settings", notice)


def _settings_pages_view(connection: sqlite3.Row, rows: str) -> str:
    return f"""
<section class="panel settings-section settings-view" id="pages">
  <div class="panel-head"><h2>페이지 설정</h2></div>
  <div class="settings-connection-line">
    <span>Notion</span>
    <strong>{_escape(connection["notion_workspace_name"] or connection["notion_workspace_id"] or "연결 대기")}</strong>
    {status_pill(bool(connection["notion_access_token_encrypted"]))}
    <div class="settings-inline-actions">
      <a class="button primary" href="/auth/notion/start?next=/settings?section=pages">연결</a>
      <form method="post" action="/account/disconnect/notion"><button class="ghost" type="submit">로그아웃</button></form>
    </div>
  </div>
  <form class="form-grid target-picker-form" method="post" action="/targets" data-target-form>
    <input type="hidden" name="return_to" value="/settings?section=pages">
    {target_picker_fields()}
    <label>제외 페이지 ID
      <input name="excluded_page_ids" placeholder="쉼표로 구분">
    </label>
    <button class="primary" type="submit">대상 추가</button>
  </form>
  <div class="table-wrap settings-table">
    <table>
      <thead><tr><th>제목</th><th>유형</th><th>URL</th><th>하위</th><th>제외</th><th>마지막 결과</th><th></th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""


def _settings_notifications_view(settings: sqlite3.Row, connection: sqlite3.Row) -> str:
    return f"""
<section class="panel settings-section settings-view" id="notifications">
  <div class="panel-head"><h2>알림 설정</h2></div>
  <form class="form-grid" method="post" action="/notifications">
    <input type="hidden" name="return_to" value="/settings?section=notifications">
    <input type="hidden" name="default_channel" value="email">
    <input type="hidden" name="notify_zero" value="1">
    <label>야간 점검 시각
      <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}" required>
    </label>
    <label>아침 알림 시각
      <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}" required>
    </label>
    <label>타임존
      <input name="timezone" value="{_escape(settings["timezone"])}" required>
    </label>
    <button class="primary" type="submit">저장</button>
  </form>
  <div class="settings-divider"></div>
  <div class="settings-email-line">
    <span>이메일</span>
    <strong>{_escape(connection["notification_email"] or "수신 주소")}</strong>
    {status_pill(bool(connection["notification_email_verified"]))}
  </div>
  <div class="settings-email-forms">
    <form class="inline-form" method="post" action="/settings/email">
      <input type="hidden" name="return_to" value="/settings?section=notifications">
      <label>수신 주소
        <input name="email" type="email" value="{_escape(connection["notification_email"] or "")}">
      </label>
      <button type="submit">코드 발송</button>
    </form>
    <form class="inline-form" method="post" action="/settings/email/verify">
      <input type="hidden" name="return_to" value="/settings?section=notifications">
      <label>인증 코드
        <input name="code" inputmode="numeric">
      </label>
      <button type="submit">인증</button>
    </form>
  </div>
</section>
"""


def targets_page(targets: list[sqlite3.Row], notice: str | None = None) -> str:
    rows = "".join(target_row(target) for target in targets) or '<tr><td colspan="7" class="empty">등록된 대상이 없습니다.</td></tr>'
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">선택 범위와 제외 목록</p>
    <h1>점검 대상</h1>
  </div>
</section>

<section class="panel">
  <form class="form-grid target-picker-form" method="post" action="/targets" data-target-form>
    {target_picker_fields()}
    <label>제외 페이지 ID
      <input name="excluded_page_ids" placeholder="쉼표로 구분">
    </label>
    <button class="primary" type="submit">대상 추가</button>
  </form>
</section>

<section class="panel">
  <div class="table-wrap">
    <table>
      <thead><tr><th>제목</th><th>유형</th><th>URL</th><th>하위</th><th>제외</th><th>마지막 결과</th><th></th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""
    return page("점검 대상", body, "targets", notice)


def target_picker_fields() -> str:
    return """
    <div class="target-picker" data-target-picker>
      <div class="target-search-row">
        <label>검색 범위
          <select data-target-type>
            <option value="">전체</option>
            <option value="page">페이지</option>
            <option value="database">데이터베이스</option>
          </select>
        </label>
        <label>Notion 검색
          <input type="search" data-target-query placeholder="제목 검색">
        </label>
        <button type="button" data-target-search>불러오기</button>
      </div>
      <div class="target-picker-status" data-target-status></div>
      <div class="target-result-list" data-target-results></div>
      <div class="target-selection" data-target-selection hidden></div>
      <input type="hidden" name="notion_object_id">
      <input type="hidden" name="notion_object_type">
      <input type="hidden" name="title">
      <input type="hidden" name="url">
      <input type="hidden" name="include_children" value="1">
    </div>
"""


def target_row(target: sqlite3.Row) -> str:
    excluded = len(_split_excluded(target["excluded_page_ids"]))
    url = target["url"] or ""
    link = f'<a href="{_escape(url)}" target="_blank" rel="noreferrer">열기</a>' if url else "-"
    return f"""
<tr>
  <td><strong>{_escape(target["title"])}</strong><small>{_escape(target["notion_object_id"])}</small></td>
  <td>{_escape(target["notion_object_type"])}</td>
  <td>{link}</td>
  <td>{'포함' if target["include_children"] else '제외'}</td>
  <td>{excluded}</td>
  <td>{_escape(target["last_result"] or "-")}</td>
  <td>
    <form method="post" action="/targets/{target['id']}/delete">
      <button class="ghost" type="submit">삭제</button>
    </form>
  </td>
</tr>
"""


def _split_excluded(value: str | None) -> list[str]:
    if not value:
        return []
    if value.startswith("["):
        import json

        try:
            decoded = json.loads(value)
            return [str(item) for item in decoded if str(item).strip()]
        except Exception:
            return []
    return [part.strip() for part in value.split(",") if part.strip()]


def notifications_page(settings: sqlite3.Row, connection: sqlite3.Row, notice: str | None = None) -> str:
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">이메일 알림</p>
    <h1>알림 설정</h1>
  </div>
</section>

<section class="panel">
  <form class="form-grid" method="post" action="/notifications">
    <input type="hidden" name="default_channel" value="email">
    <input type="hidden" name="notify_zero" value="1">
    <label>야간 점검 시각
      <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}" required>
    </label>
    <label>아침 알림 시각
      <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}" required>
    </label>
    <label>타임존
      <input name="timezone" value="{_escape(settings["timezone"])}" required>
    </label>
    <button class="primary" type="submit">저장</button>
  </form>
</section>

<section class="columns">
  <div class="panel">
    <div class="panel-head"><h2>이메일</h2>{status_pill(bool(connection["notification_email_verified"]))}</div>
    <form class="stack" method="post" action="/settings/email">
      <label>수신 주소
        <input name="email" type="email" value="{_escape(connection["notification_email"] or "")}">
      </label>
      <button type="submit">인증 코드 발송</button>
    </form>
    <form class="stack compact" method="post" action="/settings/email/verify">
      <label>인증 코드
        <input name="code" inputmode="numeric">
      </label>
      <button type="submit">인증 완료</button>
    </form>
  </div>
</section>
"""
    return page("알림 설정", body, "notifications", notice)


def runs_page(
    runs: list[sqlite3.Row],
    logs: list[sqlite3.Row],
    notice: str | None = None,
    timezone_name: str = "Asia/Seoul",
    selected_limit: int = 20,
) -> str:
    current_limit = selected_limit if selected_limit in {20, 50, 100} else 20
    limit_options = "".join(
        f'<option value="{limit}" {"selected" if limit == current_limit else ""}>최근 {limit}개</option>'
        for limit in (20, 50, 100)
    )
    run_rows = "".join(run_row(run, timezone_name) for run in runs) or '<tr><td colspan="7" class="empty">실행 기록이 없습니다.</td></tr>'
    log_rows = "".join(log_row(log, timezone_name) for log in logs) or '<tr><td colspan="3" class="empty">감사 로그가 없습니다.</td></tr>'
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Agent run과 외부 호출 내역</p>
    <h1>실행 로그</h1>
  </div>
  <div class="actions">
    <form class="limit-form" method="get" action="/runs">
      <label>표시
        <select name="limit">{limit_options}</select>
      </label>
      <button type="submit">보기</button>
    </form>
    <form method="post" action="/runs/manual">
      <input type="hidden" name="return_to" value="/runs?limit={current_limit}">
      <button class="primary" type="submit">수동 점검 실행</button>
    </form>
  </div>
</section>

<section class="panel">
  <div class="panel-head"><h2>최근 실행</h2><span class="panel-count">최근 {current_limit}개</span></div>
  <div class="table-wrap">
    <table class="run-table">
      <thead><tr><th>Run</th><th>상태</th><th>시간</th><th>페이지</th><th>제안</th><th>반영</th><th>알림</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </div>
</section>

<section class="panel">
  <div class="panel-head"><h2>감사 로그</h2><span class="panel-count">최근 {current_limit}개</span></div>
  <div class="table-wrap">
    <table class="audit-table">
      <thead><tr><th>시각</th><th>이벤트</th><th>내용</th></tr></thead>
      <tbody>{log_rows}</tbody>
    </table>
  </div>
</section>
"""
    return page("로그", body, "logs", notice)


def run_row(run: sqlite3.Row, timezone_name: str) -> str:
    run_id = run["run_id"] or ""
    status = run["status"] or ""
    proposal_detail = _count_group(
        [
            ("전체", run["proposal_count"]),
            ("오류", run["error_count"]),
            ("누락", run["omission_count"]),
            ("모순", run["contradiction_count"]),
            ("보류", run["held_count"]),
        ]
    )
    page_detail = _count_group([("스캔", run["scanned_page_count"]), ("변경", run["changed_page_count"])])
    applied = int(run["applied_count"] or 0)
    failed = int(run["apply_failed_count"] or 0)
    agent_errors = _run_agent_error_count(run)
    failure_parts = []
    if failed:
        failure_parts.append(f"반영 실패 {failed}건")
    if agent_errors:
        failure_parts.append(f"점검 오류 {agent_errors}건")
    if failure_parts:
        error_title = _run_error_title(run)
        title_attr = f' title="{_escape(error_title)}"' if error_title else ""
        apply_html = f'<strong>{applied}건</strong><small class="run-error-text"{title_attr}>{" · ".join(failure_parts)}</small>'
    else:
        apply_html = f'<strong>{applied}건</strong><small>실패 없음</small>'
    return f"""
<tr>
  <td class="run-id-cell"><code title="{_escape(run_id)}">{_escape(_short_run_id(run_id))}</code></td>
  <td>{_status_badge(status)}</td>
  <td>{_time_range(run["started_at"], run["finished_at"], timezone_name)}</td>
  <td>{page_detail}</td>
  <td>{proposal_detail}</td>
  <td>{apply_html}</td>
  <td>{_escape(run["notification_status"] or "-")}</td>
</tr>
"""


def log_row(log: sqlite3.Row, timezone_name: str) -> str:
    event = log["event"] or ""
    level = log["level"] or "info"
    return f"""
<tr>
  <td>{_datetime_html(log["created_at"], timezone_name)}</td>
  <td class="event-cell">{_event_badge(event, level)}<small>{_escape(event)}</small></td>
  <td class="log-summary">{_escape(_payload_summary(log["payload"]))}</td>
</tr>
"""


def _status_badge(status: str) -> str:
    label = STATUS_LABELS.get(status, status or "-")
    return f'<span class="pill status-{_escape(status or "unknown")}">{_escape(label)}</span>'


def _event_badge(event: str, level: str) -> str:
    label = EVENT_LABELS.get(event, event.replace("_", " ") if event else "-")
    return f'<span class="event-badge level-{_escape(level or "info")}">{_escape(label)}</span>'


def _datetime_html(value: str | None, timezone_name: str) -> str:
    parsed = parse_iso(value)
    if not parsed:
        return '<span class="muted">-</span>'
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    local = parsed.astimezone(tz)
    return (
        f'<time datetime="{_escape(value)}">'
        f'<strong>{_escape(local.strftime("%m.%d"))}</strong>'
        f'<small>{_escape(local.strftime("%H:%M"))}</small>'
        "</time>"
    )


def _time_range(started_at: str | None, finished_at: str | None, timezone_name: str) -> str:
    start = _datetime_html(started_at, timezone_name)
    if not finished_at:
        return f'<div class="time-range">{start}<span>진행 중</span></div>'
    end = parse_iso(finished_at)
    start_dt = parse_iso(started_at)
    duration = ""
    if start_dt and end:
        seconds = max(0, int((end - start_dt).total_seconds()))
        if seconds >= 60:
            duration = f"{seconds // 60}분 {seconds % 60}초"
        else:
            duration = f"{seconds}초"
    return f'<div class="time-range">{start}<span>{_escape(duration)}</span></div>'


def _count_group(items: list[tuple[str, object]]) -> str:
    parts = []
    for label, value in items:
        count = _coerce_int(value)
        if label != "전체" and count == 0:
            continue
        parts.append(f"<span><b>{_escape(label)}</b>{count}</span>")
    return f'<div class="count-group">{"".join(parts) or "<span><b>-</b>0</span>"}</div>'


def _run_agent_error_count(run: sqlite3.Row) -> int:
    logged_errors = _coerce_int(_optional_row_value(run, "agent_error_count", 0))
    message = str(run["error_message"] or "").strip()
    message_errors = len([line for line in message.splitlines() if line.strip()])
    if logged_errors or message_errors:
        return max(logged_errors, message_errors)
    return 1 if (run["status"] or "") == "failed" else 0


def _run_error_title(run: sqlite3.Row) -> str:
    message = str(run["error_message"] or "").strip()
    if message:
        return _truncate(message, 240)
    if (run["status"] or "") == "failed":
        return "Agent 실행 중 오류가 발생했습니다."
    return ""


def _optional_row_value(row: object, key: str, default: object = None) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return row.get(key, default)
        return default


def _coerce_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _short_run_id(run_id: str) -> str:
    if not run_id:
        return "-"
    return run_id[-8:] if len(run_id) > 12 else run_id


def _payload_summary(payload: str | None) -> str:
    if not payload:
        return "-"
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return _truncate(payload)
    if isinstance(decoded, list):
        if not decoded:
            return "0건"
        first = decoded[0]
        if isinstance(first, dict):
            reason = first.get("reason") or first.get("error")
            suffix = f" · {reason}" if reason else ""
            return _truncate(f"{len(decoded)}건{suffix}")
        return _truncate(f"{len(decoded)}건 · {_short_value(first)}")
    if isinstance(decoded, dict):
        if decoded.get("action") and decoded.get("status"):
            details = []
            for key in ["page_id", "title", "blocks", "queries", "proposals", "accepted", "held", "rejected", "written", "failed", "status"]:
                if key in decoded and key != "action":
                    details.append(f"{key}: {_short_value(decoded[key])}")
            suffix = f" · {' · '.join(details[:4])}" if details else ""
            return _truncate(f'{decoded["action"]}{suffix}')
        if decoded.get("title") and decoded.get("error"):
            return _truncate(f'{decoded["title"]}: {decoded["error"]}')
        if decoded.get("error"):
            return _truncate(f'오류: {decoded["error"]}')
        if decoded.get("status"):
            return _truncate(f'상태: {decoded["status"]}')
        if "manual" in decoded:
            mode = "수동 실행" if decoded.get("manual") else "예약 실행"
            plan = decoded.get("plan")
            step_count = len(plan) if isinstance(plan, list) else 0
            return f"{mode} · {step_count}단계"
        if decoded.get("workspace_id"):
            return _truncate(f'워크스페이스: {decoded["workspace_id"]}')
        if decoded.get("to") and decoded.get("subject"):
            return _truncate(f'{decoded["to"]} · {decoded["subject"]}')
        pairs = [f"{key}: {_short_value(value)}" for key, value in list(decoded.items())[:4]]
        return _truncate(" · ".join(pairs))
    return _truncate(_short_value(decoded))


def _short_value(value: object) -> str:
    if isinstance(value, list):
        return f"{len(value)}건"
    if isinstance(value, dict):
        return ", ".join(f"{key}={_short_value(item)}" for key, item in list(value.items())[:3])
    return str(value)


def _truncate(value: str, limit: int = 140) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"
