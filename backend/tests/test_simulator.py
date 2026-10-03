from datetime import date

from mitraa.planning.models import MemberSpec, Scenario, TaskSpec
from mitraa.planning.simulator import simulate


def test_losing_a_day_shifts_completion_without_changing_db():
    tasks = [TaskSpec(key="a", title="A", estimated_hours=6),
             TaskSpec(key="b", title="B", estimated_hours=6, depends_on=["a"])]
    members = [MemberSpec(name="me")]
    diff = simulate(tasks, members, date(2026, 10, 3), None,
                    Scenario(type="member_unavailable", member="me", days=1))
    assert diff.old_end_date == date(2026, 10, 4)
    assert diff.new_end_date == date(2026, 10, 5)
    assert diff.delta_days == 1
    assert {t["task"] for t in diff.tasks_delayed} == {"a", "b"}
    assert members[0].unavailable_dates == []


def test_estimate_change_recomputes_path():
    tasks = [TaskSpec(key="a", title="A", estimated_hours=6),
             TaskSpec(key="b", title="B", estimated_hours=3)]
    diff = simulate(tasks, [MemberSpec(name="one"), MemberSpec(name="two")], date(2026, 10, 3), None,
                    Scenario(type="estimate_change", task="b", new_hours=12))
    assert "b" in diff.tasks_newly_critical
    assert tasks[1].estimated_hours == 3
