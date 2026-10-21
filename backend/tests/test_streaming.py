"""Run from the project root:  python -m unittest discover -s backend/tests -v
Streaming tests. They use fake providers only: no real AI API (and no key from backend/.env) is ever used."""
import http.client
import io
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyzer  # noqa: E402
import chat  # noqa: E402
import streaming  # noqa: E402
from streaming import StreamAssembler, iter_sse_data  # noqa: E402


def sse(*objs, done=True, crlf=False):
    nl = "\r\n" if crlf else "\n"
    body = "".join(f"data: {json.dumps(o)}{nl}{nl}" for o in objs)
    if done:
        body += f"data: [DONE]{nl}{nl}"
    return body.encode()


def text_chunk(t, finish=None):
    return {"choices": [{"index": 0, "delta": {"content": t}, "finish_reason": finish}]}


def tool_chunk(index, id=None, name=None, args=None, finish=None):
    fn = {}
    if name is not None:
        fn["name"] = name
    if args is not None:
        fn["arguments"] = args
    tc = {"index": index, "function": fn}
    if id:
        tc["id"], tc["type"] = id, "function"
    return {"choices": [{"index": 0, "delta": {"tool_calls": [tc]}, "finish_reason": finish}]}


class SSEParserTests(unittest.TestCase):
    def test_basic_events_and_done(self):
        lines = io.BytesIO(sse({"a": 1}, {"a": 2}))
        self.assertEqual([json.loads(d) for d in iter_sse_data(lines)], [{"a": 1}, {"a": 2}])

    def test_crlf_comments_and_no_space_after_colon(self):
        raw = b": keep-alive\r\n\r\ndata:{\"a\":1}\r\n\r\n: ping\r\n\r\ndata: {\"a\":2}\r\n\r\ndata: [DONE]\r\n\r\n"
        self.assertEqual([json.loads(d) for d in iter_sse_data(io.BytesIO(raw))], [{"a": 1}, {"a": 2}])

    def test_multiline_data_and_other_fields_ignored(self):
        raw = b"event: message\nid: 7\ndata: {\"a\":\ndata: 1}\n\n"
        self.assertEqual(list(iter_sse_data(io.BytesIO(raw))), ['{"a":\n1}'])

    def test_stream_without_final_blank_line_and_nothing_after_done(self):
        raw = b'data: {"a":1}\n\ndata: [DONE]\n\ndata: {"a":99}\n\n'
        self.assertEqual(list(iter_sse_data(io.BytesIO(raw))), ['{"a":1}'])
        self.assertEqual(list(iter_sse_data(io.BytesIO(b'data: {"a":3}'))), ['{"a":3}'])

    def test_invalid_json_is_a_clean_error(self):
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            streaming.parse_event("{not json")
        self.assertEqual(cm.exception.code, "invalid_ai_response")


