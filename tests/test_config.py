from app.config import load_config

def test_config_loads():
    c = load_config("office.yaml")
    assert c.office.name == "Novoa Dental"
    assert c.fallback.phone == "305-773-3604"
    assert c.appointments.scheduling_system == "google_calendar"
