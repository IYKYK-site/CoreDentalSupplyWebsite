from app import tools
from app.scheduling.mock import MockScheduler


def test_execute_tool_uses_consistent_mock_scheduler_identity_fields():
    scheduler = MockScheduler()
    created = tools.execute_tool(
        scheduler,
        "create_appointment",
        {
            "patient_name": "Test Patient",
            "patient_phone": "+13055551111",
            "patient_dob": "01/02/1990",
            "provider_id": "dr_novoa",
            "start_iso": "2026-08-18T10:00:00-04:00",
            "reason": "Cleaning",
        },
    )

    found = tools.execute_tool(
        scheduler,
        "find_appointment",
        {
            "patient_name": "Test Patient",
            "patient_phone": "+13055551111",
            "patient_dob": "01/02/1990",
        },
    )
    not_found = tools.execute_tool(
        scheduler,
        "find_appointment",
        {
            "patient_name": "Test Patient",
            "patient_phone": "+13055551111",
            "patient_dob": "02/03/1991",
        },
    )

    assert found["appointments"] == [created["appointment"]]
    assert not_found == {"appointments": []}


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