class AssemblerTests(unittest.TestCase):
    def test_text_deltas_and_finish_reason(self):
        a = StreamAssembler()
        self.assertEqual([a.feed(text_chunk(t)) for t in ("Hel", "lo ", "world")], ["Hel", "lo ", "world"])
        a.feed(text_chunk("", finish="stop"))
        self.assertEqual(a.message(), {"content": "Hello world"})
        self.assertEqual(a.finish_reason, "stop")

    def test_tool_call_arguments_assembled_across_chunks(self):
        a = StreamAssembler()
        for c in (tool_chunk(0, "call_a", "get_syllabus_topics", ""), tool_chunk(0, args='{"subj'), tool_chunk(0, args='ect": "Data Struc'),
                  tool_chunk(0, args='tures", "unit"'), tool_chunk(0, args=": 1}", finish="tool_calls")):
            a.feed(c)
        calls = a.message()["tool_calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "call_a")
        self.assertEqual(calls[0]["function"]["name"], "get_syllabus_topics")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"subject": "Data Structures", "unit": 1})

    def test_two_parallel_calls_with_interleaved_indexes(self):
        a = StreamAssembler()
        for c in (tool_chunk(0, "c0", "get_study_results", ""), tool_chunk(1, "c1", "find_resources", ""),
                  tool_chunk(1, args='{"subject":"Data Structures",'), tool_chunk(0, args="{}"), tool_chunk(1, args='"topic":"Arrays"}')):
            a.feed(c)
        calls = a.message()["tool_calls"]
        self.assertEqual([c["function"]["name"] for c in calls], ["get_study_results", "find_resources"])
        self.assertEqual(json.loads(calls[1]["function"]["arguments"]), {"subject": "Data Structures", "topic": "Arrays"})
        self.assertEqual(calls[0]["function"]["arguments"], "{}")

    def test_provider_that_repeats_index_zero_for_every_call(self):
        a = StreamAssembler()
        a.feed(tool_chunk(0, "x1", "get_study_results", "{}"))
        a.feed(tool_chunk(0, "x2", "suggest_action", '{"action":"diagnostic"}'))
        calls = a.message()["tool_calls"]
        self.assertEqual([(c["id"], c["function"]["name"]) for c in calls], [("x1", "get_study_results"), ("x2", "suggest_action")])

    def test_missing_id_gets_a_generated_one_and_empty_args_become_object(self):
        a = StreamAssembler()
        a.feed(tool_chunk(0, None, "get_study_results"))
        call = a.message()["tool_calls"][0]
        self.assertTrue(call["id"])
        self.assertEqual(call["function"]["arguments"], "{}")

    def test_reasoning_usage_and_empty_choices_are_ignored(self):
        a = StreamAssembler()
        self.assertEqual(a.feed({"choices": [{"delta": {"reasoning": "secret thinking"}}]}), "")
        self.assertEqual(a.feed({"choices": [], "usage": {"total_tokens": 5}}), "")
        self.assertEqual(a.feed(text_chunk("Hi")), "Hi")
        self.assertEqual(a.message(), {"content": "Hi"})

    def test_error_chunk_raises(self):
        with self.assertRaises(analyzer.AnalyzerError):
            StreamAssembler().feed({"error": {"message": "overloaded"}})

    def test_content_and_tool_calls_in_one_message(self):
        a = StreamAssembler()
        a.feed(text_chunk("Let me check."))
        a.feed(tool_chunk(0, "c", "get_study_results", "{}"))
        m = a.message()
        self.assertEqual(m["content"], "Let me check.")
        self.assertEqual(len(m["tool_calls"]), 1)


class FakeResp:
    def __init__(self, body, ctype="text/event-stream"):
        self.body, self.headers = io.BytesIO(body), {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.body)

    def read(self):
        return self.body.read()


CFG = {"key": "sekret", "base_url": "http://x/v1", "model": "m", "timeout": 5, "max_tokens": 100}


