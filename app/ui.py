from __future__ import annotations

import html
import sqlite3

from app.security import mask_secret

ASSET_VERSION = "20260701b"


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, active: str = "overview", notice: str | None = None) -> str:
    nav = [
        ("overview", "/dashboard", "개요"),
        ("targets", "/targets", "점검 대상"),
        ("notifications", "/notifications", "알림"),
        ("runs", "/runs", "실행 로그"),
        ("account", "/account", "계정/API"),
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
    <a class="capsule-action" href="/onboarding" aria-label="튜토리얼">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5l8 7-8 7V5Z"/></svg>
    </a>
  </header>
  <main class="shell app-shell">
    {notice_html}
    {body}
  </main>
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

    <section class="intro-scroll" aria-label="Nocturne 작동 방식">
      <article class="intro-panel">
        <div class="side-photo intro-visual">
          <div class="paper-stack">
            <span></span><span></span><span></span>
          </div>
          <div class="scan-line"></div>
        </div>
        <div>
          <p class="kicker">night scan</p>
          <h2>문서가 잠든 시간에만 조용히 읽습니다.</h2>
          <p>Nocturne는 선택한 페이지와 하위 페이지를 기준으로 최근 수정된 내용만 점검합니다.</p>
        </div>
      </article>
      <article class="intro-panel reverse">
        <div class="approval-demo" aria-hidden="true">
          <span>대기</span>
          <strong>문장 위치 · 근거 · 제안문</strong>
          <span>승인</span>
          <strong>원문 일부만 반영</strong>
        </div>
        <div>
          <p class="kicker">review boundary</p>
          <h2>원문 앞에는 항상 승인 경계가 있습니다.</h2>
          <p>agent가 제안을 만들 수는 있지만, 사용자가 수정함에서 승인하기 전까지 원문은 그대로 유지됩니다.</p>
        </div>
      </article>
      <article class="intro-panel">
        <div class="brief-demo" aria-hidden="true">
          <div><span>08:00</span><strong>문제 없음</strong></div>
          <div><span>3건</span><strong>오류 1 · 누락 2</strong></div>
          <div><span>수정함</span><strong>승인 대기</strong></div>
        </div>
        <div>
          <p class="kicker">morning brief</p>
          <h2>아침에는 Slack이나 메일로 짧게 받습니다.</h2>
          <p>0건이어도 “문제 없음” 알림을 보낼 수 있어, 매일의 점검 루프가 끊기지 않습니다.</p>
        </div>
      </article>
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
    openrouter_connected = bool(connection["openrouter_api_key_encrypted"])
    slack_connected = bool(connection["slack_webhook_url_encrypted"])
    email_connected = bool(connection["notification_email_verified"])
    channels_connected = slack_connected or email_connected
    has_targets = bool(targets)
    max_allowed = 0
    if notion_connected:
        max_allowed = 1
    if notion_connected and review_acknowledged:
        max_allowed = 2
    if notion_connected and review_acknowledged and openrouter_connected:
        max_allowed = 3
    if notion_connected and review_acknowledged and openrouter_connected and has_targets:
        max_allowed = 4
    if notion_connected and review_acknowledged and openrouter_connected and has_targets and channels_connected:
        max_allowed = 5
    setup_score = max_allowed
    safe_step = max(0, min(step, max_allowed, 5))
    notice_html = f'<div class="tutorial-notice">{_escape(notice)}</div>' if notice else ""
    review_done = notion_connected and review_acknowledged
    openrouter_done = review_done and openrouter_connected
    targets_done = openrouter_done and has_targets
    channels_done = targets_done and channels_connected
    rail_items = [
        ("Notion", "M4 12h16M12 4v16M8 8h8", notion_connected),
        ("수정함", "M5 13l4 4L19 7", review_done),
        ("OpenRouter", "M7 11V7a5 5 0 0 1 10 0v4M6 11h12v9H6z", openrouter_done),
        ("대상", "M12 3l8 4v10l-8 4-8-4V7z", targets_done),
        ("알림", "M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4", channels_done),
        ("실행", "M8 5l10 7-10 7z", max_allowed == 5),
    ]
    rail_html = "".join(
        f'<button type="button" class="tutorial-dot {"done" if done else ""} {"locked" if index > max_allowed else ""}" '
        f'data-step-target="{index}" {"disabled" if index > max_allowed else ""} aria-label="{label}">'
        f'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="{path}"/></svg><span>{label}</span></button>'
        for index, (label, path, done) in enumerate(rail_items)
    )
    target_rows = "".join(
        f'<li><strong>{_escape(target["title"])}</strong><span>{_escape(target["notion_object_type"])} · {"하위 포함" if target["include_children"] else "단일 대상"}</span></li>'
        for target in targets[:4]
    ) or "<li><strong>아직 비어 있음</strong><span>첫 점검 범위를 추가하면 여기에 고정됩니다.</span></li>"
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
  <main class="tutorial" data-start-step="{safe_step}" data-max-step="{max_allowed}">
    <header class="tutorial-head">
      <a class="tutorial-brand" href="/"><img src="/static/nocturne-icon.svg" alt=""><span>Nocturne</span></a>
      <div class="tutorial-score"><strong>{setup_score}/5</strong><span>setup tasks</span></div>
    </header>

    {notice_html}

    <section class="tutorial-card">
      <div class="tutorial-progress">{rail_html}</div>

        <article class="onboarding-step" data-step="0">
          <p class="kicker">01 · workspace</p>
          <h1>먼저 Notion 작업실을 연결합니다.</h1>
          <p class="step-lede">Nocturne의 모든 일은 사용자가 허용한 Notion 범위 안에서만 시작됩니다.</p>
          <div class="connection-hero">
            <div>
              <span>Notion</span>
              <strong>{_escape(connection["notion_workspace_name"] or connection["notion_workspace_id"] or "연결 대기")}</strong>
            </div>
            {status_pill(notion_connected)}
          </div>
          <div class="step-actions">
            <a class="button primary ink-button" href="/auth/notion/start?next=/onboarding?step=1">Notion 연결</a>
          </div>
        </article>

        <article class="onboarding-step" data-step="1">
          {back_button}
          <p class="kicker">02 · review boundary</p>
          <h1>수정은 수정함에서 멈춥니다.</h1>
          <p class="step-lede">agent는 원문 대신 `Nocturne 수정함`에 위치, 근거, 제안문을 쌓습니다.</p>
          <div class="tutorial-board">
            <div><span>대기</span><strong>새 제안 검토</strong></div>
            <div><span>승인</span><strong>다음 실행에서 반영</strong></div>
            <div><span>거절/보류</span><strong>원문 유지</strong></div>
          </div>
          <form class="step-actions" method="post" action="/onboarding/review-boundary">
            <button class="primary ink-button task-button" type="submit">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 13l4 4L19 7"/></svg>
              <span>승인 경계 확인</span>
            </button>
          </form>
        </article>

        <article class="onboarding-step" data-step="2">
          {back_button}
          <p class="kicker">03 · model key</p>
          <h1>판단 비용은 내 OpenRouter 키로 처리합니다.</h1>
          <p class="step-lede">모델은 서비스 기본값을 쓰고, 키 원문은 다시 보여주지 않습니다.</p>
          <form class="onboarding-form" method="post" action="/settings/openrouter">
            <input type="hidden" name="return_to" value="/onboarding?step=3">
            <label>OpenRouter API key
              <input name="api_key" type="password" placeholder="{_escape(mask_secret(None, connection["openrouter_key_last4"]))}">
            </label>
            <button class="primary ink-button" type="submit">저장하고 계속</button>
          </form>
        </article>

        <article class="onboarding-step" data-step="3">
          {back_button}
          <p class="kicker">04 · scope</p>
          <h1>처음 점검할 페이지를 정합니다.</h1>
          <p class="step-lede">선택한 페이지의 하위 페이지는 기본 포함되고, 데이터베이스의 페이지도 점검 범위가 됩니다.</p>
          <form class="onboarding-form compact-form" method="post" action="/targets">
            <input type="hidden" name="return_to" value="/onboarding?step=4">
            <label>유형
              <select name="notion_object_type">
                <option value="page">페이지</option>
                <option value="database">데이터베이스</option>
              </select>
            </label>
            <label>Notion ID
              <input name="notion_object_id" required placeholder="page 또는 database id">
            </label>
            <label>제목
              <input name="title" required placeholder="예: Research Vault">
            </label>
            <label>URL
              <input name="url" placeholder="https://www.notion.so/...">
            </label>
            <label class="checkline">
              <input type="checkbox" name="include_children" checked>
              <span>하위 페이지 포함</span>
            </label>
            <button class="primary ink-button" type="submit">대상 추가</button>
          </form>
          <ul class="target-chip-list">{target_rows}</ul>
        </article>

        <article class="onboarding-step" data-step="4">
          {back_button}
          <p class="kicker">05 · morning brief</p>
          <h1>아침 알림을 받을 곳을 고릅니다.</h1>
          <p class="step-lede">0건이어도 문제 없음 알림을 보낼 수 있습니다.</p>
          <div class="notification-duo">
            <form class="onboarding-form" method="post" action="/settings/slack-webhook">
              <input type="hidden" name="return_to" value="/onboarding?step=5">
              <label>Slack webhook
                <input name="webhook_url" type="password" placeholder="{_escape(mask_secret(None, connection["slack_webhook_last4"]))}">
              </label>
              <button type="submit">Slack 테스트</button>
            </form>
            <div class="email-stack">
              <form class="onboarding-form" method="post" action="/settings/email">
                <input type="hidden" name="return_to" value="/onboarding?step=4">
                <label>이메일
                  <input name="email" type="email" value="{_escape(connection["notification_email"] or "")}" placeholder="me@example.com">
                </label>
                <button type="submit">코드 받기</button>
              </form>
              <form class="onboarding-form inline-form" method="post" action="/settings/email/verify">
                <input type="hidden" name="return_to" value="/onboarding?step=5">
                <label>인증 코드
                  <input name="code" inputmode="numeric" placeholder="000000">
                </label>
                <button type="submit">확인</button>
              </form>
            </div>
          </div>
          <form class="time-strip" method="post" action="/notifications">
            <input type="hidden" name="return_to" value="/onboarding?step=5">
            <input type="hidden" name="default_channel" value="both">
            <label>점검 <input type="time" name="scan_time" value="{_escape(settings["scan_time"])}"></label>
            <label>알림 <input type="time" name="notify_time" value="{_escape(settings["notify_time"])}"></label>
            <label>타임존 <input name="timezone" value="{_escape(settings["timezone"])}"></label>
            <label class="checkline"><input type="checkbox" name="notify_zero" checked><span>0건 알림</span></label>
            <button type="submit">시간 저장</button>
          </form>
        </article>

        <article class="onboarding-step" data-step="5">
          {back_button}
          <p class="kicker">06 · launch</p>
          <h1>밤의 점검 루프를 켭니다.</h1>
          <p class="step-lede">첫 실행은 기준선을 잡기 위해 선택 범위 전체를 읽습니다.</p>
          <div class="launch-grid">
            <div>{status_pill(notion_connected)}<strong>Notion</strong></div>
            <div>{status_pill(openrouter_connected)}<strong>OpenRouter</strong></div>
            <div>{status_pill(has_targets)}<strong>점검 대상</strong></div>
            <div>{status_pill(channels_connected)}<strong>알림</strong></div>
          </div>
          <div class="step-actions">
            <form method="post" action="/runs/manual"><button class="primary ink-button" type="submit">첫 점검 실행</button></form>
            <a class="button" href="/dashboard">대시보드</a>
          </div>
        </article>
    </section>
  </main>
  <script src="/static/onboarding.js?v={ASSET_VERSION}"></script>
</body>
</html>"""
    return body


def dashboard(
    user: sqlite3.Row,
    connection: sqlite3.Row,
    settings: sqlite3.Row,
    targets: list[sqlite3.Row],
    runs: list[sqlite3.Row],
    notice: str | None = None,
) -> str:
    notion_connected = bool(connection["notion_access_token_encrypted"])
    openrouter_connected = bool(connection["openrouter_api_key_encrypted"])
    slack_connected = bool(connection["slack_webhook_url_encrypted"])
    email_connected = bool(connection["notification_email_verified"])
    channels_connected = slack_connected or email_connected
    latest_run = runs[0] if runs else None
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">승인 경계가 있는 Notion 점검 루프</p>
    <h1>오늘의 운영 상태</h1>
  </div>
  <form method="post" action="/runs/manual">
    <button class="primary" type="submit">수동 점검 실행</button>
  </form>
</section>

<section class="metric-grid">
  <div class="metric"><span>Notion</span>{status_pill(notion_connected)}</div>
  <div class="metric"><span>OpenRouter</span>{status_pill(openrouter_connected)}</div>
  <div class="metric"><span>알림 채널</span>{status_pill(channels_connected)}</div>
  <div class="metric"><span>활성 대상</span><strong>{len(targets)}</strong></div>
</section>

<section class="columns">
  <div class="panel">
    <div class="panel-head"><h2>다음 실행</h2></div>
    <dl class="kv">
      <dt>스캔 시각</dt><dd>{_escape(settings["scan_time"])}</dd>
      <dt>알림 시각</dt><dd>{_escape(settings["notify_time"])}</dd>
      <dt>타임존</dt><dd>{_escape(settings["timezone"])}</dd>
      <dt>0건 알림</dt><dd>{'ON' if settings["notify_zero"] else 'OFF'}</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="panel-head"><h2>최근 실행</h2><a href="/runs">전체 보기</a></div>
    {run_summary(latest_run)}
  </div>
</section>

<section class="panel">
  <div class="panel-head"><h2>연결</h2><a href="/account">관리</a></div>
  <div class="connection-list">
    <div><span>워크스페이스</span><strong>{_escape(connection["notion_workspace_name"] or connection["notion_workspace_id"] or "연결 안 됨")}</strong></div>
    <div><span>API 키</span><strong>{_escape(mask_secret(None, connection["openrouter_key_last4"]))}</strong></div>
    <div><span>Slack</span><strong>{_escape(mask_secret(None, connection["slack_webhook_last4"]))}</strong></div>
    <div><span>이메일</span><strong>{_escape(connection["notification_email"] or "연결 안 됨")}</strong></div>
  </div>
</section>
"""
    return page("개요", body, "overview", notice)


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
  <form class="form-grid" method="post" action="/targets">
    <label>유형
      <select name="notion_object_type">
        <option value="page">페이지</option>
        <option value="database">데이터베이스</option>
      </select>
    </label>
    <label>Notion ID
      <input name="notion_object_id" required placeholder="페이지 또는 데이터베이스 ID">
    </label>
    <label>제목
      <input name="title" required placeholder="표시 제목">
    </label>
    <label>URL
      <input name="url" placeholder="https://www.notion.so/...">
    </label>
    <label>제외 페이지 ID
      <input name="excluded_page_ids" placeholder="쉼표로 구분">
    </label>
    <label class="checkline">
      <input type="checkbox" name="include_children" checked>
      <span>하위 페이지 포함</span>
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
    channel = settings["default_channel"]
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Slack과 이메일 발송 정책</p>
    <h1>알림 설정</h1>
  </div>
</section>

<section class="panel">
  <form class="form-grid" method="post" action="/notifications">
    <label>기본 채널
      <select name="default_channel">
        <option value="both" {'selected' if channel == 'both' else ''}>둘 다</option>
        <option value="slack" {'selected' if channel == 'slack' else ''}>Slack</option>
        <option value="email" {'selected' if channel == 'email' else ''}>이메일</option>
      </select>
    </label>
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
    <div class="panel-head"><h2>Slack</h2>{status_pill(bool(connection["slack_webhook_url_encrypted"]))}</div>
    <form class="stack" method="post" action="/settings/slack-webhook">
      <label>Webhook URL
        <input name="webhook_url" type="password" placeholder="https://hooks.slack.com/services/...">
      </label>
      <button type="submit">저장 및 테스트</button>
    </form>
  </div>
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


def runs_page(runs: list[sqlite3.Row], logs: list[sqlite3.Row], notice: str | None = None) -> str:
    run_rows = "".join(run_row(run) for run in runs) or '<tr><td colspan="12" class="empty">실행 기록이 없습니다.</td></tr>'
    log_rows = "".join(log_row(log) for log in logs) or '<tr><td colspan="4" class="empty">감사 로그가 없습니다.</td></tr>'
    body = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Agent run과 외부 호출 기록</p>
    <h1>실행 로그</h1>
  </div>
  <div class="actions">
    <form method="post" action="/apply-approved"><button type="submit">승인 항목 반영</button></form>
    <form method="post" action="/runs/manual"><button class="primary" type="submit">수동 점검 실행</button></form>
  </div>
</section>

<section class="panel">
  <div class="table-wrap">
    <table>
      <thead><tr><th>run_id</th><th>상태</th><th>시작</th><th>종료</th><th>스캔</th><th>변경</th><th>제안</th><th>오류</th><th>누락</th><th>모순</th><th>반영</th><th>알림</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </div>
</section>

<section class="panel">
  <div class="panel-head"><h2>감사 로그</h2></div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>시각</th><th>레벨</th><th>이벤트</th><th>내용</th></tr></thead>
      <tbody>{log_rows}</tbody>
    </table>
  </div>
</section>
"""
    return page("실행 로그", body, "runs", notice)


def run_row(run: sqlite3.Row) -> str:
    return f"""
<tr>
  <td><code>{_escape(run["run_id"])}</code></td>
  <td><span class="pill">{_escape(run["status"])}</span></td>
  <td>{_escape(run["started_at"])}</td>
  <td>{_escape(run["finished_at"])}</td>
  <td>{_escape(run["scanned_page_count"])}</td>
  <td>{_escape(run["changed_page_count"])}</td>
  <td>{_escape(run["proposal_count"])}</td>
  <td>{_escape(run["error_count"])}</td>
  <td>{_escape(run["omission_count"])}</td>
  <td>{_escape(run["contradiction_count"])}</td>
  <td>{_escape(run["applied_count"])}/{_escape(run["apply_failed_count"])}</td>
  <td>{_escape(run["notification_status"] or "-")}</td>
</tr>
"""


def log_row(log: sqlite3.Row) -> str:
    return f"""
<tr>
  <td>{_escape(log["created_at"])}</td>
  <td>{_escape(log["level"])}</td>
  <td>{_escape(log["event"])}</td>
  <td><code>{_escape(log["payload"] or "")}</code></td>
</tr>
"""


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
    <div class="panel-head"><h2>OpenRouter</h2>{status_pill(bool(connection["openrouter_api_key_encrypted"]))}</div>
    <form class="stack" method="post" action="/settings/openrouter">
      <label>API 키
        <input name="api_key" type="password" placeholder="{_escape(mask_secret(None, connection["openrouter_key_last4"]))}">
      </label>
      <button type="submit">저장 및 검증</button>
    </form>
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
