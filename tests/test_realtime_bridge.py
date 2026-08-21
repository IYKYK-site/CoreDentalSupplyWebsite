import asyncio
import json
import time
from types import SimpleNamespace
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


def make_initialized_bridge(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(realtime_bridge, "Client", lambda *args: object())
    return RealtimeTwilioBridge(config=object(), scheduler=object())


def test_realtime_model_defaults_to_2_1(monkeypatch):
    monkeypatch.delenv("OPENAI_REALTIME_MODEL", raising=False)

    bridge = make_initialized_bridge(monkeypatch)

    assert bridge.model == "gpt-realtime-2.1"


def test_realtime_model_environment_override_is_preserved(monkeypatch):
    monkeypatch.setenv("OPENAI_REALTIME_MODEL", "custom-realtime-model")

    bridge = make_initialized_bridge(monkeypatch)

    assert bridge.model == "custom-realtime-model"


def test_realtime_session_audio_configuration_is_unchanged():
    async def scenario():
        bridge = object.__new__(RealtimeTwilioBridge)
        bridge.config = SimpleNamespace(
            system_prompt=lambda: "test prompt",
            receptionist=SimpleNamespace(greeting="Test greeting"),
        )
        ws = FakeOpenAIWebSocket()

        await bridge._configure(ws)

        session = ws.sent[0]["session"]
        assert session["type"] == "realtime"
        assert session["instructions"] == "test prompt"
        assert session["tool_choice"] == "auto"
        assert session["audio"] == {
            "input": {
                "format": {"type": "audio/pcmu"},
                "noise_reduction": {"type": "far_field"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": "high",
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "format": {"type": "audio/pcmu"},
                "voice": "coral",
                "speed": 1.0,
            },
        }
        assert ws.sent[1] == {
            "type": "response.create",
            "response": {
                "instructions": 'Say this greeting naturally: "Test greeting"'
            },
        }

    asyncio.run(scenario())


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


def test_late_arrival_tool_receives_twilio_caller_id(monkeypatch):
    async def scenario():
        captured = {}
        bridge = object.__new__(RealtimeTwilioBridge)
        bridge.caller_number = "+13055550123"
        bridge.scheduler = object()
        bridge.config = object()
        ws = FakeOpenAIWebSocket()

        def fake_execute_tool(scheduler, name, args, config=None):
            captured.update({
                "scheduler": scheduler,
                "name": name,
                "args": args,
                "config": config,
            })
            return {"recommended_action": "transfer"}

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", fake_execute_tool
        )
        await bridge._handle_tool_calls(ws, {
            "response": {
                "status": "completed",
                "output": [{
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "check_late_arrival_status",
                    "arguments": "{}",
                }],
            }
        })

        assert captured == {
            "scheduler": bridge.scheduler,
            "name": "check_late_arrival_status",
            "args": {"patient_phone": "+13055550123"},
            "config": bridge.config,
        }

    asyncio.run(scenario())


def make_tool_call_bridge():
    bridge = object.__new__(RealtimeTwilioBridge)
    bridge.caller_number = "+13055550123"
    bridge.scheduler = object()
    bridge.config = object()
    bridge._processed_tool_call_ids = set()
    bridge._pending_response_call_ids = []
    return bridge


def function_call_event(status, calls):
    return {
        "response": {
            "status": status,
            "output": [
                {
                    "type": "function_call",
                    "call_id": f"call-{index}",
                    "name": name,
                    "arguments": "{}",
                }
                for index, name in enumerate(calls, start=1)
            ],
        }
    }


@pytest.mark.parametrize("status", ["completed", "incomplete"])
def test_tool_output_always_triggers_one_continuation(
    monkeypatch, status
):
    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        executions = []

        def fake_execute_tool(scheduler, name, args, config=None):
            executions.append(name)
            return {"ok": True}

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", fake_execute_tool
        )
        await bridge._handle_tool_calls(
            ws, function_call_event(status, ["get_available_slots"])
        )

        assert executions == ["get_available_slots"]
        assert [message["type"] for message in ws.sent] == [
            "conversation.item.create",
            "response.create",
        ]

    asyncio.run(scenario())


def test_multiple_tool_outputs_trigger_only_one_continuation(monkeypatch):
    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        executions = []

        def fake_execute_tool(scheduler, name, args, config=None):
            executions.append(name)
            return {"ok": True}

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", fake_execute_tool
        )
        await bridge._handle_tool_calls(
            ws,
            function_call_event(
                "completed",
                ["get_available_slots", "find_first_available_time"],
            ),
        )

        assert executions == [
            "get_available_slots",
            "find_first_available_time",
        ]
        assert [message["type"] for message in ws.sent].count(
            "conversation.item.create"
        ) == 2
        assert [message["type"] for message in ws.sent].count(
            "response.create"
        ) == 1

    asyncio.run(scenario())


