from app.config import load_config


def test_config_loads():
    c = load_config("office.yaml")
    assert c.office.name == "Novoa Dental"
    assert c.fallback.phone == "305-773-3604"
    assert c.appointments.scheduling_system == "google_calendar"


def test_accepted_insurance_plans_load_as_office_knowledge():
    c = load_config("office.yaml")

    assert c.knowledge.insurance.accepted_plans == [
        "Cigna",
        "BlueCross/BlueShield",
        "Aetna",
        "UnitedHealthcare",
        "Leon Medical",
    ]


def test_system_prompt_contains_insurance_boundaries_and_name_variations():
    prompt = load_config("office.yaml").system_prompt()

    for plan in (
        "Cigna",
        "BlueCross/BlueShield",
        "Aetna",
        "UnitedHealthcare",
        "Leon Medical",
    ):
        assert plan in prompt

    for variation in ("Blue Cross", "Blue Cross Blue Shield", "BCBS", "United Healthcare"):
        assert variation in prompt

    assert "only from knowledge.insurance.accepted_plans" in prompt
    assert "Never claim that any of those details have been verified" in prompt
    assert "coverage depends on the patient's specific plan" in prompt
    assert "do not use insurance information as provider_id" in prompt
