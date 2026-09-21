from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.applications.api import human_command
from backend.app.dashboard.api import AuthDep
from backend.app.db.session import create_session_factory
from scripts.run_daily_search import DailyRun

router = APIRouter(prefix="/api/runs", tags=["runs"])
HumanDep = Annotated[None, Depends(human_command)]
_lock = Lock()
_schedule = {"enabled": False, "time": "07:00", "timezone": "Europe/Copenhagen", "last_triggered_date": None}


class ScheduleCommand(BaseModel):
    enabled: bool
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def execute_daily() -> None:
    if not _lock.acquire(blocking=False):
        return
    try:
        with create_session_factory()() as session:
            DailyRun(session=session, root=Path(__file__).resolve().parents[2]).execute()
    finally:
        _lock.release()


@router.post("/daily", status_code=202)
def run_daily(background: BackgroundTasks, _: AuthDep, __: HumanDep) -> dict[str, str]:
    if _lock.locked():
        raise HTTPException(409, "a daily run is already active")
    background.add_task(execute_daily)
    return {"status": "QUEUED"}


@router.get("/schedule")
def get_schedule(_: AuthDep) -> dict:
    return dict(_schedule)


@router.put("/schedule")
def set_schedule(command: ScheduleCommand, _: AuthDep, __: HumanDep) -> dict:
    _schedule.update(enabled=command.enabled, time=command.time)
    return dict(_schedule)


async def scheduler_loop() -> None:
    while True:
        now = datetime.now(ZoneInfo(str(_schedule["timezone"])))
        today = now.date().isoformat()
        if _schedule["enabled"] and now.strftime("%H:%M") == _schedule["time"] and _schedule["last_triggered_date"] != today:
            _schedule["last_triggered_date"] = today
            await asyncio.to_thread(execute_daily)
        await asyncio.sleep(30)
