"""Thin audited tool wrappers over the deterministic project service."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from mitraa.agent.prompts import PLAN_GENERATION_PROMPT
from mitraa.memory.store import list_memories
from mitraa.planning import service
from mitraa.planning.graph import topological_order
from mitraa.planning.models import MemberSpec, ProjectSpec, Scenario, TaskSpec
from mitraa.tools.base import RiskLevel, Tool, ToolContext, ToolResult


class CreateProjectArgs(BaseModel):
    name: str = Field(min_length=1)
    goal: str = ""
    deadline_days: int | None = Field(default=None, ge=1)
    team: list[MemberSpec] = Field(default_factory=lambda: [MemberSpec(name="You")])


class CreateProject(Tool):
    name = "create_project"
    description = "Create a project with a name, goal, deadline in days and team. Use generate_project_plan next to fill tasks."
    args_model = CreateProjectArgs
    base_risk = RiskLevel.LOW

    def run(self, args: CreateProjectArgs, ctx: ToolContext) -> ToolResult:
        row = service.create_project(ctx.session, args.name, args.goal, args.deadline_days, args.team)
        return ToolResult.success(f"Created project {row.name}.", project=service.project_dict(row))


class GeneratePlanArgs(BaseModel):
    project_id: str
    goal: str
    deadline_days: int = Field(ge=1)
    team: str = "One person, 6 hours per day"


class GenerateProjectPlan(Tool):
    name = "generate_project_plan"
    description = "Ask the planning model for 8–15 task deliverables, validate dependencies, and save them to an existing project."
    args_model = GeneratePlanArgs
    base_risk = RiskLevel.LOW

    def run(self, args: GeneratePlanArgs, ctx: ToolContext) -> ToolResult:
        service.project_or_error(ctx.session, args.project_id)
        if ctx.llm is None:
            return ToolResult.fail("Planning model is unavailable.")
        memories = "\n".join(f"{m.key}: {m.value}" for m in list_memories(ctx.session)[:8])
        prompt = PLAN_GENERATION_PROMPT.format(goal=args.goal, deadline_days=args.deadline_days,
                                                team_description=args.team, memories=memories)
        for attempt in range(2):
            try:
                raw = ctx.llm.plan_json(prompt) if hasattr(ctx.llm, "plan_json") else ctx.llm.chat(
                    [{"role": "user", "content": prompt}], []).content
                spec = ProjectSpec.model_validate(json.loads(raw))
                if not 8 <= len(spec.tasks) <= 15:
                    raise ValueError("A plan needs 8 to 15 tasks.")
                if any(task.estimated_hours > 16 for task in spec.tasks):
                    raise ValueError("Split tasks longer than 16 hours.")
                names = {member.name for member in service.project_data(ctx.session, args.project_id)[2]}
                if any(task.suggested_assignee and task.suggested_assignee not in names for task in spec.tasks):
                    raise ValueError("A suggested assignee is not on the project team.")
                topological_order([t.key for t in spec.tasks], {t.key: t.depends_on for t in spec.tasks})
                service.save_plan(ctx.session, args.project_id, spec)
                return ToolResult.success(f"Saved {len(spec.tasks)} tasks.", tasks=[t.model_dump() for t in spec.tasks])
            except (ValueError, json.JSONDecodeError) as exc:
                prompt += f"\nYour previous result was invalid: {exc}. Return corrected JSON only."
        return ToolResult.fail("The planning model returned an invalid plan twice.")


class AddTaskArgs(BaseModel):
    project_id: str
    task: TaskSpec


class AddTask(Tool):
    name = "add_task"
    description = "Add one task with estimated hours and dependency keys to a project."
    args_model = AddTaskArgs
    base_risk = RiskLevel.LOW

    def run(self, args: AddTaskArgs, ctx: ToolContext) -> ToolResult:
        row = service.add_task(ctx.session, args.project_id, args.task)
        return ToolResult.success(f"Added {row.title}.", task_id=row.id, key=row.key)


class UpdateTaskArgs(BaseModel):
    task_id: str
    changes: dict


class UpdateTask(Tool):
    name = "update_task"
    description = "Update a task title, hours, priority, status, assignee or optional flag."
    args_model = UpdateTaskArgs
    base_risk = RiskLevel.LOW

    def run(self, args: UpdateTaskArgs, ctx: ToolContext) -> ToolResult:
        row = service.update_task(ctx.session, args.task_id, args.changes)
        return ToolResult.success(f"Updated {row.title}.", task_id=row.id)


class CompleteTaskArgs(BaseModel):
    task_id: str


class CompleteTask(Tool):
    name = "complete_task"
    description = "Mark a project task completed."
    args_model = CompleteTaskArgs
    base_risk = RiskLevel.LOW

    def run(self, args: CompleteTaskArgs, ctx: ToolContext) -> ToolResult:
        row = service.update_task(ctx.session, args.task_id, {"status": "completed"})
        return ToolResult.success(f"Completed {row.title}.")


class AddDependencyArgs(BaseModel):
    project_id: str
    task: str
    depends_on: str


class AddDependency(Tool):
    name = "add_dependency"
    description = "Add a task dependency. Cycles are rejected with the cycle path."
    args_model = AddDependencyArgs
    base_risk = RiskLevel.LOW

    def run(self, args: AddDependencyArgs, ctx: ToolContext) -> ToolResult:
        service.add_dependency(ctx.session, args.project_id, args.task, args.depends_on)
        return ToolResult.success(f"{args.task} now depends on {args.depends_on}.")


class ProjectIdArgs(BaseModel):
    project_id: str


class ListTasks(Tool):
    name = "list_tasks"
    description = "List project tasks, estimates, status and dependencies."
    args_model = ProjectIdArgs
    base_risk = RiskLevel.LOW

    def run(self, args: ProjectIdArgs, ctx: ToolContext) -> ToolResult:
        _, tasks, _ = service.project_data(ctx.session, args.project_id)
        return ToolResult.success(f"Found {len(tasks)} tasks.", tasks=[t.model_dump() for t in tasks])


class GetProjectStatus(Tool):
    name = "get_project_status"
    description = "Get project metadata, task progress and computed completion date."
    args_model = ProjectIdArgs
    base_risk = RiskLevel.LOW

    def run(self, args: ProjectIdArgs, ctx: ToolContext) -> ToolResult:
        project, tasks, _ = service.project_data(ctx.session, args.project_id)
        result = service.schedule(ctx.session, args.project_id)
        return ToolResult.success(f"{project.name}: {sum(t.status == 'completed' for t in tasks)}/{len(tasks)} tasks complete.",
                                  project=service.project_dict(project), schedule=result.model_dump(mode="json"))


class CalculateCriticalPath(Tool):
    name = "calculate_critical_path"
    description = "Compute ES, EF, LS, LF, slack, critical tasks and completion date. Never calculate these mentally."
    args_model = ProjectIdArgs
    base_risk = RiskLevel.LOW

    def run(self, args: ProjectIdArgs, ctx: ToolContext) -> ToolResult:
        result = service.schedule(ctx.session, args.project_id)
        return ToolResult.success(f"Critical tasks: {', '.join(result.critical_path)}. Completion: {result.end_date}.",
                                  schedule=result.model_dump(mode="json"))


class OptimizeArgs(BaseModel):
    project_id: str
    deadline_days: int | None = Field(default=None, ge=1)


class OptimizeSchedule(Tool):
    name = "optimize_schedule"
    description = "Compute a CP-SAT team schedule for a project and optional new deadline in days; report infeasibility and optional cuts."
    args_model = OptimizeArgs
    base_risk = RiskLevel.LOW

    def run(self, args: OptimizeArgs, ctx: ToolContext) -> ToolResult:
        result = service.schedule(ctx.session, args.project_id, args.deadline_days)
        return ToolResult.success(f"Schedule {result.status}; completion {result.end_date}; dropped: {result.dropped_tasks}.",
                                  schedule=result.model_dump(mode="json"))


class SimulateArgs(BaseModel):
    project_id: str
    scenario: Scenario


class SimulateScenario(Tool):
    name = "simulate_scenario"
    description = "What-if simulation: member unavailable, task estimate change, deadline change, task removal or new member. Returns old and new dates and affected tasks."
    args_model = SimulateArgs
    base_risk = RiskLevel.LOW

    def run(self, args: SimulateArgs, ctx: ToolContext) -> ToolResult:
        diff = service.scenario(ctx.session, args.project_id, args.scenario)
        return ToolResult.success(f"Completion: {diff.old_end_date} → {diff.new_end_date} ({diff.delta_days:+} days).",
                                  diff=diff.model_dump(mode="json"))


class WhatShouldIWorkOn(Tool):
    name = "what_should_i_work_on"
    description = "Return currently unblocked tasks ordered by criticality, slack and priority."
    args_model = ProjectIdArgs
    base_risk = RiskLevel.LOW

    def run(self, args: ProjectIdArgs, ctx: ToolContext) -> ToolResult:
        _, tasks, _ = service.project_data(ctx.session, args.project_id)
        schedule = service.schedule(ctx.session, args.project_id)
        by_key = {t.key: t for t in tasks}
        rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
        ready = [timing for timing in schedule.tasks if by_key[timing.key].status != "completed" and
                 all(by_key[parent].status == "completed" for parent in by_key[timing.key].depends_on)]
        ready.sort(key=lambda t: (-int(t.is_critical), t.slack, -rank[by_key[t.key].priority]))
        return ToolResult.success(f"{len(ready)} tasks ready.", tasks=[{"key": t.key, "title": by_key[t.key].title,
                                                                         "slack": t.slack, "is_critical": t.is_critical}
                                                                        for t in ready])


class DeleteProject(Tool):
    name = "delete_project"
    description = "Permanently delete a project, its tasks and team. Always requires the user's approval."
    args_model = ProjectIdArgs
    base_risk = RiskLevel.HIGH

    def describe(self, args: ProjectIdArgs) -> str:
        return f"Delete project {args.project_id} and its tasks"

    def run(self, args: ProjectIdArgs, ctx: ToolContext) -> ToolResult:
        service.delete_project(ctx.session, args.project_id)
        return ToolResult.success("Project deleted.")


ALL_PROJECT_TOOLS = [CreateProject(), GenerateProjectPlan(), AddTask(), UpdateTask(), CompleteTask(),
                     AddDependency(), ListTasks(), GetProjectStatus(), CalculateCriticalPath(),
                     OptimizeSchedule(), SimulateScenario(), WhatShouldIWorkOn(), DeleteProject()]
