from __future__ import annotations


def topological_order(keys: list[str], dependencies: dict[str, list[str]]) -> list[str]:
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate task key")
    known = set(keys)
    for key, parents in dependencies.items():
        if key not in known:
            raise ValueError(f"Unknown task: {key}")
        for parent in parents:
            if parent not in known:
                raise ValueError(f"Task {key} depends on unknown task {parent}")
    visiting: list[str] = []
    visited: set[str] = set()
    order: list[str] = []

    def visit(key: str) -> None:
        if key in visiting:
            cycle = visiting[visiting.index(key):] + [key]
            raise ValueError("Dependency cycle: " + " -> ".join(cycle))
        if key in visited:
            return
        visiting.append(key)
        for parent in dependencies.get(key, []):
            visit(parent)
        visiting.pop()
        visited.add(key)
        order.append(key)

    for key in keys:
        visit(key)
    return order
