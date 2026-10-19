"""Responsibility tools share the same validated service as the local desktop."""
from pydantic import BaseModel, Field, AwareDatetime
from mitraa.responsibilities import service
from mitraa.responsibilities.schemas import ResponsibilityCreate, ResponsibilityPatch, ReminderCreate, Status
from mitraa.tools.base import Tool, ToolContext, ToolResult, RiskLevel


class CreateResponsibility(Tool):
    name = 'create_responsibility'
    description = 'Capture an ongoing responsibility with its desired outcome, next action and source references. Dates must include a timezone offset. Clarify ambiguous deadlines; never invent them.'
    args_model = ResponsibilityCreate
    base_risk = RiskLevel.LOW

    def run(self, args, ctx):
        return ToolResult.success('Responsibility saved locally.', responsibility=service.create(ctx.session, args))


class ListArgs(BaseModel):
    status: Status | None = None


class ListResponsibilities(Tool):
    name = 'list_responsibilities'
    description = 'List saved responsibilities, including IDs and versions needed to update them. Use before selecting a responsibility; clarify ambiguous matches.'
    args_model = ListArgs
    base_risk = RiskLevel.LOW

    def run(self, args, ctx):
        return ToolResult.success('Saved responsibilities.', responsibilities=service.list_items(ctx.session, args.status))


class UpdateArgs(ResponsibilityPatch):
    responsibility_id: str


class UpdateResponsibility(Tool):
    name = 'update_responsibility'
    description = 'Update a saved responsibility using its current expected_version. Complete only after the user explicitly confirms completion, recording that confirmation in completion_note. Prepared is not submitted. Pausing suppresses reminders; reopening does not recreate closed reminders.'
    args_model = UpdateArgs
    base_risk = RiskLevel.LOW

    def run(self, args, ctx):
        data = ResponsibilityPatch.model_validate(args.model_dump(exclude={'responsibility_id'}, exclude_unset=True))
        return ToolResult.success('Responsibility updated.', responsibility=service.patch(ctx.session, args.responsibility_id, data))


class AddReminder(Tool):
    name = 'add_reminder'
    description = 'Save a local follow-up reminder for a responsibility at a future time with explicit UTC offset. Ask when the intended time is unclear. Local reminders need the desktop app running; overdue reminders catch up when it resumes.'
    args_model = ReminderCreate
    base_risk = RiskLevel.LOW

    def run(self, args, ctx):
        return ToolResult.success('Local reminder saved.', reminder=service.add_reminder(ctx.session, args))


class ReminderActionArgs(BaseModel):
    reminder_id: str
    action: str = Field(description='snooze, acknowledge or cancel')
    due_at: AwareDatetime | None = None


class ChangeReminder(Tool):
    name = 'change_reminder'
    description = 'Snooze a reminder to a specified future time, acknowledge it, or cancel it. Acknowledgement closes the reminder but does not complete the responsibility.'
    args_model = ReminderActionArgs
    base_risk = RiskLevel.LOW

    def run(self, args, ctx):
        return ToolResult.success('Reminder updated.', reminder=service.change_reminder(ctx.session, args.reminder_id, args.action, args.due_at))


class FollowUpArgs(BaseModel):
    pass


class GetFollowUp(Tool):
    name = 'get_follow_up'
    description = 'List saved reminders and overdue follow-ups without marking them read or delivered.'
    args_model = FollowUpArgs
    base_risk = RiskLevel.LOW

    def run(self, args, ctx):
        return ToolResult.success('Local follow-ups.', reminders=service.list_reminders(ctx.session))


ALL_RESPONSIBILITY_TOOLS = [CreateResponsibility(), ListResponsibilities(), UpdateResponsibility(),
                            AddReminder(), ChangeReminder(), GetFollowUp()]