class CallLlmStreamTests(unittest.TestCase):
    def test_request_asks_for_streaming_and_yields_chunks(self):
        seen = {}

        def opener(req, timeout=None):
            seen["payload"] = json.loads(req.data)
            seen["auth"] = req.get_header("Authorization")
            return FakeResp(sse(text_chunk("A"), text_chunk("B")))

        chunks = list(analyzer.call_llm_stream([{"role": "user", "content": "hi"}], CFG, chat.TOOLS, opener=opener))
        self.assertTrue(seen["payload"]["stream"])
        self.assertIn("tools", seen["payload"])
        self.assertEqual(seen["auth"], "Bearer sekret")
        self.assertEqual([c["choices"][0]["delta"]["content"] for c in chunks], ["A", "B"])

    def test_provider_that_ignores_stream_and_returns_json(self):
        body = json.dumps({"choices": [{"message": {"content": "whole", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_study_results", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}).encode()
        chunks = list(analyzer.call_llm_stream([], CFG, None, opener=lambda r, timeout=None: FakeResp(body, "application/json")))
        a = StreamAssembler()
        for c in chunks:
            a.feed(c)
        m = a.message()
        self.assertEqual(m["content"], "whole")
        self.assertEqual(m["tool_calls"][0]["function"]["name"], "get_study_results")

    def test_http_error_keeps_status_and_redacts_key(self):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError("http://x", 400, "Bad", {}, io.BytesIO(b'{"error":"stream sekret not allowed"}'))
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            list(analyzer.call_llm_stream([], CFG, None, opener=opener))
        self.assertEqual(cm.exception.code, "llm_http_error")
        self.assertEqual(cm.exception.upstream_status, 400)
        self.assertNotIn("sekret", cm.exception.message)

    def test_connection_failure_is_llm_unavailable(self):
        def opener(req, timeout=None):
            raise urllib.error.URLError("down")
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            list(analyzer.call_llm_stream([], CFG, None, opener=opener))
        self.assertEqual(cm.exception.code, "llm_unavailable")

    def test_cut_off_chunked_stream_is_llm_unavailable(self):
        class Cut(FakeResp):
            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"content":"A"}}]}\n'
                yield b"\n"
                raise http.client.IncompleteRead(b"")
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            list(analyzer.call_llm_stream([], CFG, None, opener=lambda r, timeout=None: Cut(b"")))
        self.assertEqual(cm.exception.code, "llm_unavailable")


# ---- fake streaming providers for the agent loop
def stream_of(*chunks):
    def gen(messages, cfg, tools=None):
        yield from chunks
    return gen


class ScriptedStream:
    """Each call consumes the next list of chunks (or raises it if it is an exception)."""
    def __init__(self, *rounds):
        self.rounds, self.calls, self.tools_seen = list(rounds), [], []

    def __call__(self, messages, cfg, tools=None):
        self.calls.append([dict(m) for m in messages])
        self.tools_seen.append(tools)
        item = self.rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        yield from item


def err400():
    e = analyzer.AnalyzerError("llm_http_error", "provider said no", 502)
    e.upstream_status = 400
    return e


CTX = [{"subject": "Data Structures", "unit": 1, "year": 2, "semester": 1, "attempts": 2, "accuracy_trend": [40, 70],
        "strong": ["Arrays"], "weak": ["Linked Lists"], "insufficient": [],
        "topics": {"Linked Lists": {"correct": 1, "answered": 3}}}]


def new_run():
    return {"reply": "", "steps": [], "actions": [], "notes": [], "no_stream": False, "emitted": False,
            "chunk_seen": False, "wrote": 0, "finish": None, "truncated": False}


class StreamAgentTests(unittest.TestCase):
    def run_agent(self, llm_stream, llm=None, context=None, use_tools=True):
        run = new_run()
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
        events = list(chat.stream_agent(msgs, CFG, context if context is not None else CTX, llm_stream, llm or (lambda *a, **k: {"content": "NON-STREAM"}), use_tools, run))
        return events, run, msgs

    def kinds(self, events):
        return [e["type"] for e in events if e["type"] not in ("status", "ping")]

    def test_plain_answer_streams_tokens_in_order(self):
        ev, run, _ = self.run_agent(stream_of(text_chunk("Hel"), text_chunk("lo"), text_chunk("", "stop")))
        self.assertEqual([e["text"] for e in ev if e["type"] == "token"], ["Hel", "lo"])
        self.assertEqual(run["reply"], "Hello")
        self.assertEqual(ev[0], {"type": "status", "state": "thinking"})
        self.assertIn({"type": "status", "state": "writing"}, ev)

    def test_tool_round_then_answer_with_live_step_events(self):
        s = ScriptedStream(
            [tool_chunk(0, "c1", "get_study_results", ""), tool_chunk(0, args="{}", finish="tool_calls")],
            [text_chunk("You are "), text_chunk("weak in Linked Lists.")])
        ev, run, msgs = self.run_agent(s)
        self.assertEqual(self.kinds(ev), ["step_start", "step_done", "token", "token"])
        start, done = [e for e in ev if e["type"] == "step_start"][0], [e for e in ev if e["type"] == "step_done"][0]
        self.assertEqual((start["id"], start["tool"], done["id"]), (0, "get_study_results", 0))
        self.assertEqual(done["label"], "Checked your quiz results")
        self.assertEqual(run["reply"], "You are weak in Linked Lists.")
        self.assertEqual(run["steps"], [{"tool": "get_study_results", "label": "Checked your quiz results"}])
        # the tool result went back to the model, and the second call is a normal tool round
        tool_msg = [m for m in s.calls[1] if m["role"] == "tool"][0]
        self.assertEqual(tool_msg["tool_call_id"], "c1")
        self.assertTrue(json.loads(tool_msg["content"])["available"])
        self.assertIsNotNone(s.tools_seen[0])

    def test_streamed_tool_call_arguments_reach_the_tool(self):
        s = ScriptedStream(
            [tool_chunk(0, "c1", "get_syllabus_topics", ""), tool_chunk(0, args='{"subject":"Data Structures",'), tool_chunk(0, args='"unit":1}')],
            [text_chunk("ok")])
        ev, run, _ = self.run_agent(s)
        self.assertEqual([e["label"] for e in ev if e["type"] == "step_done"], ["Read the syllabus: Data Structures, unit 1"])
        self.assertEqual([e["label"] for e in ev if e["type"] == "step_start"], ["Reading the syllabus: Data Structures, unit 1…"])

    def test_text_before_a_tool_call_is_reset(self):
        s = ScriptedStream([text_chunk("Let me look. "), tool_chunk(0, "c1", "get_study_results", "{}")], [text_chunk("Final.")])
        ev, run, _ = self.run_agent(s)
        self.assertEqual(self.kinds(ev), ["token", "reset", "step_start", "step_done", "token"])
        self.assertEqual(run["reply"], "Final.")

    def test_action_prepared_by_suggest_action(self):
        s = ScriptedStream([tool_chunk(0, "c1", "suggest_action", '{"action":"weak_practice"}')], [text_chunk("Click the button.")])
        _, run, _ = self.run_agent(s)
        self.assertEqual(run["actions"], [{"action": "weak_practice", "label": "Start weak-topic practice"}])

    def test_tool_loop_is_bounded(self):
        rounds = [[tool_chunk(0, f"c{i}", "get_study_results", "{}")] for i in range(chat.MAX_TOOL_ROUNDS)] + [[text_chunk("forced answer")]]
        s = ScriptedStream(*rounds)
        ev, run, _ = self.run_agent(s)
        self.assertEqual(len(s.calls), chat.MAX_TOOL_ROUNDS + 1)
        self.assertIsNone(s.tools_seen[-1])  # the last round offers no tools
        self.assertEqual(run["reply"], "forced answer")
        self.assertEqual(len(run["steps"]), chat.MAX_TOOL_ROUNDS)

    def test_provider_rejecting_stream_falls_back_to_non_streaming(self):
        s = ScriptedStream(err400())
        ev, run, _ = self.run_agent(s, llm=lambda m, c, t=None: {"content": "Whole answer"})
        self.assertEqual([e["text"] for e in ev if e["type"] == "token"], ["Whole answer"])
        self.assertTrue(run["no_stream"])
        self.assertEqual(run["reply"], "Whole answer")

    def test_fallback_applies_to_later_rounds_without_retrying_the_stream(self):
        s = ScriptedStream(err400())
        replies = [{"content": "", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "get_study_results", "arguments": "{}"}}]}, {"content": "after tool"}]
        ev, run, _ = self.run_agent(s, llm=lambda m, c, t=None: replies.pop(0))
        self.assertEqual(len(s.calls), 1)  # the stream was only tried once
        self.assertEqual(self.kinds(ev), ["step_start", "step_done", "token"])

    def test_bad_key_is_not_mistaken_for_streaming_rejection(self):
        e401 = analyzer.AnalyzerError("llm_http_error", "bad key", 502)
        e401.upstream_status = 401
        called = []
        with self.assertRaises(analyzer.AnalyzerError):
            self.run_agent(ScriptedStream(e401), llm=lambda *a, **k: called.append(1))
        self.assertEqual(called, [])

    def test_error_after_chunks_is_raised_not_replayed(self):
        def gen(messages, cfg, tools=None):
            yield text_chunk("partial ")
            raise analyzer.AnalyzerError("llm_unavailable", "dropped", 504)
        run = new_run()
        out = []
        with self.assertRaises(analyzer.AnalyzerError):
            for e in chat.stream_agent([{"role": "user", "content": "q"}], CFG, CTX, gen, lambda *a, **k: self.fail("must not fall back"), True, run):
                out.append(e)
        self.assertIn({"type": "token", "text": "partial "}, out)

    def test_reply_is_capped_and_provider_stream_closed(self):
        closed = []

        def gen(messages, cfg, tools=None):
            try:
                for _ in range(100):
                    yield text_chunk("x" * 200)
            finally:
                closed.append(True)
        ev, run, _ = self.run_agent(gen)
        self.assertEqual(sum(len(e["text"]) for e in ev if e["type"] == "token"), chat.MAX_REPLY)
        self.assertEqual(len(run["reply"]), chat.MAX_REPLY)
        self.assertTrue(closed)
        self.assertTrue(any("cut off" in n for n in run["notes"]))

    def test_length_finish_reason_adds_a_note(self):
        _, run, _ = self.run_agent(stream_of(text_chunk("abc", finish="length")))
        self.assertTrue(any("LLM_MAX_TOKENS" in n for n in run["notes"]))

    def test_ping_while_model_only_thinks(self):
        old = chat.PING_EVERY
        chat.PING_EVERY = 0.0
        try:
            ev, _, _ = self.run_agent(stream_of({"choices": [{"delta": {"reasoning": "hmm"}}]}, text_chunk("ok")))
        finally:
            chat.PING_EVERY = old
        self.assertIn({"type": "ping"}, ev)

    def test_closing_the_event_generator_closes_the_provider_stream(self):
        closed = []

        def gen(messages, cfg, tools=None):
            try:
                for i in range(1000):
                    yield text_chunk("t")
            finally:
                closed.append(True)
        g = chat.stream_agent([{"role": "user", "content": "q"}], CFG, CTX, gen, None, True, new_run())
        for e in g:
            if e["type"] == "token":
                break
        g.close()
        self.assertTrue(closed)


