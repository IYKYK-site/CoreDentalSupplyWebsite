from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from .base import Scheduler, Slot, Appointment


SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarScheduler(Scheduler):
    """
    Sprint-0 Google Calendar adapter.

    POC identity strategy:
    - Run scripts/google_auth.py once as the calendar owner.
    - It creates token.json.
    - The service refreshes that token when needed.

    Appointment metadata is stored in extendedProperties.private so we can
    search by patient phone/provider without needing our own database.
    """

    def __init__(self, config):
        self.config = config
        self.tz = ZoneInfo(config.office.timezone)
        self.calendar_id = config.appointments.calendar_id
        self.creds = self._load_credentials()
        self.service = build("calendar", "v3", credentials=self.creds, cache_discovery=False)

    def _load_credentials(self):
        token_file = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        if not creds.valid:
            raise RuntimeError("Google credentials are not valid. Run scripts/google_auth.py.")
        return creds

    def _day_bounds(self, date: str):
        d = datetime.fromisoformat(date).date()
        day_start = datetime(d.year, d.month, d.day, tzinfo=self.tz)
        return day_start, day_start + timedelta(days=1)

    def _office_window(self, date: str):
        d = datetime.fromisoformat(date).date()
        day_name = d.strftime("%A").lower()
        hours = self.config.office_hours.get(day_name)
        if not hours:
            return None
        h1, m1 = map(int, hours["open"].split(":"))
        h2, m2 = map(int, hours["close"].split(":"))
        return (
            datetime(d.year, d.month, d.day, h1, m1, tzinfo=self.tz),
            datetime(d.year, d.month, d.day, h2, m2, tzinfo=self.tz),
        )

    def get_available_slots(
        self,
        date: str,
        provider_id: str | None = None,
        duration_minutes: int | None = None,
        buffer_minutes: int | None = None,
    ):

        window = self._office_window(date)
        if not window:
            return []

        open_dt, close_dt = window

        body = {
            "timeMin": open_dt.isoformat(),
            "timeMax": close_dt.isoformat(),
            "timeZone": self.config.office.timezone,
            "items": [{"id": self.calendar_id}],
        }

        fb = self.service.freebusy().query(body=body).execute()

        busy = [
            (
                datetime.fromisoformat(x["start"]),
                datetime.fromisoformat(x["end"]),
            )
            for x in fb["calendars"][self.calendar_id].get("busy", [])
        ]

        # Add lunch as blocked time
        lunch = self.config.lunch
        if lunch.get("enabled"):
            d = datetime.fromisoformat(date).date()

            lunch_start_hour, lunch_start_minute = map(
                int, lunch["start"].split(":")
            )
            lunch_end_hour, lunch_end_minute = map(
                int, lunch["end"].split(":")
            )

            lunch_start = datetime(
                d.year,
                d.month,
                d.day,
                lunch_start_hour,
                lunch_start_minute,
                tzinfo=self.tz,
            )

            lunch_end = datetime(
                d.year,
                d.month,
                d.day,
                lunch_end_hour,
                lunch_end_minute,
                tzinfo=self.tz,
            )

            busy.append((lunch_start, lunch_end))

        duration = timedelta(
            minutes=duration_minutes
            or self.config.appointments.default_duration_minutes
        )

        buffer = timedelta(
            minutes=(
                buffer_minutes
                if buffer_minutes is not None
                else self.config.appointments.default_buffer_minutes
            )
        )

        step = timedelta(
            minutes=self.config.appointments.slot_increment_minutes
        )

        slots = []
        cur = open_dt

        while cur + duration <= close_dt:
            appointment_end = cur + duration

            # Include the required post-appointment buffer
            protected_end = appointment_end + buffer

            overlaps = any(
                cur < busy_end and protected_end > busy_start
                for busy_start, busy_end in busy
            )

            if not overlaps:
                slots.append(Slot(cur, appointment_end))

            cur += step

        return slots

    def create_appointment(self, patient_name, patient_phone, provider_id, start_iso, duration_minutes=None, reason=""):
        start = datetime.fromisoformat(start_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)
        duration = duration_minutes or self.config.appointments.default_duration_minutes
        end = start + timedelta(minutes=duration)

        event = {
            "summary": f"Dental Appointment - {patient_name}",
            "description": reason,
            "start": {"dateTime": start.isoformat(), "timeZone": self.config.office.timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": self.config.office.timezone},
            "extendedProperties": {
                "private": {
                    "patient_phone": patient_phone,
                    "provider_id": provider_id,
                    "source": "core-ai-receptionist-poc",
                }
            },
        }
        created = self.service.events().insert(calendarId=self.calendar_id, body=event).execute()
        return self._to_appointment(created)

    def find_appointments(self, patient_name="", patient_phone=""):
        now = datetime.now(self.tz) - timedelta(days=1)
        kwargs = {
            "calendarId": self.calendar_id,
            "timeMin": now.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 25,
        }
        if patient_phone:
            kwargs["privateExtendedProperty"] = f"patient_phone={patient_phone}"
        events = self.service.events().list(**kwargs).execute().get("items", [])
        out = []
        for event in events:
            appt = self._to_appointment(event)
            if patient_name and patient_name.lower() not in appt.patient_name.lower():
                continue
            out.append(appt)
        return out

    def reschedule_appointment(self, appointment_id, new_start_iso):
        event = self.service.events().get(calendarId=self.calendar_id, eventId=appointment_id).execute()
        old_start = datetime.fromisoformat(event["start"]["dateTime"])
        old_end = datetime.fromisoformat(event["end"]["dateTime"])
        duration = old_end - old_start

        new_start = datetime.fromisoformat(new_start_iso)
        if new_start.tzinfo is None:
            new_start = new_start.replace(tzinfo=self.tz)
        new_end = new_start + duration

        event["start"] = {"dateTime": new_start.isoformat(), "timeZone": self.config.office.timezone}
        event["end"] = {"dateTime": new_end.isoformat(), "timeZone": self.config.office.timezone}
        updated = self.service.events().update(
            calendarId=self.calendar_id, eventId=appointment_id, body=event
        ).execute()
        return self._to_appointment(updated)

    def cancel_appointment(self, appointment_id):
        self.service.events().delete(calendarId=self.calendar_id, eventId=appointment_id).execute()
        return True

    def _to_appointment(self, event):
        start = datetime.fromisoformat(event["start"]["dateTime"])
        end = datetime.fromisoformat(event["end"]["dateTime"])
        props = event.get("extendedProperties", {}).get("private", {})
        summary = event.get("summary", "")
        patient = summary.removeprefix("Dental Appointment - ").strip()
        return Appointment(
            id=event["id"],
            patient_name=patient,
            patient_phone=props.get("patient_phone", ""),
            provider_id=props.get("provider_id", ""),
            start=start,
            end=end,
            status=event.get("status", "confirmed"),
        )
