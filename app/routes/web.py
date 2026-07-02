from __future__ import annotations

import base64
import hmac
import json
import time
import urllib.parse
from hashlib import sha256

from fastapi import APIRouter, BackgroundTasks, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from app.time_utils import utc_now_iso
from app import ui


router = APIRouter()

SESSION_COOKIE = "nocturne_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _current_user(request: Request) -> object | None:
    token = request.cookies.get(SESSION_COOKIE)
    payload = _read_session_token(token, request.app.state.settings.encryption_key) if token else None
    if not payload:
        return None
    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return request.app.state.db.user_by_id(user_id)


def _auth_redirect() -> RedirectResponse:
    return _redirect("/", "Notion으로 로그인해 주세요.")


def _auth_json() -> JSONResponse:
    return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)


def _set_session_cookie(response: RedirectResponse, request: Request, user_id: int) -> None:
    token = _make_session_token(user_id, request.app.state.settings.encryption_key)
    secure = request.url.scheme == "https" or request.app.state.settings.app_url.startswith("https://")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


def _clear_session_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(SESSION_COOKIE)


@router.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse("app/static/nocturne-icon.svg", media_type="image/svg+xml")


@router.get("/", response_class=HTMLResponse)
def index(request: Request, notice: str | None = None, dashboard: int = 0) -> str:
    user = _current_user(request)
    if dashboard:
        if not user:
            return _auth_redirect()
        return dashboard_page(request, notice)
    if user:
        return RedirectResponse("/dashboard", status_code=303)
    return ui.intro_page(notice)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, notice: str | None = None) -> str:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    connection = db.connection_for_user(user["id"])
    settings = db.notification_settings_for_user(user["id"])
    targets = db.active_targets(user["id"])
    runs = db.rows("SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 5", (user["id"],))
    latest_run = runs[0] if runs else None
    run_items = _dashboard_run_items(db, user["id"], latest_run)
    return ui.dashboard(user, connection, settings, targets, runs, run_items, notice)


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request, notice: str | None = None, step: int = 0) -> str:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    connection = db.connection_for_user(user["id"])
    targets = db.active_targets(user["id"])
    if _onboarding_complete(connection, targets) and "step" not in request.query_params:
        return RedirectResponse("/dashboard", status_code=303)
    review_acknowledged = _progress_done(db, user["id"], "review_boundary")
    allowed_step = _allowed_onboarding_step(connection, targets, review_acknowledged)
    requested_step = allowed_step if "step" not in request.query_params else step
    if requested_step > allowed_step:
        return ui.onboarding_page(
            connection,
            db.notification_settings_for_user(user["id"]),
            targets,
            review_acknowledged,
            "앞 단계가 끝나야 다음 단계가 열립니다.",
            allowed_step,
        )
    return ui.onboarding_page(
        connection,
        db.notification_settings_for_user(user["id"]),
        targets,
        review_acknowledged,
        notice,
        requested_step,
    )


@router.post("/onboarding/review-boundary")
def acknowledge_review_boundary(request: Request) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    connection = db.connection_for_user(user["id"])
    if not connection["notion_access_token_encrypted"]:
        return _redirect("/onboarding?step=0", "먼저 Notion을 연결해야 합니다.")
    db.execute(
        """
        INSERT INTO onboarding_progress (user_id, progress_key, acknowledged_at)
        VALUES (?, 'review_boundary', ?)
        ON CONFLICT(user_id, progress_key) DO UPDATE SET acknowledged_at = excluded.acknowledged_at
        """,
        (user["id"], utc_now_iso()),
    )
    db.log("onboarding_review_boundary_acknowledged", user_id=user["id"])
    return _redirect("/onboarding?step=2", "수정함 승인 경계를 확인했습니다.")


@router.get("/auth/notion/start")
def notion_start(request: Request, next_path: str | None = Query(None, alias="next")) -> RedirectResponse:
    notion = request.app.state.notion
    return_to = _safe_return(next_path, "/onboarding?step=1")
    try:
        url = notion.oauth_start_url(_make_state(request.app.state.settings.encryption_key, return_to))
    except Exception as exc:
        return _redirect("/", f"Notion OAuth 시작 실패: {exc}")
    return RedirectResponse(url, status_code=303)


