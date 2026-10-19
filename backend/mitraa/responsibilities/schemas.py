from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Status = Literal['captured', 'planned', 'active', 'waiting_for_user', 'waiting_external',
                 'ready_for_review', 'completed', 'paused', 'cancelled']
Priority = Literal['low', 'medium', 'high', 'critical']


def timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Choose a valid IANA timezone, for example Asia/Kolkata.') from None
    return value


class ResponsibilityCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    outcome: str = Field(default='', max_length=10000)
    next_action: str = Field(default='', max_length=5000)
    notes: str = Field(default='', max_length=20000)
    sources: list[str] = Field(default_factory=list, max_length=50)
    priority: Priority = 'medium'
    deadline: AwareDatetime | None = None
    timezone: str = 'UTC'
    _zone = field_validator('timezone')(timezone_name)

    @field_validator('sources')
    @classmethod
    def validate_sources(cls, values):
        if any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError('Source references must contain 1–2000 characters.')
        return list(dict.fromkeys(v.strip() for v in values))


class ResponsibilityPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    outcome: str | None = Field(default=None, max_length=10000)
    next_action: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=20000)
    sources: list[str] | None = Field(default=None, max_length=50)
    priority: Priority | None = None
    deadline: AwareDatetime | None = None
    timezone: str | None = None
    status: Status | None = None
    completion_note: str | None = Field(default=None, max_length=5000)


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    responsibility_id: str
    message: str = Field(min_length=1, max_length=500)
    due_at: AwareDatetime


class SnoozeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    due_at: AwareDatetime


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timezone: str = 'UTC'
    quiet_enabled: bool = True
    quiet_start: str = '22:00'
    quiet_end: str = '08:00'
    notifications_enabled: bool = True
    _zone = field_validator('timezone')(timezone_name)

    @field_validator('quiet_start', 'quiet_end')
    @classmethod
    def clock(cls, value):
        import re
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('Use a 24-hour time in HH:MM format.')
        return value
