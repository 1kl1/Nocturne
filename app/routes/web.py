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


@router.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse("app/static/nocturne-icon.svg", media_type="image/svg+xml")


@router.get("/", response_class=HTMLResponse)
def index(request: Request, notice: str | None = None, dashboard: int = 0) -> str:
    if dashboard:
        return dashboard_page(request, notice)
    return ui.intro_page(notice)


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, notice: str | None = None) -> str:
    db = request.app.state.db
    user = db.default_user()
    connection = db.connection_for_user(user["id"])
    settings = db.notification_settings_for_user(user["id"])
    targets = db.active_targets(user["id"])
    runs = db.rows("SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 5", (user["id"],))
    improvements = db.rows(
        """
        SELECT
            pc.source_page_id,
            COALESCE(MAX(st.title), '') AS source_title,
            COUNT(*) AS proposal_count,
            SUM(CASE WHEN pc.status IN ('대기', '보류') THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN pc.status = '승인' THEN 1 ELSE 0 END) AS approved_count,
            SUM(CASE WHEN pc.status = '반영 실패' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN pc.issue_type = 'error' THEN 1 ELSE 0 END) AS error_count,
            SUM(CASE WHEN pc.issue_type = 'omission' THEN 1 ELSE 0 END) AS omission_count,
            SUM(CASE WHEN pc.issue_type = 'contradiction' THEN 1 ELSE 0 END) AS contradiction_count,
            MAX(pc.confidence) AS max_confidence,
            MAX(pc.created_at) AS latest_created_at,
            MAX(pc.notion_proposal_page_id) AS notion_proposal_page_id
        FROM proposals_cache pc
        LEFT JOIN scan_targets st
          ON st.user_id = pc.user_id AND st.notion_object_id = pc.source_page_id
        WHERE pc.user_id = ?
          AND pc.status IN ('대기', '보류', '승인', '반영 실패')
        GROUP BY pc.source_page_id
        ORDER BY approved_count DESC, pending_count DESC, latest_created_at DESC
        LIMIT 8
        """,
        (user["id"],),
    )
    return ui.dashboard(user, connection, settings, targets, runs, improvements, notice)


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request, notice: str | None = None, step: int = 0) -> str:
    db = request.app.state.db
    user = db.default_user()
    connection = db.connection_for_user(user["id"])
    targets = db.active_targets(user["id"])
    review_acknowledged = _progress_done(db, user["id"], "review_boundary")
    allowed_step = _allowed_onboarding_step(connection, targets, review_acknowledged)
    if step > allowed_step:
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
        step,
    )


@router.post("/onboarding/review-boundary")
def acknowledge_review_boundary(request: Request) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
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
    db = request.app.state.db
    user = db.default_user()
    notion = request.app.state.notion
    try:
        url = notion.oauth_start_url(_make_state(user["id"], request.app.state.settings.encryption_key, next_path or "/settings"))
    except Exception as exc:
        return _redirect(_safe_return(next_path, "/settings"), f"Notion OAuth 시작 실패: {exc}")
    return RedirectResponse(url, status_code=303)


@router.get("/auth/notion/callback")
def notion_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    if error:
        return _redirect("/settings", f"Notion OAuth 오류: {error}")
    if not code or not state:
        return _redirect("/settings", "Notion OAuth callback 값이 빠져 있습니다.")
    state_data = _read_state(state, request.app.state.settings.encryption_key)
    if state_data is None:
        return _redirect("/settings", "Notion OAuth state를 확인하지 못했습니다.")
    user_id = state_data["user_id"]
    return_to = state_data["return_to"]
    try:
        oauth_data = request.app.state.notion.exchange_code(code)
        request.app.state.notion.save_oauth_connection(user_id, oauth_data)
        request.app.state.db.log("notion_connected", user_id=user_id, payload={"workspace_id": oauth_data.get("workspace_id")})
        return _redirect(return_to, "Notion 연결을 완료했습니다.")
    except Exception as exc:
        return _redirect(return_to, f"Notion 연결 실패: {exc}")


@router.post("/settings/openrouter")
def save_openrouter(request: Request, api_key: str = Form(...), return_to: str = Form("")) -> RedirectResponse:
    return _redirect(_safe_return(return_to, "/settings"), "OpenRouter는 서버 환경변수 OPENROUTER_API_KEY를 사용합니다.")


@router.post("/settings/email")
def send_email_verification(request: Request, email: str = Form(...), return_to: str = Form("")) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
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
    user = db.default_user()
    ok, error = request.app.state.email_service.verify_code(user["id"], code)
    if not ok:
        return _redirect(_safe_return(return_to, "/settings"), error or "이메일 인증에 실패했습니다.")
    db.log("email_verified", user_id=user["id"])
    return _redirect(_safe_return(return_to, "/settings"), "이메일 알림을 연결했습니다.")


@router.get("/targets", response_class=HTMLResponse)
def targets(request: Request, notice: str | None = None) -> str:
    return settings_page(request, notice)


@router.get("/api/notion/search")
def search_notion_targets(
    request: Request,
    q: str = "",
    object_type: str = "",
    limit: int = Query(25, ge=1, le=50),
) -> JSONResponse:
    user = request.app.state.db.default_user()
    try:
        items = request.app.state.notion.search_selectable_objects(
            user["id"],
            query=q,
            object_type=object_type or None,
            limit=limit,
        )
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return JSONResponse({"items": items})