@router.get("/auth/notion/callback")
def notion_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return _redirect("/", f"Notion OAuth 오류: {error}")
    if not code or not state:
        return _redirect("/", "Notion OAuth callback 값이 빠져 있습니다.")
    state_data = _read_state(state, request.app.state.settings.encryption_key)
    if state_data is None:
        return _redirect("/", "Notion OAuth state를 확인하지 못했습니다.")
    return_to = state_data["return_to"]
    try:
        oauth_data = request.app.state.notion.exchange_code(code)
        user = request.app.state.db.user_for_notion_oauth(oauth_data)
        user_id = user["id"]
        request.app.state.notion.save_oauth_connection(user_id, oauth_data)
        request.app.state.db.log("notion_connected", user_id=user_id, payload={"workspace_id": oauth_data.get("workspace_id")})
        response = _redirect(return_to, "Notion 연결을 완료했습니다.")
        _set_session_cookie(response, request, user_id)
        return response
    except Exception as exc:
        return _redirect("/", f"Notion 연결 실패: {exc}")


@router.post("/settings/openrouter")
def save_openrouter(request: Request, api_key: str = Form(...), return_to: str = Form("")) -> RedirectResponse:
    if not _current_user(request):
        return _auth_redirect()
    return _redirect(_safe_return(return_to, "/settings"), "OpenRouter는 서버 환경변수 OPENROUTER_API_KEY를 사용합니다.")


@router.post("/settings/email")
def send_email_verification(request: Request, email: str = Form(...), return_to: str = Form("")) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    try:
        dev_code = request.app.state.email_service.send_verification(user["id"], email.strip())
        db.update(
            """
            UPDATE connections
            SET notification_email = ?, notification_email_verified = 0, updated_at = ?
            WHERE user_id = ?
            """,
            (email.strip(), utc_now_iso(), user["id"]),
        )
    except Exception as exc:
        return _redirect(_safe_return(return_to, "/settings"), f"이메일 인증 코드 발송 실패: {exc}")
    suffix = f" 개발 코드: {dev_code}" if dev_code else ""
    return _redirect(_safe_return(return_to, "/settings"), f"이메일 인증 코드를 보냈습니다.{suffix}")


@router.post("/settings/email/verify")
def verify_email(request: Request, code: str = Form(...), return_to: str = Form("")) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    ok, error = request.app.state.email_service.verify_code(user["id"], code)
    if not ok:
        return _redirect(_safe_return(return_to, "/settings"), error or "이메일 인증에 실패했습니다.")
    db.log("email_verified", user_id=user["id"])
    return _redirect(_safe_return(return_to, "/settings"), "이메일 알림을 연결했습니다.")


@router.get("/targets", response_class=HTMLResponse)
def targets(request: Request, notice: str | None = None) -> str:
    if not _current_user(request):
        return _auth_redirect()
    return settings_page(request, notice)


@router.get("/api/notion/search")
def search_notion_targets(
    request: Request,
    q: str = "",
    object_type: str = "",
    limit: int = Query(50, ge=1, le=100),
) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return _auth_json()
    try:
        items = request.app.state.notion.search_selectable_objects(
            user["id"],
            query=q,
            object_type=object_type or None,
            limit=limit,
            root_only=not q.strip(),
        )
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"items": items})


@router.get("/api/notion/children")
def notion_target_children(
    request: Request,
    parent_id: str = "",
    parent_type: str = "",
    object_type: str = "",
    limit: int = Query(50, ge=1, le=100),
) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return _auth_json()
    try:
        items = request.app.state.notion.list_selectable_children(
            user["id"],
            parent_id=parent_id,
            parent_type=parent_type,
            object_type=object_type or None,
            limit=limit,
        )
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"items": items})


@router.get("/api/knowledge-graph")
def knowledge_graph(request: Request) -> JSONResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_json()
    return JSONResponse(_knowledge_graph_payload(db, user["id"]))


@router.post("/api/knowledge-graph/sync")
def sync_knowledge_graph(request: Request) -> JSONResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_json()
    try:
        request.app.state.notion.sync_knowledge_graph(user["id"])
    except Exception as exc:
        payload = _knowledge_graph_payload(db, user["id"])
        payload["detail"] = str(exc)
        return JSONResponse(payload, status_code=400)
    return JSONResponse(_knowledge_graph_payload(db, user["id"]))


