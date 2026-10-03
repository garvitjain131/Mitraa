from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class TaskSpec(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1)
    description: str = ""
    estimated_hours: float = Field(gt=0, le=10000)
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    optional: bool = False
    depends_on: list[str] = Field(default_factory=list)
    suggested_assignee: str | None = None
    status: Literal["todo", "in_progress", "completed"] = "todo"


class ProjectSpec(BaseModel):
    tasks: list[TaskSpec] = Field(min_length=1)


class DependencySpec(BaseModel):
    task: str
    depends_on: str


class MemberSpec(BaseModel):
    name: str = Field(min_length=1)
    hours_per_day: float = Field(default=6, gt=0, le=24)
    unavailable_dates: list[date] = Field(default_factory=list)


class Scenario(BaseModel):
    type: Literal["member_unavailable", "estimate_change", "deadline_change", "remove_task", "add_member"]
    member: str | None = None
    days: int | None = Field(default=None, ge=0)
    start_date: date | None = None
    task: str | None = None
    new_hours: float | None = Field(default=None, gt=0)
    new_days: int | None = Field(default=None, ge=0)
    name: str | None = None
    hours_per_day: float | None = Field(default=None, gt=0, le=24)


class TaskTiming(BaseModel):
    key: str
    title: str = ""
    status: str = "todo"
    priority: str = "medium"
    optional: bool = False
    estimated_hours: float = 0
    es: float
    ef: float
    ls: float
    lf: float
    slack: float
    is_critical: bool
    start_date: date | None = None
    end_date: date | None = None
    assignee: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class ScheduleResult(BaseModel):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE"]
    project_duration: float
    critical_path: list[str]
    tasks: list[TaskTiming]
    end_date: date | None = None
    meets_deadline: bool | None = None
    dropped_tasks: list[str] = Field(default_factory=list)
    reason: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class ScenarioDiff(BaseModel):
    old_end_date: date | None
    new_end_date: date | None
    delta_days: int
    tasks_newly_critical: list[str]
    tasks_delayed: list[dict]
    meets_deadline: bool | None
    suggestions: list[str]
    baseline: ScheduleResult
    scenario: ScheduleResult
