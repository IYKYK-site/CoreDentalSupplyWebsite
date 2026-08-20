from __future__ import annotations

from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

from .config import OfficeConfig
from .scheduling.base import Appointment, Scheduler


def _empty_result(
    *,
    match_status: str,
    appointment_found: bool,
    recommended_action: str,
    policy_message: str,
    matches_count: int = 0,
):
    return {
        "appointment_found": appointment_found,
        "match_status": match_status,
        "matches_count": matches_count,
        "identity_verified": False,
        "details_disclosable": False,
        "appointment_id": None,
        "appointment_start": None,
        "minutes_late": None,
        "provider": None,
        "provider_id": None,
        "service": None,
        "duration_minutes": None,
        "policy_band": "not_applicable",
        "recommended_action": recommended_action,
        "policy_message": policy_message,
        "clarification_fields": ["patient_name", "patient_dob"],
        "workflow_state": "identity_verification_required",
        "can_search_replacement": False,
        "can_modify_appointment": False,
        "replacement_search": None,
        "requires_replacement_confirmation": False,
        "mutation_performed": False,
    }


def _provider_name(config: OfficeConfig, provider_id: str):
    for provider in config.providers:
        if provider.id == provider_id:
            return provider.name
    return provider_id or None


def _appointment_result(
    config: OfficeConfig,
    appointment: Appointment,
    *,
    now: datetime,
    identity_verified: bool,
):
    start = appointment.start
    if start.tzinfo is None:
        start = start.replace(tzinfo=now.tzinfo)
    else:
        start = start.astimezone(now.tzinfo)

    seconds_from_start = (now - start).total_seconds()
    minutes_late = max(0, int(seconds_from_start // 60))
    minutes_until_start = (
        max(0, ceil(-seconds_from_start / 60))
        if seconds_from_start < 0
        else 0
    )
    duration_minutes = int(
        (appointment.end - appointment.start).total_seconds() // 60
    )

    if seconds_from_start < 0:
        policy_band = "not_started"
        recommended_action = "arrive_as_scheduled"
        policy_message = "Your appointment has not started yet."
    else:
        policy = config.late_arrival
        if minutes_late <= policy.grace_period_minutes:
            policy_band = "within_grace"
            behavior = policy.behaviors.within_grace
        elif (
            policy.escalation_threshold_minutes
            <= minutes_late
            < policy.reschedule_threshold_minutes
        ):
            policy_band = "escalation"
            behavior = policy.behaviors.escalation
        else:
            policy_band = "reschedule"
            behavior = policy.behaviors.reschedule

        recommended_action = behavior.action
        policy_message = behavior.message

    reschedule_recommended = recommended_action == "reschedule"
    ready_to_reschedule = reschedule_recommended and identity_verified

    if ready_to_reschedule:
        workflow_state = "ready_to_find_replacement"
        replacement_search = {
            "tool": "find_first_available_time",
            "provider_id": appointment.provider_id or None,
            "duration_minutes": duration_minutes,
        }
    elif reschedule_recommended:
        workflow_state = "identity_verification_required"
        replacement_search = None
    else:
        workflow_state = "policy_result_ready"
        replacement_search = None

    return {
        "appointment_found": True,
        "match_status": "unique",
        "matches_count": 1,
        "identity_verified": identity_verified,
        "details_disclosable": identity_verified,
        "appointment_id": appointment.id,
        "appointment_start": start.isoformat(),
        "minutes_late": minutes_late,
        "minutes_until_start": minutes_until_start,
        "provider": _provider_name(config, appointment.provider_id),
        "provider_id": appointment.provider_id or None,
        "service": appointment.service or None,
        "duration_minutes": duration_minutes,
        "policy_band": policy_band,
        "recommended_action": recommended_action,
        "policy_message": policy_message,
        "clarification_fields": [] if identity_verified else [
            "patient_name",
            "patient_dob",
        ],
        "workflow_state": workflow_state,
        "can_search_replacement": ready_to_reschedule,
        "can_modify_appointment": ready_to_reschedule,
        "replacement_search": replacement_search,
        "requires_replacement_confirmation": reschedule_recommended,
        "mutation_performed": False,
    }


def check_late_arrival_status(
    config: OfficeConfig,
    scheduler: Scheduler,
    *,
    patient_phone: str = "",
    patient_name: str = "",
    patient_dob: str = "",
    now: datetime | None = None,
):
    """Return a read-only late-arrival policy decision from today's live data."""

    patient_phone = patient_phone.strip()
    patient_name = patient_name.strip()
    patient_dob = patient_dob.strip()
    identity_verified = bool(patient_name and patient_dob)

    if not patient_phone and not identity_verified:
        return _empty_result(
            match_status="identity_required",
            appointment_found=False,
            recommended_action="clarify_identity",
            policy_message=(
                "The caller's phone number is unavailable. Please verify the "
                "patient's full name and date of birth."
            ),
        )

    appointments = scheduler.find_today_appointments(
        # Caller ID is only a convenience signal. Once both identity fields
        # are supplied, name and DOB are authoritative for this POC.
        patient_phone="" if identity_verified else patient_phone,
        patient_name=patient_name,
        patient_dob=patient_dob,
    )

    if not appointments:
        return _empty_result(
            match_status="not_found",
            appointment_found=False,
            recommended_action="clarify_or_transfer",
            policy_message=(
                "I could not find a matching appointment for today. Please "
                "verify the patient's full name and date of birth or contact "
                "office staff."
            ),
        )

    if len(appointments) > 1:
        return _empty_result(
            match_status="ambiguous",
            appointment_found=True,
            matches_count=len(appointments),
            recommended_action="clarify_identity",
            policy_message=(
                "More than one appointment matches. Please verify the "
                "patient's full name and date of birth."
            ),
        )

    office_timezone = ZoneInfo(config.office.timezone)
    if now is None and hasattr(scheduler, "_now"):
        now = scheduler._now()
    if now is not None and now.tzinfo is None:
        raise ValueError("Late-arrival evaluation requires a timezone-aware time")
    office_now = (
        now.astimezone(office_timezone)
        if now is not None
        else datetime.now(office_timezone)
    )

    return _appointment_result(
        config,
        appointments[0],
        now=office_now,
        identity_verified=identity_verified,
    )
