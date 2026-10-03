from __future__ import annotations

from datetime import date, timedelta

from mitraa.planning.models import MemberSpec


def work_date(start: date, hours: float, member: MemberSpec, weekends_working: bool = True) -> date:
    """Date containing an hour offset; zero is the project start."""
    current = start
    remaining = max(0.0, hours)
    unavailable = set(member.unavailable_dates)
    while current in unavailable or (not weekends_working and current.weekday() >= 5):
        current += timedelta(days=1)
    while remaining > member.hours_per_day:
        remaining -= member.hours_per_day
        current += timedelta(days=1)
        while current in unavailable or (not weekends_working and current.weekday() >= 5):
            current += timedelta(days=1)
    return current
