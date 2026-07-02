from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.security import mask_secret
from app.time_utils import parse_iso

ASSET_VERSION = "20260702d"


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


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, active: str = "home", notice: str | None = None) -> str:
    nav = [
        ("home", "/dashboard", "홈"),
        ("logs", "/runs", "로그"),
        ("settings", "/settings", "설정"),
    ]
    nav_html = "".join(
        f'<a class="nav-link {"active" if key == active else ""}" href="{href}"><span>{label}</span></a>'
        for key, href, label in nav
    )
    notice_html = f'<div class="notice">{_escape(notice)}</div>' if notice else ""
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
  <script src="/static/target-picker.js?v={ASSET_VERSION}"></script>
</body>
</html>"""


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
          <p class="step-lede">SMTP 발송 설정은 서버 환경변수로 관리하고, 사용자는 받을 이메일만 인증합니다.</p>
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
  <label>점검 <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}"></label>
  <label>알림 <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}"></label>
  <label>타임존 <input name="timezone" value="{_escape(settings["timezone"])}"></label>
  <label class="checkline"><input type="checkbox" name="notify_zero" checked><span>0건 알림</span></label>
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
<p class="setup-note">{_escape(connection["notification_email"])} 주소로 보낸 코드를 입력하면 시간 설정이 열립니다.</p>
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
    improvements: list[sqlite3.Row],
    notice: str | None = None,
) -> str:
    latest_run = runs[0] if runs else None
    timezone_name = settings["timezone"] or "Asia/Seoul"
    inbox_url = connection["notion_inbox_url"] or ""
    inbox_action = (
        f'<a class="button" href="{_escape(inbox_url)}" target="_blank" rel="noreferrer">수정사항 확인</a>'
        if inbox_url
        else '<a class="button" href="/settings#account">수정함 연결 확인</a>'
    )
    body = f"""
<section class="page-head home-head">
  <div>
    <p class="eyebrow">오늘 확인할 수정사항</p>
    <h1>홈</h1>
  </div>
  <div class="actions">
    <form method="post" action="/runs/manual">
      <input type="hidden" name="return_to" value="/dashboard">
      <button class="primary" type="submit">수동 점검 실행</button>
    </form>
    <form method="post" action="/apply-approved">
      <input type="hidden" name="return_to" value="/dashboard">
      <button type="submit">승인 항목 반영</button>
    </form>
  </div>
</section>

<section class="home-grid">
  <div class="panel home-card">
    <div class="panel-head"><h2>최근 실행</h2><a href="/runs">로그 보기</a></div>
    {_home_run_summary(latest_run, timezone_name)}
  </div>
  <div class="panel home-card">
    <div class="panel-head"><h2>다음 실행</h2><a href="/settings#notifications">시간 설정</a></div>
    {_next_schedule_summary(settings)}
  </div>
</section>

<section class="panel improvement-panel">
  <div class="panel-head">
    <h2>보완 필요 페이지</h2>
    <div class="actions">{inbox_action}</div>
  </div>
  {_improvement_list(improvements, inbox_url, timezone_name)}
</section>
"""
    return page("홈", body, "home", notice)


def _home_run_summary(run: sqlite3.Row | None, timezone_name: str) -> str:
    if not run:
        return '<p class="empty">아직 실행 기록이 없습니다.</p>'
    finished = _datetime_html(run["finished_at"] or run["started_at"], timezone_name)
    return f"""
<div class="home-summary">
  <div>
    <span>상태</span>
    {_status_badge(run["status"] or "")}
  </div>
  <div>
    <span>실행 시각</span>
    {finished}
  </div>
  <div>
    <span>제안</span>
    <strong>{_escape(run["proposal_count"])}</strong>
  </div>
  <div>
    <span>반영</span>
    <strong>{_escape(run["applied_count"])}</strong><small>실패 {_escape(run["apply_failed_count"])}</small>
  </div>
</div>
"""


def _next_schedule_summary(settings: sqlite3.Row) -> str:
    timezone_name = settings["timezone"] or "Asia/Seoul"
    scan = _next_scheduled_time(settings["scan_time"], timezone_name)
    notify = _next_scheduled_time(settings["notify_time"], timezone_name)
    return f"""
<div class="home-summary">
  <div>
    <span>다음 점검</span>
    {_scheduled_time_html(scan)}
  </div>
  <div>
    <span>아침 알림</span>
    {_scheduled_time_html(notify)}
  </div>
  <div>
    <span>타임존</span>
    <strong>{_escape(timezone_name)}</strong>
  </div>
  <div>
    <span>0건 알림</span>
    <strong>{'ON' if settings["notify_zero"] else 'OFF'}</strong>
  </div>
