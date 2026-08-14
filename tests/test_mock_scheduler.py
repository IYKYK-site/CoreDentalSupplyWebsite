from app.scheduling.mock import MockScheduler

def test_crud():
    s = MockScheduler()
    a = s.create_appointment(
        "Test Patient", "3055551111", "dr_novoa",
        "2026-08-18T10:00:00-04:00"
    )
    assert s.find_appointments(patient_phone="3055551111")[0].id == a.id

    moved = s.reschedule_appointment(a.id, "2026-08-18T11:00:00-04:00")
    assert moved.start.hour == 11

    assert s.cancel_appointment(a.id) is True
    assert s.find_appointments(patient_phone="3055551111") == []
