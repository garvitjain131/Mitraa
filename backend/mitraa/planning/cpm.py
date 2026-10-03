from __future__ import annotations

from mitraa.planning.graph import topological_order
from mitraa.planning.models import TaskSpec, TaskTiming


def calculate(tasks: list[TaskSpec]) -> tuple[list[TaskTiming], float, list[str]]:
    by_key = {task.key: task for task in tasks}
    order = topological_order([t.key for t in tasks], {t.key: t.depends_on for t in tasks})
    successors: dict[str, list[str]] = {key: [] for key in order}
    for task in tasks:
        for parent in task.depends_on:
            successors[parent].append(task.key)
    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    for key in order:
        task = by_key[key]
        es[key] = max((ef[parent] for parent in task.depends_on), default=0)
        ef[key] = es[key] + (0 if task.status == "completed" else task.estimated_hours)
    duration = max(ef.values(), default=0)
    ls: dict[str, float] = {}
    lf: dict[str, float] = {}
    for key in reversed(order):
        lf[key] = min((ls[child] for child in successors[key]), default=duration)
        ls[key] = lf[key] - (ef[key] - es[key])
    timings = [TaskTiming(key=key, title=by_key[key].title, status=by_key[key].status,
                          priority=by_key[key].priority, optional=by_key[key].optional,
                          estimated_hours=by_key[key].estimated_hours,
                          es=es[key], ef=ef[key], ls=ls[key], lf=lf[key],
                          slack=round(ls[key]-es[key], 8), is_critical=abs(ls[key]-es[key]) < 1e-8,
                          depends_on=by_key[key].depends_on) for key in order]
    return timings, duration, [t.key for t in timings if t.is_critical]
