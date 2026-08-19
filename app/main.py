from __future__ import annotations
import logging
import os
from fastapi import FastAPI, WebSocket, Request, WebSocketDisconnect
from fastapi.responses import Response
from dotenv import load_dotenv
from .devlog import devlog

from .config import load_config
from .scheduling.google_calendar import GoogleCalendarScheduler
from .scheduling.mock import MockScheduler
from .realtime_bridge import RealtimeTwilioBridge

logger = logging.getLogger(__name__)

load_dotenv()
config = load_config()

app = FastAPI(title="Core AI Receptionist POC")


def build_scheduler():
    mode = config.appointments.scheduling_system
    if mode == "mock":
        return MockScheduler(
            config.office.timezone,
            config.appointments.default_duration_minutes
        )
    if mode == "google_calendar":
        return GoogleCalendarScheduler(config)
    raise RuntimeError(f"Unsupported scheduling_system: {mode}")


_scheduler = None


def scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = build_scheduler()
    return _scheduler


@app.get("/health")
def health():
    return {"ok": True, "office": config.office.name}


@app.get("/debug/config")
def debug_config():
    # No secrets should ever live in office.yaml.
    return {
        "office": config.office.model_dump(),
        "providers": [p.model_dump() for p in config.providers],
        "fallback": config.fallback.model_dump(),
        "scheduling_system": config.appointments.scheduling_system,
    }


@app.get("/debug/availability")
def debug_availability(date: str, provider_id: str = ""):
    slots = scheduler().get_available_slots(date, provider_id or None)
    return {"slots": [{"start": s.start.isoformat(), "end": s.end.isoformat()} for s in slots]}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    form = await request.form()
    caller_number = form.get("From", "Unknown")

    devlog("CALL", f"Incoming call from: {caller_number}")

    public_url = os.environ["PUBLIC_URL"].rstrip("/")
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://") + "/media-stream"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">

      <Parameter name="caller_number" value="{caller_number}" />

    </Stream>
  </Connect>
  <Hangup/>
</Response>"""
    return Response(content=xml, media_type="application/xml")


@app.websocket("/media-stream")
async def media_stream(ws: WebSocket):
    await ws.accept()
    bridge = RealtimeTwilioBridge(config, scheduler())

    try:
        await bridge.run(ws)
    except WebSocketDisconnect:
        # Normal caller/network disconnect; bridge.run owns peer cleanup.
        pass
    except Exception:
        logger.exception(
            "Unexpected media bridge failure call_sid=%s",
            bridge.call_sid or "unknown",
        )
        raise
    finally:
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
