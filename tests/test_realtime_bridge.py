import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect

from app import realtime_bridge
from app.realtime_bridge import RealtimeTwilioBridge


class FakeConnect:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, traceback):
        await self.websocket.close()


class FakeOpenAIWebSocket:
    def __init__(self, events=(), error=None):
        self.events = list(events)
        self.error = error
        self.close_count = 0
        self.sent = []
        self._closed = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.events:
            return self.events.pop(0)
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        await self._closed.wait()
        raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def close(self):
        self.close_count += 1
        self._closed.set()


class FakeTwilioWebSocket:
    def __init__(self, events=(), disconnect=False, wait_for_mark=False):
        self.events = list(events)
        self.disconnect = disconnect
        self.wait_for_mark = wait_for_mark
        self.close_count = 0
        self.sent = []
        self.cancelled = False
        self._mark_received = asyncio.Event()

    async def iter_text(self):
        try:
            for event in self.events:
                yield json.dumps(event)
            if self.disconnect:
                raise WebSocketDisconnect(code=1000)
            if self.wait_for_mark:
                await self._mark_received.wait()
                yield json.dumps({
                    "event": "mark",
                    "mark": {"name": "end_call"},
                })
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def send_json(self, message):
        self.sent.append(message)
        if message.get("event") == "mark":
            self._mark_received.set()

    async def close(self):
        self.close_count += 1


def start_event():
    return {
        "event": "start",
        "start": {
            "streamSid": "MZ123",
            "callSid": "CA123",
            "customParameters": {"caller_number": "+13055550123"},
        },
    }


def make_bridge(monkeypatch, oai_ws, lifecycle=None):
    bridge = object.__new__(RealtimeTwilioBridge)
    bridge.model = "test-model"
    bridge.api_key = "test-key"
    bridge.call_sid = None
    bridge.caller_number = None
    bridge._configure = AsyncMock()
    bridge._handle_tool_calls = AsyncMock(return_value=False)
    monkeypatch.setattr(
        realtime_bridge.websockets,
        "connect",
        lambda *args, **kwargs: FakeConnect(oai_ws),
    )
    if lifecycle is not None:
        monkeypatch.setattr(
            realtime_bridge,
            "devlog",
            lambda category, message: lifecycle.append((category, message)),
        )
    return bridge


def test_twilio_stop_closes_openai_and_creates_exactly_two_tasks(
    monkeypatch,
):
    lifecycle = []

    async def scenario():
        oai_ws = FakeOpenAIWebSocket()
        twilio_ws = FakeTwilioWebSocket([
            start_event(),
            {"event": "stop"},
        ])
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)
        task_names = []
        original_create_task = asyncio.create_task

        def recording_create_task(coro, *, name=None, context=None):
            task_names.append(name)
            return original_create_task(coro, name=name, context=context)

        monkeypatch.setattr(asyncio, "create_task", recording_create_task)
        await bridge.run(twilio_ws)

        assert task_names == ["twilio-to-openai", "openai-to-twilio"]
        assert oai_ws.close_count >= 1
        assert twilio_ws.close_count >= 1

    asyncio.run(scenario())
    assert lifecycle == [
        ("CALL", "started call_sid=CA123 stream_sid=MZ123"),
        ("CALL", "ended call_sid=CA123 reason=twilio_stop"),
    ]


def test_twilio_disconnect_is_expected_and_cancels_openai(monkeypatch):
    lifecycle = []

    async def scenario():
        oai_ws = FakeOpenAIWebSocket()
        twilio_ws = FakeTwilioWebSocket(
            [start_event()], disconnect=True
        )
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)

        await bridge.run(twilio_ws)

        assert oai_ws.close_count >= 1
        assert twilio_ws.close_count >= 1

    asyncio.run(scenario())
    assert lifecycle[-1] == (
        "CALL",
        "ended call_sid=CA123 reason=twilio_disconnect",
    )


def test_natural_twilio_stream_end_closes_openai(monkeypatch):
    lifecycle = []

    async def scenario():
        oai_ws = FakeOpenAIWebSocket()
        twilio_ws = FakeTwilioWebSocket([start_event()])
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)

        await bridge.run(twilio_ws)

        assert oai_ws.close_count >= 1
        assert twilio_ws.close_count >= 1

    asyncio.run(scenario())
    assert lifecycle[-1] == (
        "CALL",
        "ended call_sid=CA123 reason=twilio_stream_ended",
    )


def test_farewell_mark_closes_call_and_handles_response_once(
    monkeypatch,
):
    lifecycle = []

    async def scenario():
        response_done = json.dumps({
            "type": "response.done",
            "response": {
                "output": [{
                    "type": "message",
                    "content": [{"transcript": "Have a nice day. Goodbye."}],
                }],
            },
        })
        oai_ws = FakeOpenAIWebSocket([response_done])
        twilio_ws = FakeTwilioWebSocket(
            [start_event()], wait_for_mark=True
        )
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)

        await bridge.run(twilio_ws)

        bridge._handle_tool_calls.assert_awaited_once()
        assert any(
            message.get("mark", {}).get("name") == "end_call"
            for message in twilio_ws.sent
        )

    asyncio.run(scenario())
    assert lifecycle[-1] == (
        "CALL",
        "ended call_sid=CA123 reason=farewell_audio_complete",
    )


def test_unexpected_openai_error_propagates_after_peer_cleanup(
    monkeypatch,
):
    lifecycle = []

    async def scenario():
        oai_ws = FakeOpenAIWebSocket(error=ValueError("broken stream"))
        twilio_ws = FakeTwilioWebSocket(
            [start_event()], wait_for_mark=True
        )
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)

        with pytest.raises(ValueError, match="broken stream"):
            await bridge.run(twilio_ws)

        assert twilio_ws.cancelled is True
        assert oai_ws.close_count >= 1
        assert twilio_ws.close_count >= 1

    asyncio.run(scenario())
    assert lifecycle[-1] == (
        "CALL",
        "ended call_sid=CA123 reason=unexpected_error",
    )
