from app import tools
from app.scheduling.mock import MockScheduler


def create_test_appointment(scheduler, start_iso="2026-08-25T10:30:00-04:00"):
    return tools.execute_tool(
        scheduler,
        "create_appointment",
        {
            "patient_name": "Jorge Perez",
            "patient_phone": "305-555-1234",
            "patient_dob": "08/15/1985",
            "provider_id": "dr_novoa",
            "start_iso": start_iso,
            "reason": "Cleaning",
        },
    )


def test_execute_tool_uses_existing_scheduler_identity_schema():
    scheduler = MockScheduler()
    created = create_test_appointment(scheduler)

    found = tools.execute_tool(
        scheduler,
        "find_appointment",
        {
            "patient_name": "Jorge Perez",
            "patient_phone": "305-555-1234",
            "patient_dob": "08/15/1985",
        },
    )
    not_found = tools.execute_tool(
        scheduler,
        "find_appointment",
        {
            "patient_name": "Jorge Perez",
            "patient_phone": "305-555-1234",
            "patient_dob": "02/03/1991",
        },
    )

    expected = created["appointment"] | {
        "spoken_time": "10:30 AM",
        "spoken_date": "Tuesday, August 25, 2026",
    }
    assert found == {"appointments": [expected]}
    assert not_found == {"appointments": []}


def test_spoken_date_and_time_are_added_only_to_lookup_results():
    scheduler = MockScheduler()
    created = create_test_appointment(scheduler)
    found = tools.execute_tool(
        scheduler,
        "find_appointment",
        {
            "patient_name": "Jorge Perez",
            "patient_phone": "305-555-1234",
            "patient_dob": "08/15/1985",
        },
    )

    assert "spoken_time" not in created["appointment"]
    assert "spoken_date" not in created["appointment"]
    assert found["appointments"][0]["spoken_time"] == "10:30 AM"
    assert found["appointments"][0]["spoken_date"] == (
        "Tuesday, August 25, 2026"
    )


def test_multiple_lookup_results_preserve_every_actual_appointment():
    scheduler = MockScheduler()
    create_test_appointment(scheduler, "2026-08-25T10:30:00-04:00")
    create_test_appointment(scheduler, "2026-08-27T14:00:00-04:00")

    result = tools.execute_tool(
        scheduler,
        "find_appointment",
        {
            "patient_name": "Jorge Perez",
            "patient_phone": "305-555-1234",
            "patient_dob": "08/15/1985",
        },
    )

    assert list(result) == ["appointments"]
    assert [
        (item["spoken_time"], item["spoken_date"])
        for item in result["appointments"]
    ] == [
        ("10:30 AM", "Tuesday, August 25, 2026"),
        ("2:00 PM", "Thursday, August 27, 2026"),
    ]


def test_reschedule_uses_existing_tool_schema():
    scheduler = MockScheduler()
    appointment_id = create_test_appointment(scheduler)["appointment"]["id"]

    result = tools.execute_tool(
        scheduler,
        "reschedule_appointment",
        {
            "appointment_id": appointment_id,
            "new_start_iso": "2026-08-26T10:30:00-04:00",
        },
    )

    assert result["appointment"]["start"] == "2026-08-26T10:30:00-04:00"


def test_execute_tool_dispatches_sms_once(monkeypatch):
    calls = []

    def fake_send_sms(phone_number, message_name, variables=None):
        calls.append((phone_number, message_name, variables))
        return {"sid": "SM123", "status": "queued"}

    monkeypatch.setattr(tools, "send_sms", fake_send_sms)

    result = tools.execute_tool(
        MockScheduler(),
        "send_sms",
        {
            "phone_number": "+13055551111",
            "message_name": "address",
            "variables": {"office": "Novoa Dental"},
        },
    )

    assert result == {"sid": "SM123", "status": "queued"}
    assert calls == [
        (
            "+13055551111",
            "address",
            {"office": "Novoa Dental"},
        )
    ]
