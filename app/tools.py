from __future__ import annotations
import json
from .config import OfficeConfig
from .late_arrival import check_late_arrival_status
from .scheduling.base import Scheduler
from .devlog import devlog
from .sms_service import send_sms


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
                    "patient_dob": {
                        "type": "string",
                        "description": "Patient date of birth in MM/DD/YYYY format."
                    },
                    "provider_id": {"type": "string"},
                    "start_iso": {"type": "string", "description": "ISO-8601 local datetime"},
                    "reason": {"type": "string"},
                },
                "required": ["patient_name", "patient_phone", "patient_dob", "provider_id", "start_iso"],
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
                    "patient_dob": {
                        "type": "string",
                        "description": "Patient date of birth in MM/DD/YYYY format."
                    },
                },
                "required": [
                    "patient_name",
                    "patient_phone",
                    "patient_dob",
                ],
            },
        },
        {
            "type": "function",
            "name": "check_late_arrival_status",
            "description": (
                "Look up today's appointment and apply the configured late-arrival "
                "policy. Use whenever a caller reports being late or asks whether "
                "they can still come. Caller ID is supplied by the server when "
                "available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_phone": {
                        "type": "string",
                        "description": "Caller phone number when caller ID is unavailable."
                    },
                    "patient_name": {
                        "type": "string",
                        "description": "Full name, requested only when identity clarification is needed."
                    },
                    "patient_dob": {
                        "type": "string",
                        "description": "Date of birth in MM/DD/YYYY, requested only when identity clarification is needed."
                    },
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
        {
            "type": "function",
            "name": "send_sms",
            "description": (
                "Send an approved informational SMS to the caller only after "
                "the caller has explicitly agreed to receive it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message_name": {
                        "type": "string",
                        "enum": [
                            "address",
                            "doctor_bio",
                            "insurance",
                            "patient_forms",
                            "urgent_contact",
                            "appointment_confirmation",
                        ],
                    },
                    "variables": {
                        "type": "object"
                    },
                },
                "required": [
                    "message_name",
                ],
            },
        },
            {
            "type": "function",
            "name": "find_next_available_time",
            "description": (
                "Find the next available appointment on any future day "
                "that starts at a specific preferred time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "preferred_time": {
                        "type": "string",
                        "description": "Preferred start time in HH:MM 24-hour format, for example 15:00."
                    },
                    "provider_id": {
                        "type": "string"
                    },
                    "duration_minutes": {
                        "type": "integer"
                    },
                    "buffer_minutes": {
                        "type": "integer"
                    },
                    "days_to_search": {
                        "type": "integer",
                        "description": "How many future days to search. Default is 30."
                    }
                },
                "required": [
                    "preferred_time"
                ]
            }
        },
        {
            "type": "function",
            "name": "find_first_available_time",
            "description": (
                "Find the earliest appointment available on the live calendar, "
                "starting one hour after the current office-local time. Use for "
                "urgent, immediate, earliest, or first-available requests."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "provider_id": {
                        "type": "string"
                    },
                    "duration_minutes": {
                        "type": "integer"
                    },
                    "buffer_minutes": {
                        "type": "integer"
                    },
                    "days_to_search": {
                        "type": "integer",
                        "description": "How many future days to search. Default is 30."
                    }
                }
            }
        },
    ]


def execute_tool(
    scheduler: Scheduler,
    name: str,
    args: dict,
    config: OfficeConfig | None = None,
) -> dict:
    if name == "get_available_slots":
        slots = scheduler.get_available_slots(args["date"], args.get("provider_id"))
        return {"slots": [{"start": s.start.isoformat(), "end": s.end.isoformat()} for s in slots[:8]]}

    if name == "create_appointment":
        patient_name = args["patient_name"]
        patient_phone = args["patient_phone"]
        patient_dob = args["patient_dob"]
        provider_id = args["provider_id"]
        start_iso = args["start_iso"]

        devlog("CALL", f"Caller name: {patient_name}")
        devlog("CALL", f"Caller phone: {patient_phone}")
        devlog("CALL", f"Caller DOB: {patient_dob}")
        devlog("CALENDAR", f"Requested appointment: {start_iso}")
        devlog("CALENDAR", "Creating appointment...")

        a = scheduler.create_appointment(
            patient_name,
            patient_phone,
            patient_dob,
            provider_id,
            start_iso,
            reason=args.get("reason", "")
        )

        devlog(
            "CALENDAR",
            f"Appointment created: {patient_name} | "
            f"{patient_phone} | {a.start.isoformat()}"
        )

        return {"appointment": appointment_dict(a)}

    if name == "find_appointment":
        items = scheduler.find_appointments(
            patient_name=args.get("patient_name", ""),
            patient_phone=args.get("patient_phone", ""),
            patient_dob=args.get("patient_dob", ""),
        )
        return {"appointments": [appointment_dict(a) for a in items]}

    if name == "check_late_arrival_status":
        if config is None:
            raise RuntimeError("Office configuration is required for late arrivals")
        return check_late_arrival_status(
            config,
            scheduler,
            patient_phone=args.get("patient_phone", ""),
            patient_name=args.get("patient_name", ""),
            patient_dob=args.get("patient_dob", ""),
        )

    if name == "reschedule_appointment":
        devlog(
            "CALENDAR",
            f"Rescheduling appointment {args['appointment_id']} "
            f"to {args['new_start_iso']}"
        )

        a = scheduler.reschedule_appointment(
            args["appointment_id"],
            args["new_start_iso"]
        )

        devlog(
            "CALENDAR",
            f"Appointment rescheduled to: {a.start.isoformat()}"
        )

        return {"appointment": appointment_dict(a)}

    if name == "cancel_appointment":
        devlog(
            "CALENDAR",
            f"Cancelling appointment: {args['appointment_id']}"
        )

        result = scheduler.cancel_appointment(
            args["appointment_id"]
        )

        devlog("CALENDAR", "Appointment cancelled.")

        return {"cancelled": result}
    if name == "find_next_available_time":
        slot = scheduler.find_next_available_time(
            preferred_time=args["preferred_time"],
            provider_id=args.get("provider_id"),
            duration_minutes=args.get("duration_minutes"),
            buffer_minutes=args.get("buffer_minutes"),
            days_to_search=args.get("days_to_search", 30),
        )

        if slot is None:
            return {
                "found": False,
                "message": "No matching appointment time was found in the search window."
            }

        return {
            "found": True,
            "slot": {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
            }
        }

    if name == "find_first_available_time":
        slot = scheduler.find_first_available_time(
            provider_id=args.get("provider_id"),
            duration_minutes=args.get("duration_minutes"),
            buffer_minutes=args.get("buffer_minutes"),
            days_to_search=args.get("days_to_search", 30),
        )

        if slot is None:
            return {
                "found": False,
                "message": "No available appointment was found in the search window."
            }

        return {
            "found": True,
            "slot": {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
            }
        }

        if name == "send_sms":
            return send_sms(
                phone_number=args["phone_number"],
                message_name=args["message_name"],
                variables=args.get("variables"),
            )
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