@router.post("/api/knowledge-graph/proposals/{proposal_id}/approve")
def approve_graph_proposal(request: Request, proposal_id: int) -> JSONResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_json()
    proposal = db.row(
        """
        SELECT * FROM proposals_cache
        WHERE id = ? AND user_id = ?
        LIMIT 1
        """,
        (proposal_id, user["id"]),
    )
    if not proposal:
        return JSONResponse({"detail": "제안을 찾지 못했습니다."}, status_code=404)
    if proposal["status"] == "반영됨":
        payload = _knowledge_graph_payload(db, user["id"])
        payload["message"] = "이미 반영된 제안입니다."
        return JSONResponse(payload)
    notion_page_id = proposal["notion_proposal_page_id"]
    if not notion_page_id:
        return JSONResponse({"detail": "Notion 제안 페이지 ID가 없습니다."}, status_code=400)
    try:
        request.app.state.notion.update_proposal_status(user["id"], notion_page_id, "승인")
        db.update(
            "UPDATE proposals_cache SET status = '승인', updated_at = ? WHERE id = ? AND user_id = ?",
            (utc_now_iso(), proposal_id, user["id"]),
        )
        applied, failed = request.app.state.harness.apply_approved_for_user(user["id"])
        db.log(
            "knowledge_graph_proposal_approved",
            user_id=user["id"],
            payload={"proposal_id": proposal_id, "notion_page_id": notion_page_id, "applied": applied, "failed": failed},
        )
    except Exception as exc:
        return JSONResponse({"detail": f"제안 승인 실패: {exc}"}, status_code=400)
    payload = _knowledge_graph_payload(db, user["id"])
    payload["message"] = f"승인 반영 완료: 성공 {applied}건, 실패 {failed}건"
    return JSONResponse(payload)


