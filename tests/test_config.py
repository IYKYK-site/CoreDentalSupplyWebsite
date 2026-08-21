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


def test_identity_dialogue_is_sequential_and_non_repetitive():
    prompt = load_config("office.yaml").system_prompt()
    name_request = '"Please provide the full name for the appointment."'
    name_confirmation = '"I heard Jorge Perez. Is that correct?"'
    dob_request = '"What is the patient\'s date of birth?"'
    dob_confirmation = '"Date of birth August 15, 1985. Is that correct?"'
    phone_request = '"What is the best telephone number?"'
    phone_confirmation = '"I heard 305-555-1234. Is that correct?"'

    assert prompt.index(name_request) < prompt.index(name_confirmation)
    assert prompt.index(name_confirmation) < prompt.index(dob_request)
    assert prompt.index(dob_request) < prompt.index(dob_confirmation)
    assert prompt.index(dob_confirmation) < prompt.index(phone_request)
    assert prompt.index(phone_request) < prompt.index(phone_confirmation)
    assert prompt.count("I heard Jorge Perez") == 1
    assert "OK Jorge" not in prompt
    assert "part by part" not in prompt.casefold()
    assert "COLLECTING INFORMATION" not in prompt
    assert "PATIENT INFORMATION COLLECTION" not in prompt


def test_identity_corrections_and_confirmation_gates_are_explicit():
    prompt = load_config("office.yaml").system_prompt()

    assert prompt.count("discard the unconfirmed value") == 3
    assert "full name, date of birth, and telephone number have each been confirmed" in prompt
    assert "Do not ask for a new date in that turn" in prompt
    assert 'Only after the caller confirms that appointment, ask exactly: "What date would you like to change it to?"' in prompt


def test_located_appointment_prompt_uses_returned_time_and_requires_selection():
    prompt = load_config("office.yaml").system_prompt()

    assert "Use only appointments returned by find_appointment" in prompt
    assert "returned spoken_date and spoken_time fields" in prompt
    assert "Substitute the actual returned spoken time and date" in prompt
    assert "present each returned appointment's spoken date and time clearly" in prompt
    assert "Do not select one automatically" in prompt


def test_recording_announcement_is_acknowledged_without_authentication():
    prompt = load_config("office.yaml").system_prompt()
    acknowledgement = '"That\'s perfectly fine. How can I help you today?"'

    assert '"This call is being recorded,"' in prompt
    assert acknowledgement in prompt
    assert "treat it only as a notice" in prompt
    assert "not as the caller's reason for calling" in prompt
    assert "do not give the separate interrupted-greeting AI disclosure" in prompt
    assert "do not begin identity verification" in prompt


def test_unclear_opening_interruption_asks_how_claudia_can_help():
    prompt = load_config("office.yaml").system_prompt()

    assert 'ask exactly: "How can I help you today?"' in prompt
    assert "Do not guess the caller's intent" in prompt
    assert "General greetings, background speech, silence" in prompt


def test_interrupted_greeting_is_not_restarted_or_completed():
    prompt = load_config("office.yaml").system_prompt()

    assert "Do not restart or complete the interrupted greeting afterward" in prompt
    assert "An interruption by itself is never a reason" in prompt


def test_existing_appointment_intent_still_starts_sequential_verification():
    prompt = load_config("office.yaml").system_prompt()
    intent_gate = "Establish the caller's intent before starting this sequence"
    existing_appointment_trigger = (
        "find, access, discuss, reschedule, or cancel an existing appointment"
    )

    assert intent_gate in prompt
    assert existing_appointment_trigger in prompt
    assert prompt.index(intent_gate) < prompt.index("1. FULL APPOINTMENT NAME")
    assert '"Please provide the full name for the appointment."' in prompt


def test_successful_rescheduling_dialogue_remains_in_order():
    prompt = load_config("office.yaml").system_prompt()
    located_confirmation = (
        '"I found your appointment at 10:30 AM on Tuesday, August 25. '
        'Is that the appointment you\'d like to reschedule?"'
    )
    new_date_request = '"What date would you like to change it to?"'

    assert located_confirmation in prompt
    assert prompt.index(located_confirmation) < prompt.index(new_date_request)
    assert "Only after the caller confirms that appointment" in prompt