class ChatStreamTests(unittest.TestCase):
    def setUp(self):
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ.pop("LLM_VISION_MODEL", None)

    def tearDown(self):
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("LLM_VISION_MODEL", None)

    def test_validation_errors_happen_before_streaming_starts(self):
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            chat.chat_stream({"message": ""}, llm_stream=stream_of())
        self.assertEqual(cm.exception.code, "invalid_request")
        os.environ.pop("LLM_API_KEY")
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            chat.chat_stream({"message": "hi"}, llm_stream=stream_of())
        self.assertEqual(cm.exception.code, "ai_not_configured")

    def test_done_event_carries_reply_steps_actions_and_document(self):
        s = ScriptedStream([tool_chunk(0, "c1", "get_study_results", "{}"), tool_chunk(1, "c2", "suggest_action", '{"action":"weak_practice"}')],
                           [text_chunk("Practise "), text_chunk("linked lists.")])
        payload = {"message": "what next?", "study_context": CTX, "document": {"name": "notes.pdf", "text": "stack and queue"}}
        ev = list(chat.chat_stream(payload, llm_stream=s))
        self.assertEqual([e["type"] for e in ev if e["type"] in ("actions", "done")], ["actions", "done"])
        done = ev[-1]
        self.assertEqual(done["reply"], "Practise linked lists.")
        self.assertEqual([x["tool"] for x in done["steps"]], ["get_study_results", "suggest_action"])
        self.assertEqual(done["actions"][0]["action"], "weak_practice")
        self.assertEqual(done["document"]["name"], "notes.pdf")
        self.assertNotIn("test-key", json.dumps(ev))

    def test_history_and_system_prompt_are_sent_like_the_non_streaming_path(self):
        s = ScriptedStream([text_chunk("hi")])
        list(chat.chat_stream({"message": "now", "history": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}, llm_stream=s))
        self.assertEqual([m["role"] for m in s.calls[0]], ["system", "user", "assistant", "user"])

    def test_tools_rejected_retries_plain_chat_with_note_and_still_streams(self):
        s = ScriptedStream(err400(), [text_chunk("plain "), text_chunk("answer")])  # 1st: stream+tools rejected (then non-stream+tools rejected too)
        ev = list(chat.chat_stream({"message": "hi"}, llm_stream=s, llm=lambda m, c, t=None: (_ for _ in ()).throw(err400())))
        done = ev[-1]
        self.assertEqual(done["reply"], "plain answer")
        self.assertTrue(any("did not accept tools" in n for n in done["notes"]))
        self.assertFalse(any("did not accept streaming" in n for n in done["notes"]))
        self.assertIsNone(s.tools_seen[-1])

    def test_streaming_unsupported_adds_an_honest_note(self):
        ev = list(chat.chat_stream({"message": "hi"}, llm_stream=ScriptedStream(err400()), llm=lambda m, c, t=None: {"content": "all at once"}))
        self.assertEqual(ev[-1]["reply"], "all at once")
        self.assertTrue(any("did not accept streaming" in n for n in ev[-1]["notes"]))

    def test_empty_answer_is_an_error(self):
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            list(chat.chat_stream({"message": "hi"}, llm_stream=stream_of(text_chunk("  "))))
        self.assertEqual(cm.exception.code, "invalid_ai_response")

    def test_image_without_vision_model_fails_before_streaming(self):
        import base64
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            chat.chat_stream({"message": "x", "file": {"name": "a.png", "data": png}}, llm_stream=stream_of())
        self.assertEqual(cm.exception.code, "vision_not_configured")


