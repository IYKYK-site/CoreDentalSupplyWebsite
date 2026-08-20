from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import threading

import httplib2
from google_auth_httplib2 import AuthorizedHttp
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
        self._service_lock = threading.RLock()
        authorized_http = AuthorizedHttp(
            self.creds,
            http=httplib2.Http(timeout=20),
        )
        self.service = build(
            "calendar",
            "v3",
            http=authorized_http,
            cache_discovery=False,
        )

    def _execute(self, request_factory):
        """Serialize access to the shared, non-thread-safe Google client."""
        lock = getattr(self, "_service_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._service_lock = lock
        with lock:
            return request_factory().execute(num_retries=0)

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

    def _now(self):
        return datetime.now(self.tz)

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

        now = self._now()
        requested_date = datetime.fromisoformat(date).date()
        is_today = requested_date == now.date()

        open_dt, close_dt = window

        body = {
            "timeMin": open_dt.isoformat(),
            "timeMax": close_dt.isoformat(),
            "timeZone": self.config.office.timezone,
            "items": [{"id": self.calendar_id}],
        }

        fb = self._execute(
            lambda: self.service.freebusy().query(body=body)
        )

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

        while cur + duration + buffer <= close_dt:
            if is_today and cur <= now:
                cur += step
                continue
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

    def find_first_available_time(
        self,
        provider_id: str | None = None,
        duration_minutes: int | None = None,
        buffer_minutes: int | None = None,
        days_to_search: int = 30,
    ):
        """Return the earliest live-calendar slot at least one hour from now."""

        now = self._now()
        not_before = now + timedelta(hours=1)

        for day_offset in range(days_to_search + 1):
            search_date = now.date() + timedelta(days=day_offset)
            slots = self.get_available_slots(
                date=search_date.isoformat(),
                provider_id=provider_id,
                duration_minutes=duration_minutes,
                buffer_minutes=buffer_minutes,
            )

            for slot in slots:
                if slot.start >= not_before:
                    return slot

        return None

    def find_next_available_time(
        self,
        preferred_time: str,
        provider_id: str | None = None,
        duration_minutes: int | None = None,
        buffer_minutes: int | None = None,
        days_to_search: int = 30,
    ):
        """
        Find the next available appointment that starts at a specific time.

        preferred_time must be HH:MM, for example "15:00".
        """

        now = self._now()

        preferred_hour, preferred_minute = map(
            int,
            preferred_time.split(":")
        )

        for day_offset in range(days_to_search + 1):
            search_date = (now.date() + timedelta(days=day_offset))

            slots = self.get_available_slots(
                date=search_date.isoformat(),
                provider_id=provider_id,
                duration_minutes=duration_minutes,
                buffer_minutes=buffer_minutes,
            )

            for slot in slots:
                if (
                    slot.start.hour == preferred_hour
                    and slot.start.minute == preferred_minute
                ):
                    return slot

        return None
    def create_appointment(
        self,
        patient_name,
        patient_phone,
        patient_dob,
        provider_id,
        start_iso,
        duration_minutes=None,
        reason="",
    ):
        start = datetime.fromisoformat(start_iso)

        if start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)

        duration = (
            duration_minutes
            or self.config.appointments.default_duration_minutes
        )

        end = start + timedelta(minutes=duration)

        description = f"""PATIENT INFORMATION

    Name: {patient_name}
    Date of Birth: {patient_dob}
    Phone: {patient_phone}

    Reason / Procedure: {reason or "Not specified"}

    Created by: Claudia AI Receptionist
    """

        event = {
            "summary": f"Dental Appointment - {patient_name}",
            "description": description,

            "start": {
                "dateTime": start.isoformat(),
                "timeZone": self.config.office.timezone,
            },

            "end": {
                "dateTime": end.isoformat(),
                "timeZone": self.config.office.timezone,
            },

            "extendedProperties": {
                "private": {
                    "patient_phone": patient_phone,
                    "patient_dob": patient_dob,
                    "provider_id": provider_id,
                    "service": reason,
                    "source": "core-ai-receptionist-poc",
                }
            },
        }

        created = self._execute(
            lambda: self.service.events().insert(
                calendarId=self.calendar_id,
                body=event,
            )
        )

        return self._to_appointment(created)

    def find_appointments(
        self,
        patient_name="",
        patient_phone="",
        patient_dob="",
    ):
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

        events = self._execute(
            lambda: self.service.events().list(**kwargs)
        ).get("items", [])

        out = []

        for event in events:
            appt = self._to_appointment(event)

            if patient_name and patient_name.strip().lower() != appt.patient_name.strip().lower():
                continue

            private_props = event.get("extendedProperties", {}).get("private", {})
            stored_dob = private_props.get("patient_dob", "")

            if patient_dob and stored_dob != patient_dob:
                continue

            out.append(appt)

        return out

    def find_today_appointments(
        self,
        patient_phone="",
        patient_name="",
        patient_dob="",
    ):
        """Query today's appointments directly from Google Calendar."""

        today = self._now().date().isoformat()
        day_start, day_end = self._day_bounds(today)
        kwargs = {
            "calendarId": self.calendar_id,
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": 50,
        }
        if patient_phone and not (patient_name and patient_dob):
            kwargs["privateExtendedProperty"] = (
                f"patient_phone={patient_phone}"
            )

        events = self._execute(
            lambda: self.service.events().list(**kwargs)
        ).get("items", [])
        matches = []

        for event in events:
            appointment = self._to_appointment(event)
            private = event.get("extendedProperties", {}).get("private", {})

            if (
                patient_name
                and patient_name.strip().casefold()
                != appointment.patient_name.strip().casefold()
            ):
                continue
            if patient_dob and private.get("patient_dob", "") != patient_dob:
                continue

            matches.append(appointment)

        return matches

    def reschedule_appointment(self, appointment_id, new_start_iso):
        event = self._execute(
            lambda: self.service.events().get(
                calendarId=self.calendar_id, eventId=appointment_id
            )
        )
        old_start = datetime.fromisoformat(event["start"]["dateTime"])
        old_end = datetime.fromisoformat(event["end"]["dateTime"])
        duration = old_end - old_start

        new_start = datetime.fromisoformat(new_start_iso)
        if new_start.tzinfo is None:
            new_start = new_start.replace(tzinfo=self.tz)
        new_end = new_start + duration

        event["start"] = {"dateTime": new_start.isoformat(), "timeZone": self.config.office.timezone}
        event["end"] = {"dateTime": new_end.isoformat(), "timeZone": self.config.office.timezone}
        updated = self._execute(
            lambda: self.service.events().update(
                calendarId=self.calendar_id,
                eventId=appointment_id,
                body=event,
            )
        )
        return self._to_appointment(updated)

    def cancel_appointment(self, appointment_id):
        self._execute(
            lambda: self.service.events().delete(
                calendarId=self.calendar_id, eventId=appointment_id
            )
        )
        return True

    def _to_appointment(self, event):
        start = datetime.fromisoformat(event["start"]["dateTime"])
        end = datetime.fromisoformat(event["end"]["dateTime"])
        props = event.get("extendedProperties", {}).get("private", {})
        summary = event.get("summary", "")
        patient = summary.removeprefix("Dental Appointment - ").strip()
        service = props.get("service", "")
        if not service:
            for line in event.get("description", "").splitlines():
                label, separator, value = line.partition(":")
                if separator and label.strip().casefold() == "reason / procedure":
                    service = value.strip()
                    break
        return Appointment(
            id=event["id"],
            patient_name=patient,
            patient_phone=props.get("patient_phone", ""),
            provider_id=props.get("provider_id", ""),
            start=start,
            end=end,
            status=event.get("status", "confirmed"),
            service=service,
        )
