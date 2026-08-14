from __future__ import annotations
from pathlib import Path
import os
import yaml
from pydantic import BaseModel, Field


class OfficeInfo(BaseModel):
    name: str
    phone: str = ""
    address: str = ""
    website: str = ""
    timezone: str = "America/New_York"


class Receptionist(BaseModel):
    name: str = "Emma"
    greeting: str


class Provider(BaseModel):
    id: str
    name: str
    specialty: str = ""
    services: list[str] = Field(default_factory=list)


class AppointmentsConfig(BaseModel):
    scheduling_system: str = "google_calendar"
    calendar_id: str = "primary"
    default_duration_minutes: int = 60
    slot_increment_minutes: int = 30
    default_buffer_minutes: int = 0


class Fallback(BaseModel):
    phone: str
    enabled: bool = True


class OfficeConfig(BaseModel):
    office: OfficeInfo
    receptionist: Receptionist
    providers: list[Provider]
    office_hours: dict
    appointments: AppointmentsConfig
    lunch: dict = Field(default_factory=dict)
    appointment_types: dict = Field(default_factory=dict)
    fallback: Fallback
    knowledge: dict = Field(default_factory=dict)

    def system_prompt(self) -> str:
        providers = "\n".join(
            f"- {p.name} ({p.specialty}); services: {', '.join(p.services) or 'not yet specified'}"
            for p in self.providers
        )
        facts = yaml.safe_dump(
            {
                "office": self.office.model_dump(),
                "office_hours": self.office_hours,
                "providers": [p.model_dump() for p in self.providers],
                "knowledge": self.knowledge,
            },
            sort_keys=False,
        )
        return f"""
You are {self.receptionist.name}, the telephone receptionist for {self.office.name}.

SPRINT-0 SCOPE:
- Answer general questions only from the OFFICE DATA below.
- Check appointment availability.
- Create appointments.
- Find appointments.
- Reschedule appointments.
- Cancel appointments.

RULES:
- Never invent office facts, services, prices, insurance coverage, provider availability, or policies.
- If the answer is not in OFFICE DATA, say you do not have that information and direct the caller to {self.fallback.phone}.
- Before creating, moving, or cancelling an appointment, confirm the important details with the caller.
- Use the scheduling tools for calendar facts. Do not claim a slot is available until the tool confirms it.
- Keep phone responses concise and natural.
- Do not provide medical diagnosis or clinical advice.

PROVIDERS:
{providers}

OFFICE DATA:
{facts}
""".strip()


def load_config(path: str | None = None) -> OfficeConfig:
    path = path or os.getenv("OFFICE_CONFIG", "office.yaml")
    raw = yaml.safe_load(Path(path).read_text())
    return OfficeConfig.model_validate(raw)
