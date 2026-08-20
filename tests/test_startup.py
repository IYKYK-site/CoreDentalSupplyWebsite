import pytest

from app.config import load_config, validate_environment
from app import main


def valid_environment():
    return {
        "OPENAI_API_KEY": "test-openai-key",
        "PUBLIC_URL": "https://example.test",
        "TWILIO_ACCOUNT_SID": "AC123",
        "TWILIO_AUTH_TOKEN": "test-twilio-token",
        "TWILIO_MESSAGING_SERVICE_SID": "MG123",
    }


def test_environment_validation_accepts_messaging_service():
    validate_environment(load_config("office.yaml"), valid_environment())


def test_environment_validation_accepts_twilio_phone_number():
    environment = valid_environment()
    environment.pop("TWILIO_MESSAGING_SERVICE_SID")
    environment["TWILIO_PHONE_NUMBER"] = "+13055558033"

    validate_environment(load_config("office.yaml"), environment)


def test_environment_validation_lists_missing_keys_without_values():
    with pytest.raises(RuntimeError) as exc_info:
        validate_environment(load_config("office.yaml"), {})

    message = str(exc_info.value)
    for name in (
        "OPENAI_API_KEY",
        "PUBLIC_URL",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_MESSAGING_SERVICE_SID or TWILIO_PHONE_NUMBER",
    ):
        assert name in message


def test_fastapi_startup_runs_environment_validation(monkeypatch):
    calls = []

    def fake_validate_environment(config):
        calls.append(config)

    monkeypatch.setattr(main, "validate_environment", fake_validate_environment)
    main.validate_startup_environment()

    assert calls == [main.config]
