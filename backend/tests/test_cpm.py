from __future__ import annotations

import pytest

from mitraa.planning.cpm import calculate
from mitraa.planning.graph import topological_order
from mitraa.planning.models import TaskSpec


def task(key, hours, parents=(), status="todo"):
    return TaskSpec(key=key, title=key, estimated_hours=hours, depends_on=list(parents), status=status)


def test_chain():
    timings, duration, path = calculate([task("a", 2), task("b", 3, ["a"]), task("c", 4, ["b"])])
    assert duration == 9
    assert path == ["a", "b", "c"]
    assert [(t.es, t.ef, t.ls, t.lf, t.slack) for t in timings] == [
        (0, 2, 0, 2, 0), (2, 5, 2, 5, 0), (5, 9, 5, 9, 0)]


def test_diamond_parallel_slack():
    timings, duration, path = calculate([task("a", 2), task("b", 4, ["a"]), task("c", 1, ["a"]),
                                         task("d", 3, ["b", "c"])])
    by_key = {t.key: t for t in timings}
    assert duration == 9
    assert path == ["a", "b", "d"]
    assert (by_key["c"].es, by_key["c"].ef, by_key["c"].ls, by_key["c"].lf, by_key["c"].slack) == (2, 3, 5, 6, 3)


def test_completed_duration_is_zero():
    _, duration, _ = calculate([task("a", 4, status="completed"), task("b", 3, ["a"])])
    assert duration == 3


def test_cycle_names_path():
    with pytest.raises(ValueError, match=r"a -> b -> a"):
        topological_order(["a", "b"], {"a": ["b"], "b": ["a"]})
