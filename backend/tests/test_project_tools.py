import json

from mitraa.agent.orchestrator import Orchestrator
from mitraa.planning import service
from mitraa.planning.models import TaskSpec
from mitraa.tools.project_tools import GeneratePlanArgs, GenerateProjectPlan
from tests.fakes import FakeLLM, act, call, say


def test_generate_plan_repairs_cycle_once(make_ctx):
    ctx = make_ctx()
    project = service.create_project(ctx.session, "Demo", deadline_days=10)
    ctx.session.commit()
    tasks = [{"key": f"t{i}", "title": f"Task {i}", "estimated_hours": 3,
              "depends_on": [f"t{i-1}"] if i else []} for i in range(8)]
    invalid = json.loads(json.dumps(tasks))
    invalid[0]["depends_on"] = ["t7"]

    class Planner:
        requests = []
        def plan_json(self, prompt):
            self.requests.append(prompt)
            return json.dumps({"tasks": invalid if len(self.requests) == 1 else tasks})

    planner = Planner()
    ctx.llm = planner
    result = GenerateProjectPlan().run(GeneratePlanArgs(project_id=project.id, goal="Demo", deadline_days=10), ctx)
    assert result.ok
    assert len(service.project_data(ctx.session, project.id)[1]) == 8
    assert "Dependency cycle" in planner.requests[1]


def test_cycle_tool_is_audited_and_rejected(session, settings, sandbox):
    project = service.create_project(session, "Demo")
    service.add_task(session, project.id, TaskSpec(key="a", title="A", estimated_hours=1))
    service.add_task(session, project.id, TaskSpec(key="b", title="B", estimated_hours=1, depends_on=["a"]))
    session.commit()
    llm = FakeLLM([act(call("add_dependency", {"project_id": project.id, "task": "a", "depends_on": "b"})), say("Cycle rejected")])
    response = Orchestrator(llm, session, settings, sandbox=sandbox).run(None, "Add dependency")
    assert response.steps[0].status == "error"
    assert "Dependency cycle" in response.steps[0].summary
