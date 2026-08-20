import asyncio
from datetime import datetime, timedelta, timezone
import threading
import time
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import LateArrivalPolicy, load_config
from app.late_arrival import check_late_arrival_status
from app.scheduling.base import Appointment
from app.scheduling.google_calendar import GoogleCalendarScheduler
from app.tools import execute_tool, tool_definitions


OFFICE_TZ = ZoneInfo("America/New_York")


class StaticScheduler:
    def __init__(self, appointments):
        self.appointments = appointments
        self.lookup_calls = []
        self.reschedule_calls = []
        self.cancel_calls = []

    def find_today_appointments(self, **kwargs):
        self.lookup_calls.append(kwargs)
        return self.appointments

    def reschedule_appointment(self, *args):
        self.reschedule_calls.append(args)

    def cancel_appointment(self, *args):
        self.cancel_calls.append(args)


def appointment(start, *, suffix="1", service="Dental Cleaning"):
    return Appointment(
        id=f"event-{suffix}",
        patient_name="Test Patient",
        patient_phone="+13055550123",
        provider_id="dr_novoa",
        start=start,
        end=start + timedelta(minutes=45),
        service=service,
    )


def evaluate(minutes_from_start, *, config=None, now=None):
    config = config or load_config("office.yaml")
    current = now or datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ)
    start = current - timedelta(minutes=minutes_from_start)
    scheduler = StaticScheduler([appointment(start)])
    result = check_late_arrival_status(
        config,
        scheduler,
        patient_phone="+13055550123",
        now=current,
    )
    return result, scheduler


def test_appointment_not_yet_started():
    result, _ = evaluate(-15)

    assert result["minutes_late"] == 0
    assert result["minutes_until_start"] == 15
    assert result["policy_band"] == "not_started"
    assert result["recommended_action"] == "arrive_as_scheduled"


def test_within_grace_period():
    result, _ = evaluate(8)

    assert result["minutes_late"] == 8
    assert result["policy_band"] == "within_grace"
    assert result["recommended_action"] == "still_come"


def test_twenty_minutes_late_uses_reschedule_escalation_policy():
    result, _ = evaluate(20)

    assert result["minutes_late"] == 20
    assert result["policy_band"] == "escalation"
    assert result["recommended_action"] == "reschedule"
    assert result["policy_message"] == (
        "You are beyond the normal grace period. We can look for another "
        "appointment time."
    )


def test_thirty_minutes_late_recommends_rescheduling_without_mutation():
    result, scheduler = evaluate(30)

    assert result["policy_band"] == "reschedule"
    assert result["recommended_action"] == "reschedule"
    assert result["workflow_state"] == "identity_verification_required"
    assert result["can_search_replacement"] is False
    assert result["can_modify_appointment"] is False
    assert result["replacement_search"] is None
    assert result["requires_replacement_confirmation"] is True
    assert result["mutation_performed"] is False
    assert scheduler.reschedule_calls == []
    assert scheduler.cancel_calls == []


def test_no_appointment_found():
    scheduler = StaticScheduler([])

    result = check_late_arrival_status(
        load_config("office.yaml"),
        scheduler,
        patient_phone="+13055550123",
        now=datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ),
    )

    assert result["appointment_found"] is False
    assert result["match_status"] == "not_found"
    assert result["recommended_action"] == "clarify_or_transfer"


def test_ambiguous_caller_id_requires_identity_clarification():
    start = datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ)
    scheduler = StaticScheduler([
        appointment(start, suffix="1"),
        appointment(start + timedelta(hours=1), suffix="2"),
    ])

    result = check_late_arrival_status(
        load_config("office.yaml"),
        scheduler,
        patient_phone="+13055550123",
        now=start + timedelta(minutes=20),
    )

    assert result["appointment_found"] is True
    assert result["match_status"] == "ambiguous"
    assert result["details_disclosable"] is False
    assert result["clarification_fields"] == ["patient_name", "patient_dob"]


def test_lateness_uses_office_timezone():
    office_start = datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ)
    scheduler = StaticScheduler([appointment(office_start)])
    utc_now = datetime(2026, 8, 19, 14, 20, tzinfo=timezone.utc)

    result = check_late_arrival_status(
        load_config("office.yaml"),
        scheduler,
        patient_phone="+13055550123",
        now=utc_now,
    )

    assert result["minutes_late"] == 20
    assert result["appointment_start"] == "2026-08-19T10:00:00-04:00"


def test_configured_escalation_behavior_override():
    config = load_config("office.yaml").model_copy(deep=True)
    config.late_arrival.behaviors.escalation.action = "still_come"
    config.late_arrival.behaviors.escalation.message = "Please still come."

    result, _ = evaluate(20, config=config)

    assert result["recommended_action"] == "still_come"
    assert result["policy_message"] == "Please still come."


def test_policy_threshold_validation():
    with pytest.raises(ValidationError):
        LateArrivalPolicy(
            grace_period_minutes=10,
            escalation_threshold_minutes=15,
            reschedule_threshold_minutes=30,
        )


