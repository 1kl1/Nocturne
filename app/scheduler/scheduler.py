from __future__ import annotations

import asyncio
from contextlib import suppress

from app.agent.harness import AgentHarness
from app.db import Database
from app.time_utils import local_hhmm, to_user_date, utc_now


class SchedulerLoop:
    def __init__(self, db: Database, harness: AgentHarness, interval_seconds: int = 60):
        self.db = db
        self.harness = harness
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="nocturne-scheduler")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._tick()
            except Exception as exc:
                self.db.log("scheduler_tick_failed", level="error", payload={"error": str(exc)})
            await asyncio.sleep(self.interval_seconds)

    async def _tick(self) -> None:
        users = self.db.rows(
            """
            SELECT u.* FROM users u
            JOIN connections c ON c.user_id = u.id
            WHERE c.notion_access_token_encrypted IS NOT NULL
            ORDER BY u.id
            """
        )
        for user in users:
            prefs = self.db.notification_settings_for_user(user["id"])
            timezone = prefs["timezone"] or user["timezone"] or "UTC"
            if local_hhmm(timezone) != prefs["scan_time"]:
                continue
            today = to_user_date(utc_now(), timezone)
            if user["last_scheduled_run_date"] == today:
                continue
            self.db.update("UPDATE users SET last_scheduled_run_date = ? WHERE id = ?", (today, user["id"]))
            await asyncio.to_thread(self.harness.run_for_user, user["id"], manual=False)
