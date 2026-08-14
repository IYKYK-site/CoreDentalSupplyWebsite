from __future__ import annotations
import json
from .config import OfficeConfig
from .scheduling.base import Scheduler


def tool_definitions():
    return [
        {
            "type": "function",
            "name": "get_available_slots",
            "description": "Check real calendar availability for a date before offering appointment times.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "provider_id": {"type": "string"},
                },
                "required": ["date"],
            },
        },
        {
            "type": "function",
            "name": "create_appointment",
            "description": "Create a confirmed appointment after the caller confirms the slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string"},
                    "patient_phone": {"type": "string"},
                    "provider_id": {"type": "string"},
                    "start_iso": {"type": "string", "description": "ISO-8601 local datetime"},
                    "reason": {"type": "string"},
                },
                "required": ["patient_name", "patient_phone", "provider_id", "start_iso"],
            },
        },
        {
            "type": "function",
            "name": "find_appointment",
            "description": "Find upcoming appointments for a caller before rescheduling or cancellation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string"},
                    "patient_phone": {"type": "string"},
                },
            },
        },
        {
            "type": "function",
            "name": "reschedule_appointment",
            "description": "Move an existing appointment to a new confirmed start time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "new_start_iso": {"type": "string"},
                },
                "required": ["appointment_id", "new_start_iso"],
            },
        },
        {
            "type": "function",
            "name": "cancel_appointment",
            "description": "Cancel an existing appointment after the caller confirms.",
            "parameters": {
                "type": "object",
                "properties": {"appointment_id": {"type": "string"}},
                "required": ["appointment_id"],
            },
        },
    ]


def execute_tool(scheduler: Scheduler, name: str, args: dict) -> dict:
    if name == "get_available_slots":
        slots = scheduler.get_available_slots(args["date"], args.get("provider_id"))
        return {"slots": [{"start": s.start.isoformat(), "end": s.end.isoformat()} for s in slots[:8]]}

    if name == "create_appointment":
        a = scheduler.create_appointment(
            args["patient_name"], args["patient_phone"], args["provider_id"],
            args["start_iso"], reason=args.get("reason", "")
        )
        return {"appointment": appointment_dict(a)}

    if name == "find_appointment":
        items = scheduler.find_appointments(args.get("patient_name", ""), args.get("patient_phone", ""))
        return {"appointments": [appointment_dict(a) for a in items]}

    if name == "reschedule_appointment":
        a = scheduler.reschedule_appointment(args["appointment_id"], args["new_start_iso"])
        return {"appointment": appointment_dict(a)}

    if name == "cancel_appointment":
        return {"cancelled": scheduler.cancel_appointment(args["appointment_id"])}

    raise ValueError(f"Unknown tool: {name}")


def appointment_dict(a):
    return {
        "id": a.id,
        "patient_name": a.patient_name,
        "patient_phone": a.patient_phone,
        "provider_id": a.provider_id,
        "start": a.start.isoformat(),
        "end": a.end.isoformat(),
        "status": a.status,
    }