</div>
"""


def _next_scheduled_time(time_value: str | None, timezone_name: str) -> datetime | None:
    if not time_value:
        return None
    try:
        hour_text, minute_text = time_value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError):
        return None
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _scheduled_time_html(value: datetime | None) -> str:
    if value is None:
        return '<span class="muted">-</span>'
    return (
        f'<time datetime="{_escape(value.isoformat())}">'
        f'<strong>{_escape(value.strftime("%m.%d"))}</strong>'
        f'<small>{_escape(value.strftime("%H:%M"))}</small>'
        "</time>"
    )


def _improvement_list(rows: list[sqlite3.Row], inbox_url: str, timezone_name: str) -> str:
    if not rows:
        return """
<div class="empty-state">
  <strong>확인할 수정사항이 없습니다.</strong>
  <span>다음 점검 후 보완이 필요한 페이지가 이곳에 모입니다.</span>
</div>
"""
    return f'<div class="improvement-list">{"".join(_improvement_item(row, inbox_url, timezone_name) for row in rows)}</div>'


def _improvement_item(row: sqlite3.Row, inbox_url: str, timezone_name: str) -> str:
    page_id = row["source_page_id"] or ""
    title = row["source_title"] or f"페이지 {_short_page_id(page_id)}"
    proposal_url = _notion_page_url(row["notion_proposal_page_id"]) or inbox_url
    review_link = (
        f'<a class="button" href="{_escape(proposal_url)}" target="_blank" rel="noreferrer">확인</a>'
        if proposal_url
        else '<a class="button" href="/settings#account">수정함 연결</a>'
    )
    apply_action = ""
    if _coerce_int(row["approved_count"]):
        apply_action = """
<form method="post" action="/apply-approved">
  <input type="hidden" name="return_to" value="/dashboard">
  <button class="primary" type="submit">반영</button>
</form>
"""
    return f"""
<article class="improvement-item">
  <div>
    <strong>{_escape(title)}</strong>
    <small>{_escape(_short_page_id(page_id))}</small>
  </div>
  <div class="improvement-time"><span>최근 제안</span>{_datetime_html(row["latest_created_at"], timezone_name)}</div>
  <div class="issue-strip">{_issue_counts(row)}</div>
  <div class="proposal-state">{_proposal_state(row)}</div>
  <div class="actions">{review_link}{apply_action}</div>
</article>
"""


def _issue_counts(row: sqlite3.Row) -> str:
    parts = []
    for key, label in [("error_count", "오류"), ("omission_count", "누락"), ("contradiction_count", "모순")]:
        count = _coerce_int(row[key])
        if count:
            parts.append(f"<span>{_escape(label)} {_escape(count)}</span>")
    return "".join(parts) or "<span>제안 없음</span>"


def _proposal_state(row: sqlite3.Row) -> str:
    pending = _coerce_int(row["pending_count"])
    approved = _coerce_int(row["approved_count"])
    failed = _coerce_int(row["failed_count"])
    total = _coerce_int(row["proposal_count"])
    parts = [f"전체 {total}"]
    if pending:
        parts.append(f"확인 {pending}")
    if approved:
        parts.append(f"승인 {approved}")
    if failed:
        parts.append(f"실패 {failed}")
    return " · ".join(parts)


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
) -> str:
    rows = "".join(target_row(target) for target in targets) or '<tr><td colspan="7" class="empty">등록된 대상이 없습니다.</td></tr>'
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">점검 범위와 알림, 계정 연결</p>
    <h1>설정</h1>
  </div>
</section>

<nav class="settings-tabs" aria-label="설정 섹션">
  <a href="#pages">페이지</a>
  <a href="#notifications">알림</a>
  <a href="#account">계정/API</a>
</nav>

<section class="panel settings-section" id="pages">
  <div class="panel-head"><h2>페이지 설정</h2></div>
  <form class="form-grid target-picker-form" method="post" action="/targets" data-target-form>
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

<section class="panel settings-section" id="notifications">
  <div class="panel-head"><h2>알림 설정</h2></div>
  <form class="form-grid" method="post" action="/notifications">
    <input type="hidden" name="default_channel" value="email">
    <label>야간 점검 시각
      <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}" required>
    </label>
    <label>아침 알림 시각
      <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}" required>
    </label>
    <label>타임존
      <input name="timezone" value="{_escape(settings["timezone"])}" required>
    </label>
    <label class="checkline">
      <input type="checkbox" name="notify_zero" {'checked' if settings["notify_zero"] else ''}>
      <span>0건 알림 발송</span>
    </label>
    <button class="primary" type="submit">저장</button>
  </form>
  <div class="settings-duo single">
    <div class="settings-subpanel">
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
  </div>
</section>

<section class="panel settings-section" id="account">
  <div class="panel-head"><h2>계정/API</h2></div>
  <div class="settings-duo">
    <div class="settings-subpanel">
      <div class="panel-head"><h2>Notion</h2>{status_pill(bool(connection["notion_access_token_encrypted"]))}</div>
      <dl class="kv">
        <dt>워크스페이스</dt><dd>{_escape(connection["notion_workspace_name"] or connection["notion_workspace_id"] or "-")}</dd>
        <dt>수정함</dt><dd>{_escape(connection["notion_inbox_url"] or connection["notion_inbox_database_id"] or "-")}</dd>
      </dl>
      <div class="actions">
        <a class="button primary" href="/auth/notion/start?next=/settings">Notion 연결</a>
        <form method="post" action="/account/disconnect/notion"><button class="ghost" type="submit">연결 해제</button></form>
      </div>
    </div>
    <div class="settings-subpanel">
      <div class="panel-head"><h2>OpenRouter</h2>{status_pill(openrouter_configured, "서버 설정", "필요")}</div>
      <dl class="kv">
        <dt>API 키</dt><dd>환경변수 <code>OPENROUTER_API_KEY</code></dd>
        <dt>모델</dt><dd>{_escape(openrouter_model or "OPENROUTER_DEFAULT_MODEL")}</dd>
      </dl>
    </div>
  </div>
  <form class="settings-danger" method="post" action="/account/delete-local-data">
    <button class="danger-button" type="submit">로컬 데이터 삭제</button>
  </form>
</section>
"""
    return page("설정", body, "settings", notice)


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
    <p class="eyebrow">SMTP 이메일 발송 방식</p>
    <h1>알림 설정</h1>
  </div>