@router.post("/targets")
def add_target(
    request: Request,
    notion_object_id: str = Form(...),
    notion_object_type: str = Form(...),
    title: str = Form(...),
    url: str = Form(""),
    excluded_page_ids: str = Form(""),
    return_to: str = Form(""),
) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    object_id = notion_object_id.strip()
    object_type = notion_object_type.strip()
    display_title = title.strip()
    if object_type not in {"page", "database"} or not object_id or not display_title:
        return _redirect(_safe_return(return_to, "/targets"), "Notion 검색 결과에서 점검 대상을 선택해 주세요.")
    excluded = [item.strip() for item in excluded_page_ids.split(",") if item.strip()]
    now = utc_now_iso()
    db.execute(
        """
        INSERT INTO scan_targets
            (user_id, notion_object_id, notion_object_type, title, url, include_children,
             excluded_page_ids, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            user["id"],
            object_id,
            object_type,
            display_title,
            url.strip() or None,
            1,
            json.dumps(excluded, ensure_ascii=False),
            now,
            now,
        ),
    )
    notice = "점검 대상을 추가했습니다."
    if object_type == "page":
        try:
            request.app.state.notion.ensure_inbox_database(user["id"], object_id)
            notice = "점검 대상을 추가하고 Nocturne을 확인했습니다."
        except Exception as exc:
            db.log("inbox_ensure_after_target_failed", user_id=user["id"], level="warning", payload={"error": str(exc)})
    return _redirect(_safe_return(return_to, "/settings"), notice)


@router.post("/targets/{target_id}/delete")
def delete_target(request: Request, target_id: int) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    db.update(
        "UPDATE scan_targets SET active = 0, updated_at = ? WHERE id = ? AND user_id = ?",
        (utc_now_iso(), target_id, user["id"]),
    )
    return _redirect("/settings", "점검 대상을 삭제했습니다.")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, notice: str | None = None, section: str = "pages") -> str:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    return ui.settings_page(
        db.notification_settings_for_user(user["id"]),
        db.connection_for_user(user["id"]),
        db.active_targets(user["id"]),
        notice,
        request.app.state.settings.openrouter_configured,
        request.app.state.settings.openrouter_default_model,
        section,
    )


@router.get("/notifications", response_class=HTMLResponse)
def notifications(request: Request, notice: str | None = None) -> str:
    return settings_page(request, notice, "notifications")


@router.post("/notifications")
def update_notifications(
    request: Request,
    default_channel: str = Form("email"),
    scan_time: str = Form(...),
    notify_time: str = Form(...),
    timezone: str = Form(...),
    return_to: str = Form(""),
) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    db.update(
        """
        UPDATE notification_settings
        SET default_channel = ?, scan_time = ?, notify_time = ?, timezone = ?, notify_zero = ?, updated_at = ?
        WHERE user_id = ?
        """,
        ("email", scan_time, notify_time, timezone, 1, utc_now_iso(), user["id"]),
    )
    db.update("UPDATE users SET timezone = ? WHERE id = ?", (timezone, user["id"]))
    return _redirect(_safe_return(return_to, "/settings"), "알림 설정을 저장했습니다.")


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request, notice: str | None = None, limit: int = Query(20)) -> str:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    selected_limit = limit if limit in {20, 50, 100} else 20
    run_rows, logs, timezone_name = _runs_snapshot(db, user["id"], selected_limit)
    return ui.runs_page(run_rows, logs, notice, timezone_name, selected_limit)


@router.get("/api/runs")
def runs_api(request: Request, limit: int = Query(20)) -> JSONResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_json()
    selected_limit = limit if limit in {20, 50, 100} else 20
    run_rows, logs, timezone_name = _runs_snapshot(db, user["id"], selected_limit)
    return JSONResponse(
        {
            "limit": selected_limit,
            "fetchedAt": utc_now_iso(),
            "runCount": len(run_rows),
            "logCount": len(logs),
            "runRows": "".join(ui.run_row(run, timezone_name) for run in run_rows)
            or '<tr><td colspan="7" class="empty">실행 기록이 없습니다.</td></tr>',
            "logRows": "".join(ui.log_row(log, timezone_name) for log in logs)
            or '<tr><td colspan="3" class="empty">감사 로그가 없습니다.</td></tr>',
        }
    )


@router.get("/api/runs/{run_id}/errors")
def run_errors_api(request: Request, run_id: str) -> JSONResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_json()
    run = db.row(
        """
        SELECT * FROM runs
        WHERE user_id = ? AND run_id = ?
        LIMIT 1
        """,
        (user["id"], run_id),
    )
    if not run:
        return JSONResponse({"detail": "실행 기록을 찾지 못했습니다."}, status_code=404)
    logs = db.rows(
        """
        SELECT * FROM audit_logs
        WHERE (user_id IS NULL OR user_id = ?)
          AND run_id = ?
          AND (
            event IN (
                'approval_apply_failed',
                'page_scan_failed',
                'proposal_write_failed',
                'run_failed',
                'target_expand_failed'
            )
            OR (event = 'agent_tool_call' AND level IN ('warning', 'error'))
          )
        ORDER BY created_at DESC
        LIMIT 30
        """,
        (user["id"], run_id),
    )
    return JSONResponse(
        {
            "runId": run_id,
            "status": run["status"] or "",
            "errorMessage": run["error_message"] or "",
            "items": [
                {
                    "event": log["event"] or "",
                    "label": ui.EVENT_LABELS.get(log["event"] or "", (log["event"] or "").replace("_", " ")),
                    "level": log["level"] or "info",
                    "createdAt": log["created_at"] or "",
                    "summary": ui._payload_summary(log["payload"]),
                    "payload": log["payload"] or "",
                }
                for log in logs
            ],
        }
    )


@router.post("/runs/manual")
def manual_run(request: Request, background_tasks: BackgroundTasks, return_to: str = Form("")) -> RedirectResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    background_tasks.add_task(request.app.state.harness.run_for_user, user["id"], manual=True)
    target = _safe_return(return_to, "/runs")
    if not target.startswith("/runs"):
        target = "/runs"
    return _redirect(target, "수동 점검을 시작했습니다.")


@router.post("/apply-approved")
def apply_approved(request: Request, return_to: str = Form("")) -> RedirectResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    target = _safe_return(return_to, "/dashboard")
    try:
        applied, failed = request.app.state.harness.apply_approved_for_user(user["id"])
    except Exception as exc:
        return _redirect(target, f"승인 항목 반영 실패: {exc}")
    return _redirect(target, f"승인 항목 반영 완료: 성공 {applied}건, 실패 {failed}건")


@router.get("/account", response_class=HTMLResponse)
def account(request: Request, notice: str | None = None) -> str:
    if not _current_user(request):
        return _auth_redirect()
    return settings_page(request, notice, "pages")


@router.post("/account/disconnect/notion")
def disconnect_notion(request: Request) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    db.update(
        """
        UPDATE connections
        SET notion_access_token_encrypted = NULL,
            notion_workspace_id = NULL,
            notion_workspace_name = NULL,
            notion_bot_id = NULL,
            notion_owner_info = NULL,
            notion_inbox_database_id = NULL,
            notion_inbox_url = NULL,
            updated_at = ?
        WHERE user_id = ?
        """,
        (utc_now_iso(), user["id"]),
    )
    db.log("notion_disconnected", user_id=user["id"])
    response = _redirect("/", "Notion 연결을 해제했습니다.")
    _clear_session_cookie(response)
    return response


@router.post("/account/delete-local-data")
def delete_local_data(request: Request) -> RedirectResponse:
    db = request.app.state.db
    user = _current_user(request)
    if not user:
        return _auth_redirect()
    with db.connection() as conn:
        for table in [
            "connections",
            "scan_targets",
            "knowledge_graph_nodes",
            "knowledge_graph_edges",
            "knowledge_graph_syncs",
            "runs",
            "proposals_cache",
            "nocturne_edits",
            "email_verifications",
            "audit_logs",
            "notification_settings",
            "onboarding_progress",
        ]:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        conn.commit()
    response = _redirect("/", "로컬 데이터를 삭제했습니다.")
    _clear_session_cookie(response)
    return response


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    response = _redirect("/", "로그아웃했습니다.")
    _clear_session_cookie(response)
    return response


def _redirect(path: str, notice: str) -> RedirectResponse:
    separator = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{separator}notice={urllib.parse.quote(notice)}", status_code=303)


def _safe_return(path: str | None, default: str) -> str:
    if not path:
        return default
    parsed = urllib.parse.urlparse(path)
    if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//"):
        return default
    return path


def _runs_snapshot(db: object, user_id: int, selected_limit: int) -> tuple[list[object], list[object], str]:
    run_rows = db.rows(
        """
        SELECT
            r.*,
            COALESCE(
                (
                    SELECT COUNT(*)
                    FROM audit_logs l
                    WHERE l.run_id = r.run_id
                      AND (
                        l.event IN (
                            'approval_apply_failed',
                            'page_scan_failed',
                            'proposal_write_failed',
                            'run_failed',
                            'target_expand_failed'
                        )
                        OR (l.event = 'agent_tool_call' AND l.level IN ('warning', 'error'))
                      )
                ),
                0
            ) AS agent_error_count
        FROM runs r
        WHERE r.user_id = ?
        ORDER BY r.created_at DESC
        LIMIT ?
        """,
        (user_id, selected_limit),
    )
    logs = db.rows(
        """
        SELECT * FROM audit_logs
        WHERE user_id IS NULL OR user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, selected_limit),
    )
    settings = db.notification_settings_for_user(user_id)
    return run_rows, logs, settings["timezone"]