def test_end_call_tool_suppresses_continuation(monkeypatch):
    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        executions = []

        def fake_execute_tool(scheduler, name, args, config=None):
            executions.append(name)
            return {"end_call": True}

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", fake_execute_tool
        )
        end_call_requested = await bridge._handle_tool_calls(
            ws, function_call_event("completed", ["end_call"])
        )

        assert executions == ["end_call"]
        assert end_call_requested is True
        assert [message["type"] for message in ws.sent] == [
            "conversation.item.create"
        ]

    asyncio.run(scenario())


def test_identical_call_id_executes_only_once(monkeypatch):
    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        executions = []

        def fake_execute_tool(scheduler, name, args, config=None):
            executions.append(name)
            return {"ok": True}

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", fake_execute_tool
        )
        event = function_call_event("completed", ["reschedule_appointment"])
        await bridge._handle_tool_calls(ws, event)
        await bridge._handle_tool_calls(ws, event)

        assert executions == ["reschedule_appointment"]
        assert [message["type"] for message in ws.sent].count(
            "conversation.item.create"
        ) == 1
        assert [message["type"] for message in ws.sent].count(
            "response.create"
        ) == 1

    asyncio.run(scenario())


def test_distinct_call_ids_execute_normally(monkeypatch):
    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        executions = []

        def fake_execute_tool(scheduler, name, args, config=None):
            executions.append(name)
            return {"ok": True}

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", fake_execute_tool
        )
        await bridge._handle_tool_calls(
            ws, function_call_event("completed", ["get_available_slots"])
        )
        second = function_call_event("completed", ["get_available_slots"])
        second["response"]["output"][0]["call_id"] = "call-distinct"
        await bridge._handle_tool_calls(ws, second)

        assert executions == ["get_available_slots", "get_available_slots"]

    asyncio.run(scenario())


def test_blocking_tool_does_not_block_event_loop_heartbeat(monkeypatch):
    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        ticks = 0

        def slow_execute_tool(scheduler, name, args, config=None):
            time.sleep(0.08)
            return {"ok": True}

        async def heartbeat(task):
            nonlocal ticks
            while not task.done():
                ticks += 1
                await asyncio.sleep(0.005)

        monkeypatch.setattr(
            realtime_bridge, "execute_tool", slow_execute_tool
        )
        tool_task = asyncio.create_task(
            bridge._handle_tool_calls(
                ws,
                function_call_event("completed", ["get_available_slots"]),
            )
        )
        await asyncio.gather(tool_task, heartbeat(tool_task))
        assert ticks >= 5

    asyncio.run(scenario())


def test_tool_continuation_logging_and_openai_acknowledgement(monkeypatch):
    lifecycle = []

    async def scenario():
        response_created = json.dumps({
            "type": "response.created",
            "response": {"id": "resp-123"},
        })
        openai_error = json.dumps({
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "busy"},
        })
        oai_ws = FakeOpenAIWebSocket([response_created, openai_error])
        twilio_ws = FakeTwilioWebSocket(
            [start_event()], wait_for_mark=True
        )
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)
        bridge._pending_response_call_ids = ["call-7"]

        with pytest.raises(RuntimeError, match="invalid_request_error"):
            await bridge.run(twilio_ws)

    asyncio.run(scenario())
    messages = [message for _, message in lifecycle]
    assert any(
        "response.created observed response_id=resp-123 call_ids=call-7"
        in message
        for message in messages
    )
    assert any(
        "error correlated_call_ids=none" in message
        for message in messages
    )


def test_openai_rejection_is_correlated_to_pending_tool_call(monkeypatch):
    lifecycle = []

    async def scenario():
        openai_error = json.dumps({
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "busy"},
        })
        oai_ws = FakeOpenAIWebSocket([openai_error])
        twilio_ws = FakeTwilioWebSocket(
            [start_event()], wait_for_mark=True
        )
        bridge = make_bridge(monkeypatch, oai_ws, lifecycle)
        bridge._pending_response_call_ids = ["call-8"]

        with pytest.raises(RuntimeError, match="invalid_request_error"):
            await bridge.run(twilio_ws)

    asyncio.run(scenario())
    assert any(
        category == "OPENAI"
        and "error correlated_call_ids=call-8" in message
        for category, message in lifecycle
    )


def test_tool_timing_and_continuation_logs_are_correlated(monkeypatch):
    logs = []

    async def scenario():
        bridge = make_tool_call_bridge()
        ws = FakeOpenAIWebSocket()
        monkeypatch.setattr(
            realtime_bridge,
            "execute_tool",
            lambda *args, **kwargs: {"ok": True},
        )
        monkeypatch.setattr(
            realtime_bridge,
            "devlog",
            lambda category, message: logs.append((category, message)),
        )

        await bridge._handle_tool_calls(
            ws, function_call_event("completed", ["get_available_slots"])
        )

    asyncio.run(scenario())
    messages = [message for _, message in logs]
    assert "started name=get_available_slots call_id=call-1" in messages
    assert any(
        message.startswith(
            "completed name=get_available_slots call_id=call-1 elapsed_ms="
        )
        for message in messages
    )
    assert "function output sent name=get_available_slots call_id=call-1" in messages
    assert "response.create requested call_ids=call-1" in messages