class ServerStreamTests(unittest.TestCase):
    """Real HTTP against the real handler, with a fake provider: proves events leave the server one by one."""

    @classmethod
    def setUpClass(cls):
        # Set the env BEFORE importing server so a developer's backend/.env can never supply a real key here.
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update({"LLM_API_KEY": "test-key", "LLM_BASE_URL": "http://127.0.0.1:9/v1", "CHAT_RATE_LIMIT_PER_MIN": "1000",
                           "DATABASE_PATH": os.path.join(cls.tmp.name, "t.db")})
        import server
        cls.server = server
        server.db.init_db()  # the chat routes now need a logged-in user: make one and keep its session cookie
        uid = server.auth.signup({"name": "Test User", "email": "t@example.com", "password": "password1", "password_confirm": "password1"})
        cls.cookie = f"{server.auth.COOKIE}={server.auth.create_session(uid)}"
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.orig = analyzer.call_llm_stream
        cls.closed = threading.Event()

        def slow_stream(messages, cfg, tools=None):
            try:
                yield text_chunk("one ")
                time.sleep(0.6)
                yield text_chunk("two")
                time.sleep(0.6)
                yield text_chunk("", "stop")
            finally:
                cls.closed.set()
        chat.chat_stream.__defaults__ = (slow_stream, analyzer.call_llm_message)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        chat.chat_stream.__defaults__ = (cls.orig, analyzer.call_llm_message)
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("DATABASE_PATH", None)
        cls.tmp.cleanup()

    def post(self, path, body, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers.update({"Cookie": self.cookie, "X-Requested-With": "StudyGap"})
        conn.request("POST", path, json.dumps(body), headers)
        return conn, conn.getresponse()

    def test_legacy_chat_routes_need_a_login(self):
        for path in ("/api/chat", "/api/chat/stream"):
            conn, resp = self.post(path, {"message": "hi"}, auth=False)
            self.assertEqual(resp.status, 401)
            self.assertEqual(json.loads(resp.read())["error"]["code"], "unauthenticated")
            conn.close()

    def test_events_arrive_incrementally_as_ndjson(self):
        t0 = time.time()
        conn, resp = self.post("/api/chat/stream", {"message": "hi"})
        self.assertEqual(resp.status, 200)
        self.assertIn("application/x-ndjson", resp.getheader("Content-Type"))
        self.assertIsNone(resp.getheader("Content-Length"))
        arrivals = []
        while True:
            line = resp.readline()
            if not line:
                break
            arrivals.append((time.time() - t0, json.loads(line)))
        conn.close()
        types = [e["type"] for _, e in arrivals]
        self.assertEqual(types[-1], "done")
        first_token = next(t for t, e in arrivals if e["type"] == "token")
        done_at = arrivals[-1][0]
        self.assertLess(first_token, 0.5)   # the first token is shown long before the answer is finished
        self.assertGreater(done_at, 1.0)    # ...and the answer really did take ~1.2 s to finish
        self.assertEqual(arrivals[-1][1]["reply"], "one two")

    def test_validation_error_is_a_normal_http_error_not_a_stream(self):
        conn, resp = self.post("/api/chat/stream", {"message": ""})
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(resp.read())["error"]["code"], "invalid_request")
        conn.close()

    def test_provider_failure_mid_stream_becomes_an_error_event(self):
        def bad(messages, cfg, tools=None):
            yield text_chunk("partial")
            raise analyzer.AnalyzerError("llm_unavailable", "The AI provider could not be reached or timed out.", 504)
        saved = chat.chat_stream.__defaults__
        chat.chat_stream.__defaults__ = (bad, analyzer.call_llm_message)
        try:
            conn, resp = self.post("/api/chat/stream", {"message": "hi"})
            events = [json.loads(l) for l in resp.read().decode().splitlines() if l.strip()]
            conn.close()
        finally:
            chat.chat_stream.__defaults__ = saved
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "llm_unavailable")

    def test_client_disconnect_closes_the_provider_stream(self):
        self.closed.clear()
        conn, resp = self.post("/api/chat/stream", {"message": "hi"})
        resp.readline()  # status event
        while json.loads(resp.readline())["type"] != "token":
            pass
        conn.close()  # like pressing Stop
        self.assertTrue(self.closed.wait(5), "provider stream was not closed after the browser disconnected")

    def test_non_stream_chat_route_still_works(self):
        saved = analyzer.call_llm_message
        chat_default = chat.chat.__defaults__
        chat.chat.__defaults__ = (lambda messages, cfg, tools=None: {"content": "plain json answer"},)
        try:
            conn, resp = self.post("/api/chat", {"message": "hi"})
            body = json.loads(resp.read())
            conn.close()
        finally:
            chat.chat.__defaults__ = chat_default
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["reply"], "plain json answer")
        self.assertIsNotNone(saved)

    def test_rate_limit_bucket_is_shared_between_chat_routes(self):
        self.assertEqual(self.server.STREAM_ROUTES["/api/chat/stream"][3], "/api/chat")


if __name__ == "__main__":
    unittest.main()
