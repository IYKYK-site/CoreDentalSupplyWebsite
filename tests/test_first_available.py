from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import load_config
from app.scheduling.google_calendar import GoogleCalendarScheduler
from app.tools import execute_tool, tool_definitions


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self, **kwargs):
        return self.response


class FakeFreeBusy:
    def __init__(self, calendar_id, busy_by_date):
        self.calendar_id = calendar_id
        self.busy_by_date = busy_by_date
        self.queries = []

    def query(self, body):
        self.queries.append(body)
        date = body["timeMin"][:10]
        return FakeRequest({
            "calendars": {
                self.calendar_id: {
                    "busy": self.busy_by_date.get(date, []),
                }
            }
        })


class FakeCalendarService:
    def __init__(self, calendar_id="primary", busy_by_date=None):
        self.freebusy_resource = FakeFreeBusy(
            calendar_id, busy_by_date or {}
        )

    def freebusy(self):
        return self.freebusy_resource


def busy(start, end):
    return [{"start": start.isoformat(), "end": end.isoformat()}]


def make_scheduler(now, busy_by_date=None):
    config = load_config("office.yaml")
    scheduler = object.__new__(GoogleCalendarScheduler)
    scheduler.config = config
    scheduler.tz = ZoneInfo(config.office.timezone)
    scheduler.calendar_id = config.appointments.calendar_id
    scheduler.service = FakeCalendarService(
        scheduler.calendar_id, busy_by_date
    )
    scheduler._now = lambda: now
    return scheduler


def queried_dates(scheduler):
    return [
        body["timeMin"][:10]
        for body in scheduler.service.freebusy_resource.queries
    ]


def test_first_available_uses_one_hour_cutoff_and_slot_increment():
    tz = ZoneInfo("America/New_York")
    scheduler = make_scheduler(datetime(2026, 8, 17, 9, 10, tzinfo=tz))

    slot = scheduler.find_first_available_time()

    assert slot.start == datetime(2026, 8, 17, 10, 30, tzinfo=tz)
    assert slot.start >= scheduler._now() + timedelta(hours=1)
    assert queried_dates(scheduler) == ["2026-08-17"]


def test_first_available_returns_free_thursday_before_friday():
    tz = ZoneInfo("America/New_York")
    thursday = datetime(2026, 8, 20, 8, 0, tzinfo=tz)
    scheduler = make_scheduler(
        datetime(2026, 8, 19, 16, 30, tzinfo=tz),
        {"2026-08-20": busy(thursday, thursday + timedelta(hours=1))},
    )

    slot = scheduler.find_first_available_time()

    assert slot.start == datetime(2026, 8, 20, 9, 0, tzinfo=tz)
    assert queried_dates(scheduler) == ["2026-08-19", "2026-08-20"]


def test_first_available_checks_each_open_day_live_and_skips_closed_days():
    tz = ZoneInfo("America/New_York")
    saturday_open = datetime(2026, 8, 22, 8, 0, tzinfo=tz)
    scheduler = make_scheduler(
        datetime(2026, 8, 21, 12, 30, tzinfo=tz),
        {
            "2026-08-22": busy(
                saturday_open,
                datetime(2026, 8, 22, 13, 0, tzinfo=tz),
            )
        },
    )

    slot = scheduler.find_first_available_time()

    assert slot.start == datetime(2026, 8, 24, 8, 0, tzinfo=tz)
    assert queried_dates(scheduler) == [
        "2026-08-21",
        "2026-08-22",
        "2026-08-24",
    ]


def test_slots_respect_lunch_duration_buffer_and_office_close():
    tz = ZoneInfo("America/New_York")
    scheduler = make_scheduler(datetime(2026, 8, 19, 18, 0, tzinfo=tz))

    slots = scheduler.get_available_slots(
        "2026-08-20",
        duration_minutes=60,
        buffer_minutes=30,
    )

    close = datetime(2026, 8, 20, 17, 0, tzinfo=tz)
    lunch_start = datetime(2026, 8, 20, 12, 0, tzinfo=tz)
    lunch_end = datetime(2026, 8, 20, 13, 0, tzinfo=tz)
    buffer = timedelta(minutes=30)

    assert slots
    assert all(slot.end + buffer <= close for slot in slots)
    assert all(
        not (slot.start < lunch_end and slot.end + buffer > lunch_start)
        for slot in slots
    )
    assert slots[-1].start == datetime(2026, 8, 20, 15, 30, tzinfo=tz)


def test_explicit_preferred_time_behavior_remains_exact():
    tz = ZoneInfo("America/New_York")
    thursday_ten = datetime(2026, 8, 20, 10, 0, tzinfo=tz)
    scheduler = make_scheduler(
        datetime(2026, 8, 19, 18, 0, tzinfo=tz),
        {
            "2026-08-20": busy(
                thursday_ten,
                thursday_ten + timedelta(hours=1),
            )
        },
    )

    slot = scheduler.find_next_available_time("10:00")

    assert slot.start == datetime(2026, 8, 21, 10, 0, tzinfo=tz)


def test_first_available_tool_is_exposed_and_returns_scheduler_result():
    tz = ZoneInfo("America/New_York")
    scheduler = make_scheduler(datetime(2026, 8, 17, 9, 10, tzinfo=tz))

    result = execute_tool(scheduler, "find_first_available_time", {})

    assert "find_first_available_time" in {
        definition["name"] for definition in tool_definitions()
    }
    assert result == {
        "found": True,
        "slot": {
            "start": "2026-08-17T10:30:00-04:00",
            "end": "2026-08-17T11:30:00-04:00",
        },
    }


def test_prompt_routes_only_non_specific_urgent_requests_to_first_available():
    prompt = load_config("office.yaml").system_prompt()

    assert "urgent, immediate, earliest, or first-available" in prompt
    assert "use the find_first_available_time tool" in prompt
    assert "explicitly requests a specific time" in prompt
