"""Database boundary for project planning."""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mitraa.db.models import Dependency, Project, ProjectMember, Task
from mitraa.planning.graph import topological_order
from mitraa.planning.models import MemberSpec, ProjectSpec, Scenario, TaskSpec
from mitraa.planning.optimizer import optimize
from mitraa.planning.simulator import simulate


def project_or_error(session: Session, project_id: str) -> Project:
    row = session.get(Project, project_id)
    if row is None:
        raise ValueError(f"Project {project_id} not found.")
    return row


def project_data(session: Session, project_id: str) -> tuple[Project, list[TaskSpec], list[MemberSpec]]:
    project = project_or_error(session, project_id)
    rows = list(session.scalars(select(Task).where(Task.project_id == project_id)))
    ids = {r.id: r.key for r in rows}
    deps = list(session.scalars(select(Dependency).where(Dependency.task_id.in_(ids)))) if ids else []
    parents: dict[str, list[str]] = {r.key: [] for r in rows}
    for dep in deps:
        parents[ids[dep.task_id]].append(ids[dep.depends_on_id])
    tasks = [TaskSpec(key=r.key, title=r.title, description=r.description, estimated_hours=r.estimated_hours,
                      priority=r.priority, status=r.status, optional=r.optional,
                      suggested_assignee=r.assignee, depends_on=parents[r.key]) for r in rows]
    members = [MemberSpec(name=m.name, hours_per_day=m.hours_per_day,
                          unavailable_dates=json.loads(m.unavailable_dates_json)) for m in
               session.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id))]
    return project, tasks, members


def create_project(session: Session, name: str, goal: str = "", deadline_days: int | None = None,
                   members: list[MemberSpec] | None = None, start_date: date | None = None) -> Project:
    project = Project(name=name, goal=goal, start_date=(start_date or date.today()).isoformat(),
                      deadline_days=deadline_days)
    session.add(project)
    session.flush()
    for member in members or [MemberSpec(name="You")]:
        session.add(ProjectMember(project_id=project.id, name=member.name, hours_per_day=member.hours_per_day,
                                  unavailable_dates_json=json.dumps([d.isoformat() for d in member.unavailable_dates])))
    session.flush()
    return project


def save_plan(session: Session, project_id: str, spec: ProjectSpec) -> None:
    project, existing, _ = project_data(session, project_id)
    all_keys = [t.key for t in existing] + [t.key for t in spec.tasks]
    dependencies = {t.key: t.depends_on for t in existing + spec.tasks}
    topological_order(all_keys, dependencies)
    created = {}
    for task in spec.tasks:
        row = Task(project_id=project.id, key=task.key, title=task.title, description=task.description,
                   estimated_hours=task.estimated_hours, priority=task.priority, status=task.status,
                   assignee=task.suggested_assignee, optional=task.optional)
        session.add(row)
        session.flush()
        created[task.key] = row
    old_rows = {r.key: r for r in session.scalars(select(Task).where(Task.project_id == project_id))}
    for task in spec.tasks:
        for parent in task.depends_on:
            session.add(Dependency(task_id=created[task.key].id, depends_on_id=old_rows[parent].id))
    session.flush()


def add_task(session: Session, project_id: str, spec: TaskSpec) -> Task:
    save_plan(session, project_id, ProjectSpec(tasks=[spec]))
    return session.scalar(select(Task).where(Task.project_id == project_id, Task.key == spec.key))


def update_task(session: Session, task_id: str, changes: dict) -> Task:
    row = session.get(Task, task_id)
    if row is None:
        raise ValueError("Task not found.")
    allowed = {"title", "description", "estimated_hours", "priority", "status", "assignee", "optional"}
    if set(changes) - allowed:
        raise ValueError(f"Cannot update {sorted(set(changes)-allowed)[0]}.")
    current = {key: getattr(row, key) for key in allowed}
    TaskSpec(key=row.key, title=changes.get("title", current["title"]),
             description=changes.get("description", current["description"]),
             estimated_hours=changes.get("estimated_hours", current["estimated_hours"]),
             priority=changes.get("priority", current["priority"]),
             status=changes.get("status", current["status"]),
             suggested_assignee=changes.get("assignee", current["assignee"]),
             optional=changes.get("optional", current["optional"]))
    for key, value in changes.items():
        setattr(row, key, value)
    session.flush()
    return row


def add_dependency(session: Session, project_id: str, task_key: str, parent_key: str) -> None:
    project, tasks, _ = project_data(session, project_id)
    by_key = {t.key: t for t in tasks}
    if task_key not in by_key or parent_key not in by_key:
        raise ValueError("Both tasks must belong to the project.")
    if parent_key in by_key[task_key].depends_on:
        return
    by_key[task_key].depends_on.append(parent_key)
    topological_order(list(by_key), {key: t.depends_on for key, t in by_key.items()})
    rows = {r.key: r for r in session.scalars(select(Task).where(Task.project_id == project.id))}
    session.add(Dependency(task_id=rows[task_key].id, depends_on_id=rows[parent_key].id))
    session.flush()


def schedule(session: Session, project_id: str, deadline_days: int | None = None):
    project, tasks, members = project_data(session, project_id)
    original_start = date.fromisoformat(project.start_date)
    start = max(original_start, date.today())
    remaining = (original_start + timedelta(days=project.deadline_days) - start).days if project.deadline_days else None
    return optimize(tasks, members, start, remaining if deadline_days is None else deadline_days,
                    project.weekends_working)


def scenario(session: Session, project_id: str, change: Scenario):
    project, tasks, members = project_data(session, project_id)
    original_start = date.fromisoformat(project.start_date)
    start = max(original_start, date.today())
    remaining = (original_start + timedelta(days=project.deadline_days) - start).days if project.deadline_days else None
    return simulate(tasks, members, start, remaining,
                    change, project.weekends_working)


def project_dict(project: Project) -> dict:
    return {"id": project.id, "name": project.name, "goal": project.goal, "start_date": project.start_date,
            "deadline_days": project.deadline_days, "weekends_working": project.weekends_working}


def delete_task(session: Session, task_id: str) -> None:
    row = session.get(Task, task_id)
    if row is None:
        raise ValueError("Task not found.")
    for dep in session.scalars(select(Dependency).where(
            (Dependency.task_id == task_id) | (Dependency.depends_on_id == task_id))):
        session.delete(dep)
    session.delete(row)
    session.flush()


def delete_project(session: Session, project_id: str) -> None:
    project = project_or_error(session, project_id)
    tasks = list(session.scalars(select(Task).where(Task.project_id == project_id)))
    ids = [t.id for t in tasks]
    if ids:
        for dep in session.scalars(select(Dependency).where(Dependency.task_id.in_(ids))):
            session.delete(dep)
    for task in tasks:
        session.delete(task)
    for member in session.scalars(select(ProjectMember).where(ProjectMember.project_id == project_id)):
        session.delete(member)
    session.delete(project)
    session.flush()