def _dashboard_run_items(db: object, user_id: int, latest_run: object | None) -> list[object]:
    if not latest_run:
        return []
    proposal_rows = db.rows(
        """
        SELECT
            'proposal' AS item_kind,
            pc.id AS item_id,
            pc.run_id,
            pc.source_page_id,
            COALESCE(st.title, '') AS source_title,
            pc.issue_type,
            pc.apply_mode,
            pc.status,
            pc.confidence,
            pc.created_at AS happened_at,
            pc.notion_proposal_page_id
        FROM proposals_cache pc
        LEFT JOIN scan_targets st
          ON st.user_id = pc.user_id AND st.notion_object_id = pc.source_page_id
        WHERE pc.user_id = ?
          AND pc.run_id = ?
        ORDER BY pc.created_at DESC
        LIMIT 40
        """,
        (user_id, latest_run["run_id"]),
    )
    finished_at = latest_run["finished_at"]
    applied_rows = db.rows(
        """
        SELECT
            'applied' AS item_kind,
            ne.id AS item_id,
            COALESCE(pc.run_id, '') AS run_id,
            ne.source_page_id,
            COALESCE(st.title, '') AS source_title,
            COALESCE(pc.issue_type, '') AS issue_type,
            COALESCE(pc.apply_mode, '') AS apply_mode,
            COALESCE(pc.status, '반영됨') AS status,
            COALESCE(pc.confidence, 0) AS confidence,
            ne.applied_at AS happened_at,
            COALESCE(pc.notion_proposal_page_id, ne.proposal_id) AS notion_proposal_page_id
        FROM nocturne_edits ne
        LEFT JOIN proposals_cache pc
          ON pc.user_id = ne.user_id AND pc.notion_proposal_page_id = ne.proposal_id
        LEFT JOIN scan_targets st
          ON st.user_id = ne.user_id AND st.notion_object_id = ne.source_page_id
        WHERE ne.user_id = ?
          AND ne.applied_at >= ?
          AND (? IS NULL OR ne.applied_at <= ?)
        ORDER BY ne.applied_at DESC
        LIMIT 40
        """,
        (user_id, latest_run["started_at"] or latest_run["created_at"], finished_at, finished_at),
    )
    rows = list(applied_rows) + list(proposal_rows)
    return sorted(rows, key=lambda row: row["happened_at"] or "", reverse=True)[:60]


