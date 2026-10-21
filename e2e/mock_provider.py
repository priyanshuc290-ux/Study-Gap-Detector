"""A fake OpenAI-compatible provider for testing ONLY. It never sees a real key: it expects the key "test-key".
Run:  python e2e/mock_provider.py   (listens on http://127.0.0.1:9911/v1)

The scenario is chosen by a marker inside the student's last message, e.g. "SCENARIO:tools".
  tools     reasoning chunks, then two streamed tool calls (arguments split over several chunks), then a streamed answer
  slow      40 slow tokens (to test the Stop button; /_state tells whether the connection was closed from our side)
  nostream  HTTP 400 when stream:true is sent, normal JSON answer otherwise (provider that rejects streaming)
  flaky     first request: 3 tokens then an error chunk; later requests succeed (tests Retry)
  plain     a short streamed answer
GET /_state returns what the mock saw; POST /_reset clears it.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"requests": [], "slow_disconnected": False, "flaky_calls": 0, "slow_started": False}
ANSWER = ("You are doing well on **Arrays**, but **Linked Lists** needs work.\n\n"
          "- Re-read how a node stores a pointer\n- Practise insertion and deletion\n- Trace a reversal by hand\n\n"
          "Next step: start weak-topic practice below.")


def words(text):
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch == " " or ch == "\n":
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def chunk(delta, finish=None):
    return {"id": "mock", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/_state":
            return self._send_json(200, STATE)
        self._send_json(404, {})

    def sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def sse(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def do_POST(self):
        if self.path == "/_reset":
            STATE.update({"requests": [], "slow_disconnected": False, "flaky_calls": 0, "slow_started": False})
            return self._send_json(200, {})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer test-key":
            return self._send_json(401, {"error": {"message": "bad key"}})
        msgs = body["messages"]
        last_user = next((m["content"] for m in reversed(msgs) if m["role"] == "user" and isinstance(m["content"], str)), "")
        scenario = next((s for s in ("tools", "slow", "nostream", "flaky", "plain") if f"SCENARIO:{s}" in last_user), "plain")
        has_tool_result = any(m["role"] == "tool" for m in msgs)
        STATE["requests"].append({"scenario": scenario, "stream": bool(body.get("stream")), "tools": len(body.get("tools") or []),
                                  "tool_results": sum(1 for m in msgs if m["role"] == "tool"), "n_messages": len(msgs),
                                  "system_has_doc": "<document>" in str(msgs[0].get("content")),
                                  "tool_payloads": [json.loads(m["content"]) for m in msgs if m["role"] == "tool"]})
        if scenario == "nostream" and body.get("stream"):
            return self._send_json(400, {"error": {"message": "streaming is not supported by this model"}})
        if scenario == "nostream":
            return self._send_json(200, {"choices": [{"message": {"role": "assistant", "content": "Whole answer, not streamed."}, "finish_reason": "stop"}]})
        if not body.get("stream"):  # one-shot request (older backend path): plain JSON, never SSE
            return self._send_json(200, {"choices": [{"message": {"role": "assistant", "content": "A stack is a last-in, first-out structure."}, "finish_reason": "stop"}]})
        try:
            self.sse_start()
            if scenario == "tools" and not has_tool_result:
                self.sse(chunk({"role": "assistant", "reasoning": "The student wants a plan, check the results first."}))
                time.sleep(0.4)
                self.sse(chunk({"reasoning": " Then prepare a button."}))
                time.sleep(0.4)
                self.sse(chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_study_results", "arguments": ""}}]}))
                for piece in ("{", "}"):
                    self.sse(chunk({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}))
                self.sse(chunk({"tool_calls": [{"index": 1, "id": "call_2", "type": "function", "function": {"name": "suggest_action", "arguments": ""}}]}))
                for piece in ('{"act', 'ion": "wea', 'k_practice"}'):
                    self.sse(chunk({"tool_calls": [{"index": 1, "function": {"arguments": piece}}]}))
                    time.sleep(0.05)
                self.sse(chunk({}, "tool_calls"))
            elif scenario == "slow":
                STATE["slow_started"] = True
                for i in range(40):
                    self.sse(chunk({"content": f"word{i} "}))
                    time.sleep(0.2)
                self.sse(chunk({}, "stop"))
            elif scenario == "flaky":
                STATE["flaky_calls"] += 1
                if STATE["flaky_calls"] == 1:
                    for w in ("This ", "will ", "break "):
                        self.sse(chunk({"content": w}))
                        time.sleep(0.1)
                    self.sse({"error": {"message": "upstream overloaded"}})
                else:
                    for w in words("Second try worked fine."):
                        self.sse(chunk({"content": w}))
                    self.sse(chunk({}, "stop"))
            else:
                text = ANSWER if scenario == "tools" else "A stack is a last-in, first-out structure."
                for w in words(text):
                    self.sse(chunk({"content": w}))
                    time.sleep(0.12 if scenario == "tools" else 0.02)
                self.sse(chunk({}, "stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            if scenario == "slow":
                STATE["slow_disconnected"] = True  # our backend closed the stream, as it should after Stop


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9911
    print(f"Mock provider on http://127.0.0.1:{port}/v1  (testing only)")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