def test_default_escalation_behavior_is_conservative():
    policy = LateArrivalPolicy()

    assert policy.behaviors.escalation.action == "transfer"


def test_caller_id_only_result_hides_details_from_conversation():
    result, _ = evaluate(20)

    assert result["identity_verified"] is False
    assert result["details_disclosable"] is False
    assert result["recommended_action"] == "reschedule"
    assert result["appointment_start"] is not None
    assert result["provider"] == "Dr. Calixto Novoa"
    assert result["service"] == "Dental Cleaning"


def test_full_name_and_dob_mark_details_as_verified():
    current = datetime(2026, 8, 19, 10, 20, tzinfo=OFFICE_TZ)
    scheduler = StaticScheduler([
        appointment(datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ))
    ])

    result = check_late_arrival_status(
        load_config("office.yaml"),
        scheduler,
        patient_phone="+13055550123",
        patient_name="Test Patient",
        patient_dob="01/01/1990",
        now=current,
    )

    assert result["identity_verified"] is True
    assert result["details_disclosable"] is True


def test_verified_reschedule_result_is_ready_to_find_replacement():
    current = datetime(2026, 8, 19, 10, 30, tzinfo=OFFICE_TZ)
    scheduler = StaticScheduler([
        appointment(datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ))
    ])

    result = check_late_arrival_status(
        load_config("office.yaml"),
        scheduler,
        patient_phone="+13055550123",
        patient_name="Test Patient",
        patient_dob="01/01/1990",
        now=current,
    )

    assert result["recommended_action"] == "reschedule"
    assert result["identity_verified"] is True
    assert result["workflow_state"] == "ready_to_find_replacement"
    assert result["can_search_replacement"] is True
    assert result["can_modify_appointment"] is True
    assert result["replacement_search"] == {
        "tool": "find_first_available_time",
        "provider_id": "dr_novoa",
        "duration_minutes": 45,
    }
    assert result["requires_replacement_confirmation"] is True
    assert result["mutation_performed"] is False
    assert scheduler.reschedule_calls == []
    assert scheduler.cancel_calls == []


class FakeEventsRequest:
    def __init__(self, events):
        self.events = events
        self.executed = False

    def execute(self, **kwargs):
        self.executed = True
        return {"items": self.events}


class FakeEventsResource:
    def __init__(self, events):
        self.events = events
        self.list_calls = []
        self.requests = []
        self.update_calls = []
        self.delete_calls = []
        self.insert_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        request = FakeEventsRequest(self.events)
        self.requests.append(request)
        return request


class FakeCalendarService:
    def __init__(self, events):
        self.events_resource = FakeEventsResource(events)

    def events(self):
        return self.events_resource


def test_google_today_lookup_uses_live_calendar_and_is_read_only():
    event = {
        "id": "event-1",
        "summary": "Dental Appointment - Test Patient",
        "description": "Reason / Procedure: Dental Cleaning",
        "start": {"dateTime": "2026-08-19T10:00:00-04:00"},
        "end": {"dateTime": "2026-08-19T10:45:00-04:00"},
        "status": "confirmed",
        "extendedProperties": {
            "private": {
                "patient_phone": "+13055550123",
                "patient_dob": "01/01/1990",
                "provider_id": "dr_novoa",
            }
        },
    }
    config = load_config("office.yaml")
    scheduler = object.__new__(GoogleCalendarScheduler)
    scheduler.config = config
    scheduler.tz = OFFICE_TZ
    scheduler.calendar_id = "primary"
    scheduler.service = FakeCalendarService([event])
    scheduler._now = lambda: datetime(
        2026, 8, 19, 10, 20, tzinfo=OFFICE_TZ
    )

    result = execute_tool(
        scheduler,
        "check_late_arrival_status",
        {"patient_phone": "+13055550123"},
        config=config,
    )

    events_resource = scheduler.service.events_resource
    assert result["minutes_late"] == 20
    assert len(events_resource.list_calls) == 1
    assert events_resource.requests[0].executed is True
    assert events_resource.list_calls[0]["timeMin"] == (
        "2026-08-19T00:00:00-04:00"
    )
    assert events_resource.list_calls[0]["timeMax"] == (
        "2026-08-20T00:00:00-04:00"
    )
    assert events_resource.list_calls[0]["privateExtendedProperty"] == (
        "patient_phone=+13055550123"
    )
    assert events_resource.update_calls == []
    assert events_resource.delete_calls == []
    assert events_resource.insert_calls == []