def _knowledge_graph_payload(db: object, user_id: int) -> dict[str, object]:
    active_targets = db.active_targets(user_id)
    connection = db.connection_for_user(user_id)
    node_rows = db.rows(
        """
        SELECT * FROM knowledge_graph_nodes
        WHERE user_id = ?
        ORDER BY object_type, title
        """,
        (user_id,),
    )
    edge_rows = db.rows(
        """
        SELECT * FROM knowledge_graph_edges
        WHERE user_id = ?
        ORDER BY relation_type, source_object_id, target_object_id
        """,
        (user_id,),
    )
    sync = db.row("SELECT * FROM knowledge_graph_syncs WHERE user_id = ?", (user_id,))
    nodes: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    node_ids: set[str] = set()
    for row in node_rows:
        node_id = row["notion_object_id"]
        node_ids.add(node_id)
        is_database = row["object_type"] == "database"
        nodes.append(
            {
                "id": node_id,
                "objectId": node_id,
                "type": row["object_type"],
                "kind": "knowledge",
                "name": row["title"] or "Untitled",
                "url": row["url"] or "",
                "parentId": row["parent_id"] or "",
                "parentType": row["parent_type"] or "",
                "lastEditedTime": row["last_edited_time"] or "",
                "val": 4.8 if is_database else 3.6,
                "color": "#7fb6ff" if is_database else "#b8c7d9",
            }
        )
    for row in edge_rows:
        source = row["source_object_id"]
        target = row["target_object_id"]
        if source not in node_ids or target not in node_ids:
            continue
        relation = row["relation_type"] or "link"
        links.append(
            {
                "source": source,
                "target": target,
                "type": relation,
                "name": relation.replace("_", " "),
                "color": "rgba(160, 174, 192, 0.36)",
            }
        )

    proposal_rows = db.rows(
        """
        SELECT
            pc.*,
            COALESCE(kgn.title, st.title, '') AS source_title
        FROM proposals_cache pc
        LEFT JOIN knowledge_graph_nodes kgn
          ON kgn.user_id = pc.user_id AND kgn.notion_object_id = pc.source_page_id
        LEFT JOIN scan_targets st
          ON st.user_id = pc.user_id AND st.notion_object_id = pc.source_page_id
        WHERE pc.user_id = ?
          AND pc.status IN ('대기', '보류', '승인', '반영 실패')
        ORDER BY COALESCE(pc.updated_at, pc.created_at) DESC
        LIMIT 100
        """,
        (user_id,),
    )
    for row in proposal_rows:
        source_id = row["source_page_id"]
        if source_id not in node_ids:
            node_ids.add(source_id)
            nodes.append(
                {
                    "id": source_id,
                    "objectId": source_id,
                    "type": "page",
                    "kind": "knowledge",
                    "name": row["source_title"] or f"페이지 {source_id[-8:]}",
                    "url": "",
                    "val": 3.2,
                    "color": "#9aa8bb",
                }
            )
        proposal_node_id = f'proposal:{row["id"]}'
        nodes.append(
            {
                "id": proposal_node_id,
                "type": "proposal",
                "kind": "proposal",
                "name": _proposal_graph_title(row),
                "proposalId": row["id"],
                "sourcePageId": source_id,
                "sourceTitle": row["source_title"] or "",
                "notionProposalPageId": row["notion_proposal_page_id"] or "",
                "issueType": row["issue_type"] or "",
                "applyMode": row["apply_mode"] or "",
                "status": row["status"] or "",
                "confidence": float(row["confidence"] or 0),
                "originalSentence": row["original_sentence"] or "",
                "suggestedSentence": row["suggested_sentence"] or "",
                "rationale": row["rationale"] or "",
                "sourceUrls": _decode_string_list(row["source_urls"]),
                "createdAt": row["created_at"] or "",
                "val": 6.8,
                "color": "#f06a4f",
            }
        )
        links.append(
            {
                "source": source_id,
                "target": proposal_node_id,
                "type": "proposal",
                "name": "agent proposal",
                "color": "rgba(240, 106, 79, 0.78)",
                "proposal": True,
            }
        )

    return {
        "nodes": nodes,
        "links": links,
        "meta": {
            "nodeCount": len(nodes),
            "knowledgeNodeCount": len(node_rows),
            "linkCount": len(links),
            "proposalCount": len(proposal_rows),
            "lastSyncedAt": sync["last_synced_at"] if sync else None,
            "syncStatus": sync["status"] if sync else "never",
            "syncError": sync["error_message"] if sync else None,
            "hasTargets": bool(active_targets),
            "needsSync": bool(active_targets) and (not bool(node_rows) or sync is None or sync["status"] == "failed"),
            "workspaceId": connection["notion_workspace_id"] or "",
            "workspaceName": connection["notion_workspace_name"] or "",
        },
    }