def test_change_appointment_language_routes_directly_to_identity_sequence():
    prompt = load_config("office.yaml").system_prompt()
    phrases = (
        "I need to change an appointment,",
        "I need to change my appointment,",
        "I want to move my appointment,",
        "I need to reschedule,",
        "Can I change the date or time?",
    )

    for phrase in phrases:
        assert f'"{phrase}"' in prompt
    assert "Treat equivalent natural variations the same way" in prompt
    assert (
        'Immediately begin the existing identity sequence by asking exactly: '
        '"Please provide the full name for the appointment."'
    ) in prompt


def test_rescheduling_intent_cannot_route_to_insurance():
    prompt = load_config("office.yaml").system_prompt()

    assert "do not discuss insurance or any unrelated office information" in prompt
    assert "Never enter it for an appointment-changing request" in prompt
    assert prompt.index("INTENT ROUTING") < prompt.index("INSURANCE:")


def test_insurance_requires_explicit_insurance_intent():
    prompt = load_config("office.yaml").system_prompt()

    assert "Discuss or list insurance plans only when the caller explicitly asks" in prompt
    assert "names an insurance company as part of an insurance-related question" in prompt
    assert "Never volunteer insurance information" in prompt
    assert "presence of insurance information in OFFICE DATA is never a reason" in prompt


def test_ambiguous_intent_gets_one_clarifying_question():
    prompt = load_config("office.yaml").system_prompt()

    assert (
        'ask one brief clarifying question: "Could you tell me a little more '
        'about what you\'d like help with?"'
    ) in prompt
    assert "Do not guess and do not provide unrelated office information" in prompt


def test_natural_voice_delivery_section_is_present_once():
    prompt = load_config("office.yaml").system_prompt()

    assert prompt.count("NATURAL VOICE DELIVERY") == 1
    assert "Speak in connected conversational thoughts" in prompt
    assert "giving every clause equal weight" in prompt
    assert "gentle, natural pitch variation and meaningful emphasis" in prompt
    assert "Avoid flat announcement-style delivery" in prompt
    assert "avoid identical cadence across consecutive sentences" in prompt
    assert "naturally breathe or where the meaning changes" in prompt
    assert "Begin responses promptly after the caller finishes speaking" in prompt
    assert "greetings and farewells as warm, connected conversation" in prompt
    assert "never as prerecorded scripts" in prompt


def test_structured_information_is_spoken_more_slowly_and_clearly():
    prompt = load_config("office.yaml").system_prompt()

    assert (
        "Slightly slow down and articulate full names, dates of birth, "
        "telephone numbers, addresses, appointment dates, and appointment times"
    ) in prompt
    assert "Return immediately to a normal conversational pace" in prompt
    assert "Keep routine responses prompt and concise" in prompt


def test_confirmations_and_transitions_sound_attentive_without_repetition():
    prompt = load_config("office.yaml").system_prompt()

    assert "slightly reassuring tone when confirming caller information" in prompt
    assert "Sound attentive when transitioning to the next question" in prompt
    assert "without repeating what the caller just said" in prompt


def test_acknowledgements_are_optional_and_restrained():
    prompt = load_config("office.yaml").system_prompt()

    assert "only when it adds value" in prompt
    assert "Do not acknowledge every statement" in prompt
    assert "repeat the caller's words merely to sound attentive" in prompt
    assert "add an acknowledgement merely to sound human" in prompt
    assert "use no more than one brief acknowledgement for that operation" in prompt
    for acknowledgement in (
        "Let me check that for you.",
        "Okay, let me take a look.",
        "Sure—one moment.",
    ):
        assert f'"{acknowledgement}"' in prompt


def test_excessive_fillers_and_artificial_vocal_effects_are_prohibited():
    prompt = load_config("office.yaml").system_prompt()

    assert "Never manufacture breathing sounds, laughter, typing noises" in prompt
    assert "hesitation, or background activity" in prompt
    assert '"um," "uh," "you know," "like," "okay," and "alright."' in prompt
    assert "Do not add filler before an immediate answer" in prompt
    assert "Do not add artificial interjections or unsolicited audio" in prompt
    assert "while listening or during caller silence" in prompt


