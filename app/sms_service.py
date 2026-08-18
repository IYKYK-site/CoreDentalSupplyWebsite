from twilio.rest import Client
from app.sms_templates import SMS_TEMPLATES
import os


ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID")

client = Client(ACCOUNT_SID, AUTH_TOKEN)


def send_template(phone_number: str, template_name: str):
    """
    Sends one of the predefined SMS templates.
    """

    if template_name not in SMS_TEMPLATES:
        raise ValueError(f"Unknown SMS template: {template_name}")

    message = client.messages.create(
        messaging_service_sid=MESSAGING_SERVICE_SID,
        to=phone_number,
        body=SMS_TEMPLATES[template_name]
    )

    return message.sid