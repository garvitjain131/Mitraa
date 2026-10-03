from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from mitraa.planning.models import MemberSpec, Scenario, ScenarioDiff, TaskSpec
from mitraa.planning.optimizer import optimize


def simulate(tasks: list[TaskSpec], members: list[MemberSpec], start: date, deadline_days: int | None,
             scenario: Scenario, weekends_working: bool = True) -> ScenarioDiff:
    baseline = optimize(tasks, members, start, deadline_days, weekends_working)
    changed_tasks, changed_members = deepcopy(tasks), deepcopy(members)
    changed_deadline = deadline_days
    if scenario.type == "member_unavailable":
        member = next((m for m in changed_members if m.name == scenario.member), None)
        if member is None or scenario.days is None:
            raise ValueError("Choose a member and number of unavailable days.")
        begin = scenario.start_date or start
        member.unavailable_dates = sorted(set(member.unavailable_dates) |
                                          {begin + timedelta(days=i) for i in range(scenario.days)})
    elif scenario.type == "estimate_change":
        task = next((t for t in changed_tasks if t.key == scenario.task), None)
        if task is None or scenario.new_hours is None:
            raise ValueError("Choose a task and new hour estimate.")
        task.estimated_hours = scenario.new_hours
    elif scenario.type == "deadline_change":
        if scenario.new_days is None:
            raise ValueError("Provide new_days.")
        changed_deadline = scenario.new_days
    elif scenario.type == "remove_task":
        if scenario.task not in {t.key for t in changed_tasks}:
            raise ValueError("Unknown task.")
        changed_tasks = [t for t in changed_tasks if t.key != scenario.task]
        for task in changed_tasks:
            task.depends_on = [key for key in task.depends_on if key != scenario.task]
    elif scenario.type == "add_member":
        if not scenario.name:
            raise ValueError("Provide a member name.")
        if any(m.name == scenario.name for m in changed_members):
            raise ValueError("Member already exists.")
        changed_members.append(MemberSpec(name=scenario.name, hours_per_day=scenario.hours_per_day or 6))
    new = optimize(changed_tasks, changed_members, start, changed_deadline, weekends_working)
    old_by_key = {t.key: t for t in baseline.tasks}
    delayed = []
    for timing in new.tasks:
        old = old_by_key.get(timing.key)
        if old and old.end_date and timing.end_date and timing.end_date > old.end_date:
            delayed.append({"task": timing.key, "days": (timing.end_date-old.end_date).days})
    suggestions = list(new.suggestions)
    if new.status == "INFEASIBLE" and not suggestions:
        suggestions.append("Increase capacity or move the deadline.")
    if changed_deadline is not None and new.end_date and new.end_date > start + timedelta(days=changed_deadline-1):
        suggestions.append("Move independent tasks to a second member or drop optional tasks.")
    return ScenarioDiff(old_end_date=baseline.end_date, new_end_date=new.end_date,
                        delta_days=(new.end_date-baseline.end_date).days if new.end_date and baseline.end_date else 0,
                        tasks_newly_critical=sorted(set(new.critical_path)-set(baseline.critical_path)),
                        tasks_delayed=delayed, meets_deadline=new.meets_deadline,
                        suggestions=suggestions, baseline=baseline, scenario=new)