def test_natural_delivery_preserves_required_workflow_wording():
    prompt = load_config("office.yaml").system_prompt()

    assert "Preserve exact required confirmation wording" in prompt
    assert "whenever this prompt specifies exact language" in prompt
    assert "including identity verification" in prompt
    assert "appointment confirmation, privacy, emergencies, recording notices" in prompt
    assert "SMS consent" in prompt
    assert "never override accuracy, safety, privacy, or workflow order" in prompt
    assert '"Please provide the full name for the appointment."' in prompt
    assert (
        '"I found your appointment at 10:30 AM on Tuesday, August 25. '
        'Is that the appointment you\'d like to reschedule?"'
    ) in prompt


def test_opening_greeting_is_exact_and_present_once():
    config = load_config("office.yaml")
    prompt = config.system_prompt()
    greeting = (
        "Hi, thanks for calling Dr. Novoa’s office. I’m Claudia, the office’s "
        "AI assistant—how can I help? También hablo español."
    )

    assert config.receptionist.greeting == greeting
    assert prompt.count(greeting) == 1
    assert "one friendly, connected introduction" in prompt
    assert "Deliver the final Spanish sentence as a warm, helpful aside" in prompt
    assert "without pausing excessively before it" in prompt
    assert "Do not use an announcer or IVR cadence" in prompt
    assert "insert equal-length pauses between every clause" in prompt
    assert "Do not translate or repeat the entire greeting" in prompt


def test_spanish_is_the_only_additional_language_announced_in_the_greeting():
    config = load_config("office.yaml")
    greeting = config.receptionist.greeting
    prompt = config.system_prompt()

    assert "Thank you for calling Dr. Novoa's office" not in greeting
    assert "How may I help you today" not in greeting
    assert greeting.count("También hablo español") == 1
    assert prompt.count("También hablo español") == 1
    for language in ("Haitian Creole", "Brazilian Portuguese", "Russian"):
        assert language not in greeting
    assert "Spanish is the only additional language announced" in prompt


def test_all_poc_languages_are_supported_without_a_language_menu():
    prompt = load_config("office.yaml").system_prompt()

    for language in (
        "English",
        "Spanish",
        "Haitian Creole",
        "Brazilian Portuguese",
        "Russian",
    ):
        assert language in prompt
    assert "When the caller clearly speaks a supported language" in prompt
    assert "continue using it unless the caller switches languages" in prompt
    assert "Allow natural code-switching, especially between English and Spanish" in prompt
    assert "Never require the caller to select a language from a menu" in prompt


def test_language_switching_preserves_data_and_workflow_requirements():
    prompt = load_config("office.yaml").system_prompt()

    assert (
        "Preserve proper names, telephone numbers, dates, addresses, and "
        "appointment details accurately when switching languages"
    ) in prompt
    assert (
        "exact identity-confirmation and appointment-confirmation requirements "
        "apply in every supported language"
    ) in prompt
    assert "without changing their order or meaning" in prompt
    assert "ask for repetition or clarification rather than guessing" in prompt
    assert "Do not translate information into another language unless" in prompt
    assert "Do not claim native-level fluency" in prompt
    assert "describe Russian as validated or guaranteed" in prompt


def test_permitted_farewells_are_brief_and_end_with_goodbye():
    prompt = load_config("office.yaml").system_prompt()
    farewells = (
        "You're welcome. Take care—goodbye.",
        "Of course. Have a good day—goodbye.",
        "Glad I could help. Take care—goodbye.",
        "You're all set. Have a good day—goodbye.",
    )

    assert "Thank you for calling Dr. Novoa's office. Have a wonderful day. Goodbye." not in prompt
    for farewell in farewells:
        assert f'"{farewell}"' in prompt
        assert farewell.removesuffix(".").casefold().endswith("goodbye")


def test_farewell_remains_single_and_termination_compatible():
    prompt = load_config("office.yaml").system_prompt()

    assert "Do not use the same farewell mechanically after every call" in prompt
    assert "Keep the farewell to one short conversational sentence" in prompt
    assert 'Every final farewell must end with the spoken word "goodbye"' in prompt
    assert "Say the farewell only once" in prompt
    assert "Do not ask another question after the farewell" in prompt
    assert 'Do not speak again after saying "goodbye."' in prompt
    assert "automatically terminate the telephone call" in prompt
