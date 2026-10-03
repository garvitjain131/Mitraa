from datetime import date

from mitraa.planning.models import MemberSpec, TaskSpec
from mitraa.planning.optimizer import optimize


def task(key, hours, parents=(), optional=False):
    return TaskSpec(key=key, title=key, estimated_hours=hours, depends_on=list(parents), optional=optional)


def test_single_member_chain():
    result = optimize([task("a", 6), task("b", 6, ["a"])], [MemberSpec(name="me")], date(2026, 10, 3))
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.end_date == date(2026, 10, 4)
    assert result.critical_path == ["a", "b"]


def test_parallel_branches_use_two_members():
    result = optimize([task("a", 6), task("b", 6)], [MemberSpec(name="a"), MemberSpec(name="b")], date(2026, 10, 3))
    assert result.end_date == date(2026, 10, 3)


def test_member_unavailable_and_infeasible_deadline():
    member = MemberSpec(name="me", unavailable_dates=[date(2026, 10, 3)])
    result = optimize([task("a", 6)], [member], date(2026, 10, 3), deadline_days=1)
    assert result.status == "INFEASIBLE"
    assert result.end_date == date(2026, 10, 4)
    assert result.meets_deadline is False


def test_optional_cut_when_deadline_tight():
    result = optimize([task("required", 6), task("nice", 6, optional=True)],
                      [MemberSpec(name="me")], date(2026, 10, 3), deadline_days=1)
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.dropped_tasks == ["nice"]
