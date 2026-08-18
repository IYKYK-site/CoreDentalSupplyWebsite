import os

import yaml
from twilio.rest import Client


def _load_office_config():
    config_file = os.getenv("OFFICE_CONFIG", "office.yaml")

    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _render_message(template: str, variables: dict | None = None) -> str:
    variables = variables or {}
    rendered = template

    for key, value in variables.items():
        rendered = rendered.replace(
            "{{" + key + "}}",
            str(value),
        )

    return rendered


def send_sms(
    phone_number: str,
    message_name: str,
    variables: dict | None = None,
):
    config = _load_office_config()

    sms_config = config.get("sms", {})

    if not sms_config.get("enabled", False):
        raise RuntimeError("SMS is disabled in office.yaml")

    messages = sms_config.get("messages", {})
    message_config = messages.get(message_name)

    if not message_config:
        raise ValueError(f"Unknown SMS message: {message_name}")

    if not message_config.get("enabled", False):
        raise RuntimeError(
            f"SMS message '{message_name}' is disabled"
        )

    template = message_config.get("message", "")

    body = _render_message(
        template,
        variables,
    )

    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    )

    kwargs = {
        "to": phone_number,
        "body": body,
    }

    messaging_service_sid = os.getenv(
        "TWILIO_MESSAGING_SERVICE_SID"
    )

    if messaging_service_sid:
        kwargs["messaging_service_sid"] = messaging_service_sid
    else:
        kwargs["from_"] = os.environ["TWILIO_PHONE_NUMBER"]

    message = client.messages.create(**kwargs)

    return {
        "sid": message.sid,
        "status": message.status,
        "to": phone_number,
        "message_name": message_name,
    }