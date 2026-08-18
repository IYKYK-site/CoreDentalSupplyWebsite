from __future__ import annotations
import asyncio
import base64
import json
import os
import websockets
import ssl
import certifi

from .tools import tool_definitions, execute_tool


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

    async def run(self, twilio_ws):
        url = f"wss://api.openai.com/v1/realtime?model={self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        ssl_context = ssl.create_default_context(cafile=certifi.where())

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
                async for text in twilio_ws.iter_text():
                    msg = json.loads(text)
                    event = msg.get("event")
                    if event == "start":
                        stream_sid = msg["start"]["streamSid"]
                    elif event == "media":
                        await oai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": msg["media"]["payload"],
                        }))
                    elif event == "mark":
                        mark_name = msg.get("mark", {}).get("name")

                        if mark_name == "end_call":
                            print("[CALL] Goodbye audio finished. Ending call.", flush=True)

                            await oai_ws.close()
                            await twilio_ws.close()
                            break
                    elif event == "stop":
                        break

            async def openai_to_twilio():
                nonlocal stream_sid
                async for raw in oai_ws:
                    event = json.loads(raw)
                    et = event.get("type", "")

                    # Current Realtime audio event name.
                    if et in ("response.output_audio.delta", "response.audio.delta"):
                        if stream_sid:
                            await twilio_ws.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": event["delta"]},
                            })

                    # Let the caller interrupt the assistant.
                    elif et == "input_audio_buffer.speech_started" and stream_sid:
                        await twilio_ws.send_json({"event": "clear", "streamSid": stream_sid})
                    elif et == "response.done":
                        end_call_requested = await self._handle_tool_calls(oai_ws, event)

                        if end_call_requested and stream_sid:
                            await twilio_ws.send_json({
                                "event": "mark",
                                "streamSid": stream_sid,
                                "mark": {"name": "end_call"},
                            })
                    elif et == "error":
                        print("OpenAI Realtime error:", event)

            await asyncio.gather(twilio_to_openai(), openai_to_twilio())

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

        made_call = False
        end_call_requested = False

        for item in outputs:
            if item.get("type") != "function_call":
                continue

            made_call = True

            call_id = item["call_id"]
            name = item["name"]
            args = json.loads(item.get("arguments") or "{}")

            try:
                result = execute_tool(
                    self.scheduler,
                    name,
                    args,
                )
            except Exception as exc:
                result = {"error": str(exc)}

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

        if made_call and not end_call_requested:
            await ws.send(json.dumps({
                "type": "response.create"
            }))

        return end_call_requested