def _proposal_graph_title(row: object) -> str:
    issue = ui.ISSUE_LABELS.get(row["issue_type"] or "", row["issue_type"] or "제안")
    suggested = (row["suggested_sentence"] or "").strip()
    if suggested:
        return f"{issue}: {suggested[:72]}"
    return f"{issue} 제안"


def _decode_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if str(item).strip()]


def _make_state(secret: str, return_to: str) -> str:
    payload = {"return_to": _safe_return(return_to, "/onboarding?step=1")}
    return _signed_payload(payload, secret)


def _read_state(state: str, secret: str) -> dict[str, str] | None:
    payload = _read_signed_payload(state, secret, max_age_seconds=60 * 60)
    if not payload:
        return None
    return {"return_to": _safe_return(str(payload.get("return_to") or ""), "/onboarding?step=1")}


def _make_session_token(user_id: int, secret: str) -> str:
    return _signed_payload({"user_id": user_id}, secret)


def _read_session_token(token: str, secret: str) -> dict[str, object] | None:
    return _read_signed_payload(token, secret, max_age_seconds=SESSION_MAX_AGE_SECONDS)


def _signed_payload(payload: dict[str, object], secret: str) -> str:
    payload = {**payload, "timestamp": int(time.time())}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), sha256).hexdigest()
    return f"{body}.{signature}"


def _read_signed_payload(value: str, secret: str, *, max_age_seconds: int) -> dict[str, object] | None:
    try:
        body, signature = value.rsplit(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body.encode("utf-8")).decode("utf-8"))
        if int(time.time()) - int(payload["timestamp"]) > max_age_seconds:
            return None
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _onboarding_complete(connection: object, targets: list[object]) -> bool:
    notion = bool(connection["notion_access_token_encrypted"])
    channel = bool(connection["notification_email_verified"])
    return notion and channel and bool(targets)


def _progress_done(db: object, user_id: int, progress_key: str) -> bool:
    row = db.row(
        "SELECT acknowledged_at FROM onboarding_progress WHERE user_id = ? AND progress_key = ?",
        (user_id, progress_key),
    )
    return row is not None


def _allowed_onboarding_step(connection: object, targets: list[object], review_acknowledged: bool) -> int:
    notion = bool(connection["notion_access_token_encrypted"])
    channel = bool(connection["notification_email_verified"])
    if not notion:
        return 0
    if not targets:
        return 1
    if not channel:
        return 2
    return 3
