from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.harness import AgentHarness
from app.config import get_settings
from app.db import Database
from app.routes import router
from app.scheduler.scheduler import SchedulerLoop
from app.security import SecretBox
from app.services.email_service import EmailService
from app.services.notification_service import NotificationService
from app.services.notion_service import NotionService
from app.services.openrouter_service import OpenRouterService
from app.services.slack_service import SlackService
from app.services.web_search_service import WebSearchService


app = FastAPI(title="Nocturne MVP")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    settings = get_settings()
    db = Database(settings)
    db.initialize()
    secret_box = SecretBox(settings.encryption_key)
    slack = SlackService()
    email = EmailService(settings, db)
    notion = NotionService(settings, db, secret_box)
    openrouter = OpenRouterService(settings)
    web_search = WebSearchService(settings)
    notifications = NotificationService(settings, db, secret_box, slack, email)
    harness = AgentHarness(settings, db, secret_box, notion, openrouter, web_search, notifications)

    app.state.settings = settings
    app.state.db = db
    app.state.secret_box = secret_box
    app.state.slack = slack
    app.state.email_service = email
    app.state.notion = notion
    app.state.openrouter = openrouter
    app.state.web_search = web_search
    app.state.notifications = notifications
    app.state.harness = harness
    app.state.scheduler = SchedulerLoop(db, harness)
    if settings.scheduler_enabled:
        app.state.scheduler.start()
        db.log("scheduler_started")


@app.on_event("shutdown")
async def shutdown() -> None:
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        await scheduler.stop()
