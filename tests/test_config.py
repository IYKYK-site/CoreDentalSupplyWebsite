from pathlib import Path

import yaml

from app.config import load_config
from app.sms_templates import SMS_TEMPLATES


def test_config_loads():
    c = load_config("office.yaml")
    assert c.office.name == "Novoa Dental"
    assert c.office.phone == "(305) 552-8033"
    assert c.fallback.phone == "305-773-3604"
    assert c.fallback.phone != c.office.phone
    assert c.appointments.scheduling_system == "google_calendar"


def test_sms_address_matches_office_address():
    c = load_config("office.yaml")
    raw = yaml.safe_load(Path("office.yaml").read_text())
    address_message = raw["sms"]["messages"]["address"]["message"]

    assert c.office.address in address_message


def test_caller_facing_contact_uses_poc_fallback_number():
    c = load_config("office.yaml")
    raw = yaml.safe_load(Path("office.yaml").read_text())
    urgent_message = raw["sms"]["messages"]["urgent_contact"]["message"]

    assert c.fallback.phone in urgent_message
    assert c.office.phone not in urgent_message
    assert c.fallback.phone in c.system_prompt()


def test_real_office_phone_never_appears_in_caller_facing_text():
    c = load_config("office.yaml")
    raw = yaml.safe_load(Path("office.yaml").read_text())
    prompt = c.system_prompt()
    late_arrival_messages = [
        behavior.message
        for behavior in (
            c.late_arrival.behaviors.within_grace,
            c.late_arrival.behaviors.escalation,
            c.late_arrival.behaviors.reschedule,
        )
    ]
    configured_sms_messages = [
        message["message"]
        for message in raw["sms"]["messages"].values()
    ]
    caller_facing_texts = [
        prompt,
        *late_arrival_messages,
        Path("app/late_arrival.py").read_text(),
        *configured_sms_messages,
        *SMS_TEMPLATES.values(),
    ]
    real_phone_digits = "".join(character for character in c.office.phone if character.isdigit())

    assert c.fallback.phone in prompt
    for text in caller_facing_texts:
        text_digits = "".join(character for character in text if character.isdigit())
        assert real_phone_digits not in text_digits


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
