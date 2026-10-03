from __future__ import annotations

from fastapi.testclient import TestClient

from mitraa import main
from mitraa.db.session import get_session


def test_memory_and_project_api(session_factory):
    def provide():
        with session_factory() as session:
            yield session
    main.app.dependency_overrides[get_session] = provide
    client = TestClient(main.app)
    try:
        memory = client.post("/memory", json={"type": "project", "key": "current_project", "value": "NOVA"})
        assert memory.status_code == 200
        assert client.get("/memory").json()[0]["value"] == "NOVA"
        assert client.delete(f"/memory/{memory.json()['id']}").status_code == 200
        assert client.get("/memory").json() == []

        project = client.post("/projects", json={"name": "Hackathon", "deadline_days": 3}).json()
        pid = project["id"]
        first = client.post("/tasks", params={"project_id": pid}, json={"key": "a", "title": "Build",
                            "estimated_hours": 6}).json()
        assert first["key"] == "a"
        second = client.post("/tasks", params={"project_id": pid}, json={"key": "b", "title": "Test",
                             "estimated_hours": 6, "depends_on": ["a"]}).json()
        assert second["key"] == "b"
        assert len(client.get("/tasks", params={"project_id": pid}).json()) == 2
        schedule = client.get(f"/projects/{pid}/schedule").json()
        assert schedule["critical_path"] == ["a", "b"]
        assert schedule["status"] in {"OPTIMAL", "FEASIBLE"}
        diff = client.post(f"/projects/{pid}/simulate", json={"type": "member_unavailable",
                           "member": "You", "days": 1}).json()
        assert diff["delta_days"] == 1
        assert client.patch(f"/tasks/{first['id']}", json={"status": "completed"}).status_code == 200
        assert client.delete(f"/projects/{pid}").status_code == 409
        assert client.delete(f"/projects/{pid}", params={"confirmed": "true"}).status_code == 200
        assert client.get(f"/projects/{pid}").status_code == 404
    finally:
        main.app.dependency_overrides.clear()
