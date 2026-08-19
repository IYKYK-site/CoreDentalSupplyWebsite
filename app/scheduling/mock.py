from __future__ import annotations
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from uuid import uuid4

from .base import Scheduler, Slot, Appointment


class MockScheduler(Scheduler):
    def __init__(self, timezone="America/New_York", duration_minutes=60):
        self.tz = ZoneInfo(timezone)
        self.duration = duration_minutes
        self.items: dict[str, Appointment] = {}

    def get_available_slots(self, date: str, provider_id: str | None = None) -> list[Slot]:
        d = datetime.fromisoformat(date).date()
        start = datetime.combine(d, time(9, 0), self.tz)
        return [Slot(start + timedelta(hours=i), start + timedelta(hours=i+1)) for i in range(7)]

    def find_first_available_time(
        self,
        provider_id=None,
        duration_minutes=None,
        buffer_minutes=None,
        days_to_search=30,
    ):
        not_before = datetime.now(self.tz) + timedelta(hours=1)

        for day_offset in range(days_to_search + 1):
            search_date = not_before.date() + timedelta(days=day_offset)
            for slot in self.get_available_slots(
                search_date.isoformat(), provider_id
            ):
                if slot.start >= not_before:
                    return slot

        return None

    def create_appointment(self, patient_name, patient_phone, provider_id, start_iso, duration_minutes=None, reason=""):
        start = datetime.fromisoformat(start_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)
        end = start + timedelta(minutes=duration_minutes or self.duration)
        appt = Appointment(str(uuid4()), patient_name, patient_phone, provider_id, start, end)
        self.items[appt.id] = appt
        return appt

    def find_appointments(self, patient_name="", patient_phone=""):
        return [
            a for a in self.items.values()
            if (not patient_name or patient_name.lower() in a.patient_name.lower())
            and (not patient_phone or patient_phone == a.patient_phone)
            and a.status != "cancelled"
        ]

    def reschedule_appointment(self, appointment_id, new_start_iso):
        appt = self.items[appointment_id]
        duration = appt.end - appt.start
        start = datetime.fromisoformat(new_start_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)
        appt.start = start
        appt.end = start + duration
        return appt

    def cancel_appointment(self, appointment_id):
        self.items[appointment_id].status = "cancelled"
        return True