@router.post("/targets")
def add_target(
    request: Request,
    notion_object_id: str = Form(...),
    notion_object_type: str = Form(...),
    title: str = Form(...),
    url: str = Form(""),
    excluded_page_ids: str = Form(""),
    include_children: str | None = Form(None),
    return_to: str = Form(""),
) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
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
            1 if include_children else 0,
            json.dumps(excluded, ensure_ascii=False),
            now,
            now,
        ),
    )
    notice = "점검 대상을 추가했습니다."
    if object_type == "page":
        try:
            request.app.state.notion.ensure_inbox_database(user["id"], object_id)
            notice = "점검 대상을 추가하고 Nocturne 수정함을 확인했습니다."
        except Exception as exc:
            db.log("inbox_ensure_after_target_failed", user_id=user["id"], level="warning", payload={"error": str(exc)})
    return _redirect(_safe_return(return_to, "/settings"), notice)


@router.post("/targets/{target_id}/delete")
def delete_target(request: Request, target_id: int) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
    db.update(
        "UPDATE scan_targets SET active = 0, updated_at = ? WHERE id = ? AND user_id = ?",
        (utc_now_iso(), target_id, user["id"]),
    )
    return _redirect("/settings", "점검 대상을 삭제했습니다.")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, notice: str | None = None) -> str:
    db = request.app.state.db
    user = db.default_user()
    return ui.settings_page(
        db.notification_settings_for_user(user["id"]),
        db.connection_for_user(user["id"]),
        db.active_targets(user["id"]),
        notice,
        request.app.state.settings.openrouter_configured,
        request.app.state.settings.openrouter_default_model,
    )


@router.get("/notifications", response_class=HTMLResponse)
def notifications(request: Request, notice: str | None = None) -> str:
    return settings_page(request, notice)


@router.post("/notifications")
def update_notifications(
    request: Request,
    default_channel: str = Form("email"),
    scan_time: str = Form(...),
    notify_time: str = Form(...),
    timezone: str = Form(...),
    notify_zero: str | None = Form(None),
    return_to: str = Form(""),
) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
    db.update(
        """
        UPDATE notification_settings
        SET default_channel = ?, scan_time = ?, notify_time = ?, timezone = ?, notify_zero = ?, updated_at = ?
        WHERE user_id = ?
        """,
        ("email", scan_time, notify_time, timezone, 1 if notify_zero else 0, utc_now_iso(), user["id"]),
    )
    db.update("UPDATE users SET timezone = ? WHERE id = ?", (timezone, user["id"]))
    return _redirect(_safe_return(return_to, "/settings"), "알림 설정을 저장했습니다.")


@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request, notice: str | None = None) -> str:
    db = request.app.state.db
    user = db.default_user()
    run_rows = db.rows("SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 50", (user["id"],))
    logs = db.rows("SELECT * FROM audit_logs WHERE user_id IS NULL OR user_id = ? ORDER BY created_at DESC LIMIT 80", (user["id"],))
    settings = db.notification_settings_for_user(user["id"])
    return ui.runs_page(run_rows, logs, notice, settings["timezone"])


@router.post("/runs/manual")
def manual_run(request: Request, background_tasks: BackgroundTasks, return_to: str = Form("")) -> RedirectResponse:
    user = request.app.state.db.default_user()
    background_tasks.add_task(request.app.state.harness.run_for_user, user["id"], manual=True)
    return _redirect(_safe_return(return_to, "/runs"), "수동 점검을 시작했습니다.")


@router.post("/apply-approved")
def apply_approved(request: Request, return_to: str = Form("")) -> RedirectResponse:
    user = request.app.state.db.default_user()
    target = _safe_return(return_to, "/dashboard")
    try:
        applied, failed = request.app.state.harness.apply_approved_for_user(user["id"])
    except Exception as exc:
        return _redirect(target, f"승인 항목 반영 실패: {exc}")
    return _redirect(target, f"승인 항목 반영 완료: 성공 {applied}건, 실패 {failed}건")


@router.get("/account", response_class=HTMLResponse)
def account(request: Request, notice: str | None = None) -> str:
    return settings_page(request, notice)


@router.post("/account/disconnect/notion")
def disconnect_notion(request: Request) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
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
    return _redirect("/settings", "Notion 연결을 해제했습니다.")


@router.post("/account/delete-local-data")
def delete_local_data(request: Request) -> RedirectResponse:
    db = request.app.state.db
    user = db.default_user()
    with db.connection() as conn:
        for table in ["connections", "scan_targets", "runs", "proposals_cache", "nocturne_edits", "email_verifications", "audit_logs", "notification_settings", "onboarding_progress"]:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user["id"],))
        conn.execute("UPDATE users SET last_successful_scan_at = NULL, last_scheduled_run_date = NULL WHERE id = ?", (user["id"],))
        conn.commit()
    db.initialize()
    return _redirect("/", "로컬 데이터를 삭제했습니다.")


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


def _make_state(user_id: int, secret: str, return_to: str) -> str:
    payload = {
        "user_id": user_id,
        "timestamp": int(time.time()),
        "return_to": _safe_return(return_to, "/account"),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), sha256).hexdigest()
    return f"{body}.{signature}"


def _read_state(state: str, secret: str) -> dict[str, int | str] | None:
    try:
        body, signature = state.rsplit(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body.encode("utf-8")).decode("utf-8"))
        if int(time.time()) - int(payload["timestamp"]) > 60 * 60:
            return None
        return {"user_id": int(payload["user_id"]), "return_to": _safe_return(str(payload.get("return_to") or ""), "/account")}
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
    if not review_acknowledged:
        return 1
    if not targets:
        return 3
    if not channel:
        return 4
    return 5
