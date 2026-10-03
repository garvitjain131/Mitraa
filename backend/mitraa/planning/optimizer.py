"""CP-SAT schedule with hourly work slots, member capacity and time off."""
from __future__ import annotations

import math
from datetime import date, timedelta

from mitraa.planning.cpm import calculate
from mitraa.planning.models import MemberSpec, ScheduleResult, TaskSpec


def optimize(tasks: list[TaskSpec], members: list[MemberSpec], start_date: date,
             deadline_days: int | None = None, weekends_working: bool = True) -> ScheduleResult:
    from ortools.sat.python import cp_model

    if not members:
        members = [MemberSpec(name="You")]
    timings, duration, critical = calculate(tasks)
    if not tasks:
        return ScheduleResult(status="OPTIMAL", project_duration=0, critical_path=[], tasks=[],
                              end_date=start_date, meets_deadline=True)
    max_off = max((len(m.unavailable_dates) for m in members), default=0)
    days = max(15, math.ceil(sum(math.ceil(t.estimated_hours) for t in tasks) /
                             max(1, min(math.floor(m.hours_per_day) for m in members))) + max_off + 7)
    days = min(days, 365)
    horizon = days * 24
    model = cp_model.CpModel()
    starts = {}
    ends = {}
    assignments = {}
    present = {}
    member_intervals: dict[str, list] = {m.name: [] for m in members}
    for member in members:
        cap = max(1, math.floor(member.hours_per_day))
        off = set(member.unavailable_dates)
        for day in range(days):
            current = start_date + timedelta(days=day)
            unavailable = current in off or (not weekends_working and current.weekday() >= 5)
            blocked_start = day * 24 if unavailable else day * 24 + cap
            blocked_size = 24 if unavailable else 24 - cap
            if blocked_size:
                member_intervals[member.name].append(model.NewFixedSizeIntervalVar(blocked_start, blocked_size,
                                                                                    f"off_{member.name}_{day}"))
    for task in tasks:
        if task.status == "completed":
            continue
        count = math.ceil(task.estimated_hours)
        if count > horizon:
            return ScheduleResult(status="INFEASIBLE", project_duration=duration, critical_path=critical,
                                  tasks=timings, reason="Task exceeds the scheduling horizon.")
        present[task.key] = model.NewBoolVar(f"present_{task.key}")
        choices = []
        for member in members:
            choice = model.NewBoolVar(f"assigned_{task.key}_{member.name}")
            assignments[task.key, member.name] = choice
            choices.append(choice)
            if task.suggested_assignee and task.suggested_assignee != member.name:
                model.Add(choice == 0)
            previous = None
            for i in range(count):
                start = model.NewIntVar(0, horizon-1, f"s_{task.key}_{member.name}_{i}")
                end = model.NewIntVar(1, horizon, f"e_{task.key}_{member.name}_{i}")
                interval = model.NewOptionalIntervalVar(start, 1, end, choice, f"slot_{task.key}_{member.name}_{i}")
                member_intervals[member.name].append(interval)
                if previous is None:
                    starts[task.key, member.name] = start
                else:
                    model.Add(start >= previous).OnlyEnforceIf(choice)
                previous = end
            ends[task.key, member.name] = previous
        model.Add(sum(choices) == present[task.key])
        if not task.optional or deadline_days is None:
            model.Add(present[task.key] == 1)
    for member in members:
        model.AddNoOverlap(member_intervals[member.name])
    for task in tasks:
        if task.status == "completed":
            continue
        for parent in task.depends_on:
            if parent not in present:
                continue
            for m1 in members:
                for m2 in members:
                    model.Add(starts[task.key, m1.name] >= ends[parent, m2.name]).OnlyEnforceIf(
                        [assignments[task.key, m1.name], assignments[parent, m2.name]])
            model.Add(present[task.key] <= present[parent])
    makespan = model.NewIntVar(0, horizon, "makespan")
    for task in tasks:
        if task.key in present:
            for member in members:
                model.Add(makespan >= ends[task.key, member.name]).OnlyEnforceIf(assignments[task.key, member.name])
    if deadline_days is not None:
        model.Add(makespan <= max(0, deadline_days) * 24)
    priority = {"critical": 1000, "high": 300, "medium": 100, "low": 30}
    optional_value = sum(priority[t.priority] * present[t.key] for t in tasks if t.optional and t.key in present)
    model.Minimize(makespan - horizon * 1000 * optional_value)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    result = solver.Solve(model)
    if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        cuts = [t.key for t in tasks if t.optional]
        unconstrained = optimize(tasks, members, start_date, None, weekends_working) if deadline_days is not None else None
        return ScheduleResult(status="INFEASIBLE", project_duration=duration, critical_path=critical,
                              tasks=unconstrained.tasks if unconstrained else timings,
                              end_date=unconstrained.end_date if unconstrained else None, meets_deadline=False,
                              reason="The required work cannot fit within the deadline or member availability.",
                              suggestions=[f"Consider dropping optional tasks: {', '.join(cuts)}"] if cuts else
                                          ["Increase the deadline or add member capacity."])
    by_key = {t.key: t for t in timings}
    dropped = []
    for task in tasks:
        timing = by_key[task.key]
        if task.key not in present:
            timing.start_date = start_date
            timing.end_date = start_date
            continue
        if not solver.Value(present[task.key]):
            dropped.append(task.key)
            continue
        member = next(m for m in members if solver.Value(assignments[task.key, m.name]))
        timing.assignee = member.name
        timing.start_date = start_date + timedelta(days=solver.Value(starts[task.key, member.name]) // 24)
        timing.end_date = start_date + timedelta(days=(solver.Value(ends[task.key, member.name])-1) // 24)
    end_date = start_date + timedelta(days=max(0, (solver.Value(makespan)-1) // 24))
    return ScheduleResult(status="OPTIMAL" if result == cp_model.OPTIMAL else "FEASIBLE",
                          project_duration=duration, critical_path=critical, tasks=timings,
                          end_date=end_date, meets_deadline=deadline_days is None or
                          solver.Value(makespan) <= deadline_days * 24, dropped_tasks=dropped,
                          suggestions=[f"Optional tasks removed to meet the deadline: {', '.join(dropped)}"] if dropped else [])
