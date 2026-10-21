"""Parsing helpers for OpenAI-compatible streaming (`stream: true`). Pure logic: no network, no server code.

Two pieces:
  * iter_sse_data(lines)  - turns the raw Server-Sent-Events byte lines into one data string per event.
  * StreamAssembler       - folds the JSON chunks into text deltas and complete tool calls.

Streamed tool calls arrive in fragments: the function name usually comes once, the JSON arguments come in many small
pieces, and several calls are told apart by `index`. Some providers repeat index 0 for every call or omit the id, so
the assembler also starts a new call when it sees a different id on a slot that already has one.
"""
import json

from analyzer import AnalyzerError


def iter_sse_data(lines):
    """Yield the data payload (str) of each SSE event. `lines` is an iterable of bytes or str lines.
    Handles CRLF, comment/keep-alive lines (": ..."), 'data:' with or without a space, and multi-line data.
    Stops at the OpenAI-style '[DONE]' sentinel."""
    buf = []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.rstrip("\r\n")
        if not line:  # blank line = end of one event
            if buf:
                data = "\n".join(buf)
                buf = []
                if data.strip() == "[DONE]":
                    return
                yield data
            continue
        if line.startswith(":"):  # comment, often used as a keep-alive
            continue
        field, _, value = line.partition(":")
        if field == "data":
            buf.append(value[1:] if value.startswith(" ") else value)
        # other SSE fields (event:, id:, retry:) are not used by chat completions
    if buf:  # stream ended without a final blank line
        data = "\n".join(buf)
        if data.strip() != "[DONE]":
            yield data


class StreamAssembler:
    """feed(chunk) -> text delta (str, maybe empty). message() -> the assembled assistant message dict."""

    def __init__(self):
        self.text = []
        self.calls = []        # list of {"id", "name", "args"}
        self._slot = {}        # provider index -> position in self.calls
        self.finish_reason = None
        self.started = False   # True once any chunk with choices was fed

    def feed(self, chunk):
        if not isinstance(chunk, dict):
            raise AnalyzerError("invalid_ai_response", "The AI provider sent an unexpected streaming chunk.", 502)
        if chunk.get("error"):  # some providers report errors inside the stream
            raise AnalyzerError("llm_stream_error", "The AI provider reported an error while streaming the answer.", 502)
        choices = chunk.get("choices") or []
        if not choices:  # e.g. a final usage-only chunk
            return ""
        self.started = True
        choice = choices[0] or {}
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        for tc in delta.get("tool_calls") or []:
            self._feed_tool_call(tc)
        # `reasoning` / `reasoning_content` deltas (the model's private thinking) are deliberately ignored.
        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            self.text.append(piece)
            return piece
        return ""

    def _feed_tool_call(self, tc):
        if not isinstance(tc, dict):
            return
        fn = tc.get("function") or {}
        idx = tc.get("index")
        idx = idx if isinstance(idx, int) else (len(self.calls) - 1 if self.calls and not tc.get("id") else len(self.calls))
        pos = self._slot.get(idx)
        tc_id = tc.get("id")
        if pos is not None and tc_id and self.calls[pos]["id"] and self.calls[pos]["id"] != tc_id:
            pos = None  # same index, different id: a new call (providers that repeat index 0)
        if pos is None:
            pos = len(self.calls)
            self.calls.append({"id": tc_id or "", "name": "", "args": ""})
            self._slot[idx] = pos
        call = self.calls[pos]
        if tc_id and not call["id"]:
            call["id"] = tc_id
        if isinstance(fn.get("name"), str):
            if not call["name"] or fn["name"] != call["name"]:
                call["name"] += fn["name"]  # normally sent once; concatenation also covers split names
        if isinstance(fn.get("arguments"), str):
            call["args"] += fn["arguments"]

    def message(self):
        msg = {"content": "".join(self.text)}
        calls = [{"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                 for i, c in enumerate(self.calls) if c["name"]]
        if calls:
            msg["tool_calls"] = calls
        return msg


def message_to_chunk(message, finish_reason="stop"):
    """Wraps a complete (non-streamed) assistant message as one streaming chunk. Used when a provider ignores
    `stream: true` and answers with plain JSON."""
    delta = {"content": message.get("content") or ""}
    calls = message.get("tool_calls") or []
    if calls:
        delta["tool_calls"] = [{"index": i, "id": c.get("id"), "type": "function", "function": c.get("function") or {}} for i, c in enumerate(calls)]
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


def parse_event(data):
    try:
        return json.loads(data)
    except ValueError:
        raise AnalyzerError("invalid_ai_response", "The AI provider sent a streaming chunk that is not valid JSON.", 502)