</section>

<section class="panel">
  <form class="form-grid" method="post" action="/notifications">
    <input type="hidden" name="default_channel" value="email">
    <label>야간 점검 시각
      <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}" required>
    </label>
    <label>아침 알림 시각
      <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}" required>
    </label>
    <label>타임존
      <input name="timezone" value="{_escape(settings["timezone"])}" required>
    </label>
    <label class="checkline">
      <input type="checkbox" name="notify_zero" {'checked' if settings["notify_zero"] else ''}>
      <span>0건 알림 발송</span>
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


def runs_page(runs: list[sqlite3.Row], logs: list[sqlite3.Row], notice: str | None = None, timezone_name: str = "Asia/Seoul") -> str:
    run_rows = "".join(run_row(run, timezone_name) for run in runs) or '<tr><td colspan="7" class="empty">실행 기록이 없습니다.</td></tr>'
    log_rows = "".join(log_row(log, timezone_name) for log in logs) or '<tr><td colspan="3" class="empty">감사 로그가 없습니다.</td></tr>'
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Agent run과 외부 호출 내역</p>
    <h1>실행 로그</h1>
  </div>
  <div class="actions">
    <form method="post" action="/apply-approved">
      <input type="hidden" name="return_to" value="/runs">
      <button type="submit">승인 항목 반영</button>
    </form>
    <form method="post" action="/runs/manual">
      <input type="hidden" name="return_to" value="/runs">
      <button class="primary" type="submit">수동 점검 실행</button>
    </form>
  </div>
</section>

<section class="panel">
  <div class="panel-head"><h2>최근 실행</h2></div>
  <div class="table-wrap">
    <table class="run-table">
      <thead><tr><th>Run</th><th>상태</th><th>시간</th><th>페이지</th><th>제안</th><th>반영</th><th>알림</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </div>
</section>

<section class="panel">
  <div class="panel-head"><h2>감사 로그</h2></div>
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
    apply_html = f'<strong>{applied}</strong><small>실패 {failed}</small>' if failed else f'<strong>{applied}</strong><small>실패 없음</small>'
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


def account_page(connection: sqlite3.Row, notice: str | None = None) -> str:
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">토큰과 API 키 관리</p>
    <h1>계정/API 키</h1>
  </div>
</section>

<section class="columns">
  <div class="panel">
    <div class="panel-head"><h2>Notion</h2>{status_pill(bool(connection["notion_access_token_encrypted"]))}</div>
    <dl class="kv">
      <dt>워크스페이스</dt><dd>{_escape(connection["notion_workspace_name"] or connection["notion_workspace_id"] or "-")}</dd>
      <dt>수정함</dt><dd>{_escape(connection["notion_inbox_url"] or connection["notion_inbox_database_id"] or "-")}</dd>
    </dl>
    <div class="actions">
      <a class="button primary" href="/auth/notion/start">Notion 연결</a>
      <form method="post" action="/account/disconnect/notion"><button class="ghost" type="submit">연결 해제</button></form>
    </div>
  </div>
  <div class="panel">
    <div class="panel-head"><h2>OpenRouter</h2>{status_pill(True, "서버 설정", "필요")}</div>
    <dl class="kv">
      <dt>API 키</dt><dd>환경변수 <code>OPENROUTER_API_KEY</code></dd>
    </dl>
  </div>
</section>

<section class="panel danger">
  <div class="panel-head"><h2>데이터</h2></div>
  <form method="post" action="/account/delete-local-data">
    <button class="danger-button" type="submit">로컬 데이터 삭제</button>
  </form>
</section>
"""
    return page("계정/API 키", body, "account", notice)
