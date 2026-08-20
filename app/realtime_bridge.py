from __future__ import annotations

from twilio.rest import Client

import asyncio
import base64
import json
import os
import time
import websockets
import ssl
import certifi
from fastapi import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from .tools import tool_definitions, execute_tool
from .sms_service import send_sms
from .devlog import devlog


class RealtimeTwilioBridge:
    """
    Small POC bridge:
    Twilio Media Streams (G.711 u-law/8k) <-> OpenAI Realtime.

    Realtime supports G.711 u-law directly, so the POC avoids transcoding.
    """

    def __init__(self, config, scheduler):
        self.config = config
        self.scheduler = scheduler
        self.model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
        self.api_key = os.environ["OPENAI_API_KEY"]
        self.caller_number = None
        self.call_sid = None
        self._processed_tool_call_ids = set()
        self._pending_response_call_ids = []

        self.twilio_client = Client(
            os.environ["TWILIO_ACCOUNT_SID"],
            os.environ["TWILIO_AUTH_TOKEN"],
        )

    async def run(self, twilio_ws):
        termination_reason = "setup_failed"
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        ssl_context = ssl.create_default_context(cafile=certifi.where())

        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                max_size=2**24,
                ssl=ssl_context,
            ) as oai_ws:
                await self._configure(oai_ws)
                stream_sid = None

                async def twilio_to_openai():
                    nonlocal stream_sid
                    try:
                        async for text in twilio_ws.iter_text():
                            msg = json.loads(text)
                            event = msg.get("event")

                            if event == "start":
                                stream_sid = msg["start"]["streamSid"]
                                self.call_sid = msg["start"].get("callSid")
                                custom_parameters = msg["start"].get(
                                    "customParameters", {}
                                )
                                self.caller_number = custom_parameters.get(
                                    "caller_number"
                                )
                                devlog(
                                    "CALL",
                                    f"started call_sid={self.call_sid} "
                                    f"stream_sid={stream_sid}",
                                )
                            elif event == "media":
                                await oai_ws.send(json.dumps({
                                    "type": "input_audio_buffer.append",
                                    "audio": msg["media"]["payload"],
                                }))
                            elif event == "mark":
                                if msg.get("mark", {}).get("name") == "end_call":
                                    return "farewell_audio_complete"
                            elif event == "stop":
                                return "twilio_stop"

                        return "twilio_stream_ended"
                    except WebSocketDisconnect:
                        return "twilio_disconnect"

                async def openai_to_twilio():
                    nonlocal stream_sid
                    try:
                        async for raw in oai_ws:
                            event = json.loads(raw)
                            et = event.get("type", "")

                            # Current Realtime audio event name.
                            if et in (
                                "response.output_audio.delta",
                                "response.audio.delta",
                            ):
                                if stream_sid:
                                    await twilio_ws.send_json({
                                        "event": "media",
                                        "streamSid": stream_sid,
                                        "media": {"payload": event["delta"]},
                                    })

                            # Let the caller interrupt the assistant.
                            elif (
                                et == "input_audio_buffer.speech_started"
                                and stream_sid
                            ):
                                await twilio_ws.send_json({
                                    "event": "clear",
                                    "streamSid": stream_sid,
                                })
                            elif et == "response.done":
                                end_call_requested = await self._handle_tool_calls(
                                    oai_ws, event
                                )

                                response = event.get("response", {})
                                assistant_text = ""

                                for item in response.get("output", []):
                                    if item.get("type") != "message":
                                        continue

                                    for content in item.get("content", []):
                                        transcript = content.get("transcript", "")
                                        if transcript:
                                            assistant_text += " " + transcript

                                farewell_detected = (
                                    "goodbye" in assistant_text.strip().lower()
                                )

                                if (
                                    end_call_requested or farewell_detected
                                ) and stream_sid:
                                    await twilio_ws.send_json({
                                        "event": "mark",
                                        "streamSid": stream_sid,
                                        "mark": {"name": "end_call"},
                                    })
                            elif et == "response.created":
                                pending = getattr(
                                    self, "_pending_response_call_ids", []
                                )
                                response_id = event.get("response", {}).get(
                                    "id", "unknown"
                                )
                                devlog(
                                    "TOOL",
                                    "response.created observed "
                                    f"response_id={response_id} "
                                    f"call_ids={','.join(pending) or 'none'}",
                                )
                                self._pending_response_call_ids = []
                            elif et == "error":
                                pending = getattr(
                                    self, "_pending_response_call_ids", []
                                )
                                devlog(
                                    "OPENAI",
                                    "error correlated_call_ids="
                                    f"{','.join(pending) or 'none'} "
                                    f"detail={event.get('error', event)}",
                                )
                                raise RuntimeError(
                                    "OpenAI Realtime error: "
                                    f"{event.get('error', event)}"
                                )

                        return "openai_stream_ended"
                    except WebSocketDisconnect:
                        return "twilio_disconnect"
                    except ConnectionClosedOK:
                        return "openai_closed"

                twilio_task = asyncio.create_task(
                    twilio_to_openai(), name="twilio-to-openai"
                )
                openai_task = asyncio.create_task(
                    openai_to_twilio(), name="openai-to-twilio"
                )
                tasks = (twilio_task, openai_task)

                try:
                    done, _ = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    # Prefer the Twilio reason when both tasks complete together;
                    # it most directly describes a caller-initiated shutdown.
                    completed = (
                        twilio_task if twilio_task in done else openai_task
                    )
                    termination_reason = completed.result()

                    # Do not hide an unexpected failure if both tasks happened to
                    # complete before FIRST_COMPLETED resumed this coroutine.
                    for task in done:
                        if task is not completed:
                            task.result()
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()

                    try:
                        await oai_ws.close()
                    except ConnectionClosed:
                        pass

                    try:
                        await twilio_ws.close()
                    except (RuntimeError, WebSocketDisconnect):
                        pass

                    await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, ConnectionClosedOK):
            termination_reason = "client_disconnect"
        except Exception:
            termination_reason = "unexpected_error"
            raise
        finally:
            devlog(
                "CALL",
                f"ended call_sid={self.call_sid or 'unknown'} "
                f"reason={termination_reason}",
            )

    async def _configure(self, ws):
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": self.config.system_prompt(),
                "tools": tool_definitions(),
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcmu"},

                        "noise_reduction": {
                            "type": "far_field"
                        },

                        "turn_detection": {
                            "type": "semantic_vad",
                            "eagerness": "high",
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcmu"},
                        "voice": "marin",
                        "speed": 1.0,
                    },
                },
            },
        }))

        # Prompt the receptionist to speak first using the configured greeting.
        await ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "instructions": f'Say this greeting naturally: "{self.config.receptionist.greeting.strip()}"'
            },
        }))

    async def _handle_tool_calls(self, ws, response_done_event):
        response = response_done_event.get("response", {})
        outputs = response.get("output", [])

        end_call_requested = False
        tool_outputs_sent = 0
        continued_call_ids = []
        processed_call_ids = getattr(
            self, "_processed_tool_call_ids", None
        )
        if processed_call_ids is None:
            processed_call_ids = set()
            self._processed_tool_call_ids = processed_call_ids

        for item in outputs:
            if item.get("type") != "function_call":
                continue

            call_id = item["call_id"]
            name = item["name"]
            args = json.loads(item.get("arguments") or "{}")

            if call_id in processed_call_ids:
                devlog(
                    "TOOL",
                    f"duplicate ignored name={name} call_id={call_id}",
                )
                continue

            # Record before starting work. Cancellation of asyncio.to_thread()
            # cannot stop the underlying operation, so a repeated Realtime
            # event must never launch the same mutation a second time.
            processed_call_ids.add(call_id)
            started = time.perf_counter()
            devlog("TOOL", f"started name={name} call_id={call_id}")

            try:
                if name == "send_sms":
                    if not self.caller_number:
                        raise RuntimeError(
                            "Caller phone number is unavailable for SMS."
                        )

                    result = await asyncio.to_thread(
                        send_sms,
                        phone_number=self.caller_number,
                        message_name=args["message_name"],
                        variables=args.get("variables"),
                    )

                else:
                    if (
                        name == "check_late_arrival_status"
                        and self.caller_number
                        and self.caller_number != "Unknown"
                    ):
                        args.setdefault("patient_phone", self.caller_number)

                    result = await asyncio.to_thread(
                        execute_tool,
                        self.scheduler,
                        name,
                        args,
                        config=self.config,
                    )

            except Exception as exc:
                result = {"error": str(exc)}
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                devlog(
                    "TOOL",
                    f"completed name={name} call_id={call_id} "
                    f"elapsed_ms={elapsed_ms:.0f}",
                )

            if result.get("end_call"):
                end_call_requested = True

            await ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result),
                },
            }))
            tool_outputs_sent += 1
            continued_call_ids.append(call_id)
            devlog(
                "TOOL", f"function output sent name={name} call_id={call_id}"
            )

        if tool_outputs_sent and not end_call_requested:
            self._pending_response_call_ids = continued_call_ids
            await ws.send(json.dumps({
                "type": "response.create"
            }))
            devlog(
                "TOOL",
                "response.create requested call_ids="
                f"{','.join(continued_call_ids)}",
            )

        return end_call_requested