def test_verified_name_and_dob_ignore_mismatched_caller_id():
    event = {
        "id": "event-verified",
        "summary": "Dental Appointment - Test Patient",
        "start": {"dateTime": "2026-08-19T09:30:00-04:00"},
        "end": {"dateTime": "2026-08-19T10:15:00-04:00"},
        "extendedProperties": {
            "private": {
                "patient_phone": "+13055550999",
                "patient_dob": "01/01/1990",
                "provider_id": "dr_novoa",
            }
        },
    }
    config = load_config("office.yaml")
    scheduler = object.__new__(GoogleCalendarScheduler)
    scheduler.config = config
    scheduler.tz = OFFICE_TZ
    scheduler.calendar_id = "primary"
    scheduler.service = FakeCalendarService([event])
    scheduler._now = lambda: datetime(
        2026, 8, 19, 10, 15, tzinfo=OFFICE_TZ
    )

    result = execute_tool(
        scheduler,
        "check_late_arrival_status",
        {
            "patient_phone": "+13055550123",
            "patient_name": "Test Patient",
            "patient_dob": "01/01/1990",
        },
        config=config,
    )

    lookup = scheduler.service.events_resource.list_calls[0]
    assert "privateExtendedProperty" not in lookup
    assert result["identity_verified"] is True
    assert result["appointment_id"] == "event-verified"
    assert result["recommended_action"] == "reschedule"
    assert result["can_search_replacement"] is True


def test_shared_google_service_access_is_serialized():
    state_lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    class SlowRequest:
        def execute(self, **kwargs):
            with state_lock:
                state["active"] += 1
                state["max_active"] = max(
                    state["max_active"], state["active"]
                )
            time.sleep(0.04)
            with state_lock:
                state["active"] -= 1
            return {"items": []}

    class SlowEvents:
        def list(self, **kwargs):
            return SlowRequest()

    class SlowService:
        def events(self):
            return SlowEvents()

    scheduler = object.__new__(GoogleCalendarScheduler)
    scheduler.config = load_config("office.yaml")
    scheduler.tz = OFFICE_TZ
    scheduler.calendar_id = "primary"
    scheduler.service = SlowService()
    scheduler._service_lock = threading.RLock()
    scheduler._now = lambda: datetime(
        2026, 8, 19, 10, 15, tzinfo=OFFICE_TZ
    )

    async def scenario():
        await asyncio.gather(
            asyncio.to_thread(
                scheduler.find_today_appointments,
                patient_phone="+13055550123",
            ),
            asyncio.to_thread(
                scheduler.find_today_appointments,
                patient_phone="+13055550456",
            ),
        )

    asyncio.run(scenario())
    assert state["max_active"] == 1


def test_verified_late_arrival_offers_same_day_before_confirmed_reschedule():
    current = datetime(2026, 8, 19, 10, 30, tzinfo=OFFICE_TZ)
    existing = appointment(
        datetime(2026, 8, 19, 10, 0, tzinfo=OFFICE_TZ)
    )

    class RescheduleScheduler(StaticScheduler):
        def find_first_available_time(self, **kwargs):
            self.search_args = kwargs
            start = datetime(2026, 8, 19, 13, 0, tzinfo=OFFICE_TZ)
            from app.scheduling.base import Slot
            return Slot(start, start + timedelta(minutes=45))

        def reschedule_appointment(self, appointment_id, new_start_iso):
            self.reschedule_calls.append((appointment_id, new_start_iso))
            start = datetime.fromisoformat(new_start_iso)
            existing.start = start
            existing.end = start + timedelta(minutes=45)
            return existing

    scheduler = RescheduleScheduler([existing])
    config = load_config("office.yaml")

    status = check_late_arrival_status(
        config,
        scheduler,
        patient_phone="+13055550000",
        patient_name="Test Patient",
        patient_dob="01/01/1990",
        now=current,
    )
    replacement = execute_tool(
        scheduler,
        "find_first_available_time",
        {
            "provider_id": status["provider_id"],
            "duration_minutes": status["duration_minutes"],
        },
    )

    assert replacement["slot"]["start"].startswith("2026-08-19T13:00")
    assert scheduler.reschedule_calls == []

    confirmed = execute_tool(
        scheduler,
        "reschedule_appointment",
        {
            "appointment_id": status["appointment_id"],
            "new_start_iso": replacement["slot"]["start"],
        },
    )
    assert scheduler.reschedule_calls == [
        (status["appointment_id"], replacement["slot"]["start"])
    ]
    assert confirmed["appointment"]["start"] == replacement["slot"]["start"]


def test_tool_and_prompt_enforce_privacy_and_confirmation():
    config = load_config("office.yaml")
    prompt = config.system_prompt()

    assert "check_late_arrival_status" in {
        definition["name"] for definition in tool_definitions()
    }
    assert "communicate only the operational policy result" in prompt
    assert "never disclose appointment time, provider, service" in prompt
    assert "wait for the caller's explicit confirmation" in prompt
    assert "Do not cancel or modify the current appointment" in prompt
    assert "do not transfer the caller merely because rescheduling" in prompt
    assert "including the remaining availability today" in prompt
    assert "wait for the caller's explicit confirmation" in prompt
    assert "Never say internal implementation terms" in prompt
