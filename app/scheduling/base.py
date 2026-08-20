from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Slot:
    start: datetime
    end: datetime


@dataclass
class Appointment:
    id: str
    patient_name: str
    patient_phone: str
    provider_id: str
    start: datetime
    end: datetime
    status: str = "confirmed"
    service: str = ""
    patient_dob: str = ""


class Scheduler(ABC):
    @abstractmethod
    def get_available_slots(self, date: str, provider_id: str | None = None) -> list[Slot]:
        raise NotImplementedError

    @abstractmethod
    def create_appointment(
        self,
        patient_name: str,
        patient_phone: str,
        patient_dob: str,
        provider_id: str,
        start_iso: str,
        duration_minutes: int | None = None,
        reason: str = "",
    ) -> Appointment:
        raise NotImplementedError

    @abstractmethod
    def find_appointments(
        self,
        patient_name: str = "",
        patient_phone: str = "",
        patient_dob: str = "",
    ) -> list[Appointment]:
        raise NotImplementedError

    @abstractmethod
    def find_today_appointments(
        self,
        patient_phone: str = "",
        patient_name: str = "",
        patient_dob: str = "",
    ) -> list[Appointment]:
        raise NotImplementedError

    @abstractmethod
    def reschedule_appointment(self, appointment_id: str, new_start_iso: str) -> Appointment:
        raise NotImplementedError

    @abstractmethod
    def cancel_appointment(self, appointment_id: str) -> bool:
        raise NotImplementedError
