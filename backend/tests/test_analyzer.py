"""Run:  python -m unittest discover -s backend/tests -v   (from the project root)
Uses a fake LLM and a local mock provider. No real AI API is called by these tests."""
import copy
import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyzer  # noqa: E402

PAYLOAD = {
    "subject": "Data Structures", "unit": 1, "year": 2, "semester": 1, "branch": "Computer Science (CSE)",
    "rules": {"weak_below": 0.6, "min_answered": 2},
    "attempt": {
        "number": 1, "type": "diagnostic", "total": 10, "answered": 10, "score": 8, "accuracy": 80,
        "topics": {
            "Complexity": {"correct": 3, "answered": 3, "total": 3, "classification": "strong"},
            "Arrays": {"correct": 3, "answered": 3, "total": 3, "classification": "strong"},
            "Linked Lists": {"correct": 1, "answered": 3, "total": 3, "classification": "weak"},
            "Stacks": {"correct": 1, "answered": 1, "total": 1, "classification": "insufficient"},
        },
        "misses": [{"topic": "Linked Lists", "difficulty": "easy", "question": "Time to insert at the head?", "chosen": "O(n)", "correct_answer": "O(1)", "explanation": "Head insertion needs no traversal."}],
    },
    "previous": [],
}
GOOD_AI = {
    "overall": {"summary": "Solid result with one gap.", "key_gaps": ["Linked list operations"], "strengths": ["Arrays"]},
    "topics": [
        {"topic": "Linked Lists", "gap": "Confused insertion cost", "evidence": "Chose O(n) for head insertion", "why_it_matters": "", "priority": "High", "confidence": "suspected"},
        {"topic": "Arrays", "gap": "None", "evidence": "3 of 3 correct", "why_it_matters": "Base structure", "priority": "Maintain", "confidence": "observed"},
        {"topic": "Made Up Topic", "gap": "x", "evidence": "x", "why_it_matters": "x", "priority": "High", "confidence": "observed"},
    ],
    "tips": {"revise_first": ["Linked lists"], "understand_before_moving_on": ["Pointer basics"], "mistakes_to_review": ["Head insertion"], "exercises": ["Draw a list"],
             "revision_schedule": [{"when": "Day 1", "focus": "Linked lists"}], "next_assessment": "In 3 days"},
}


def set_key(on):
    if on:
        os.environ["LLM_API_KEY"] = "test-key"
    else:
        os.environ.pop("LLM_API_KEY", None)


class ValidationTests(unittest.TestCase):
    def check(self, mutate, code):
        p = copy.deepcopy(PAYLOAD)
        mutate(p)
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            analyzer.validate_payload(p, analyzer._load("syllabus.json"))
        self.assertEqual(cm.exception.code, code)

    def test_valid(self):
        analyzer.validate_payload(copy.deepcopy(PAYLOAD), analyzer._load("syllabus.json"))

    def test_wrong_accuracy(self):
        self.check(lambda p: p["attempt"].update(accuracy=95), "inconsistent_data")

    def test_totals_mismatch(self):
        self.check(lambda p: p["attempt"]["topics"]["Arrays"].update(correct=2, classification="strong"), "inconsistent_data")

    def test_wrong_classification(self):
        self.check(lambda p: p["attempt"]["topics"]["Stacks"].update(classification="weak"), "inconsistent_data")

    def test_topic_outside_syllabus(self):
        self.check(lambda p: p["attempt"]["topics"].update({"Trees": {"correct": 0, "answered": 0, "total": 1, "classification": "insufficient"}}), "out_of_syllabus")

    def test_unknown_subject_or_unit(self):
        self.check(lambda p: p.update(subject="Thermodynamics"), "out_of_syllabus")
        self.check(lambda p: p.update(unit=9), "out_of_syllabus")


class ReportTests(unittest.TestCase):
    def tearDown(self):
        set_key(False)

    def test_demo_mode_without_key(self):
        set_key(False)
        r = analyzer.analyze(copy.deepcopy(PAYLOAD))
        self.assertEqual(r["mode"], "demo")
        self.assertIn("8 of 10", r["report"]["overall"]["summary"])
        self.assertEqual(r["report"]["topics"][0]["priority"], "High")

    def test_ai_report_validated_and_numbers_from_data(self):
        set_key(True)
        r = analyzer.analyze(copy.deepcopy(PAYLOAD), llm=lambda m, c: "Here you go:\n" + json.dumps(GOOD_AI))
        self.assertEqual(r["mode"], "ai")
        names = [t["topic"] for t in r["report"]["topics"]]
        self.assertNotIn("Made Up Topic", names)
        ll = next(t for t in r["report"]["topics"] if t["topic"] == "Linked Lists")
        self.assertEqual(ll["observed"], "1 of 3 answered correctly (33%).")  # from data, not from the model
        self.assertTrue(ll["why_it_matters"])  # filled from syllabus when the model leaves it empty

    def test_invalid_ai_output_retries_then_fails_with_resources(self):
        set_key(True)
        calls = []
        def bad(m, c):
            calls.append(1)
            return "not json"
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            analyzer.analyze(copy.deepcopy(PAYLOAD), llm=bad)
        self.assertEqual(cm.exception.code, "invalid_ai_response")
        self.assertEqual(len(calls), 2)
        self.assertIn("not json", cm.exception.message)  # the model's own words are shown to help debugging
        with self.assertRaises(analyzer.AnalyzerError) as cm2:
            analyzer.analyze(copy.deepcopy(PAYLOAD), llm=lambda m, c: "")
        self.assertIn("empty", cm2.exception.message)
        self.assertTrue(cm.exception.resources)

    def test_bad_priority_rejected(self):
        set_key(True)
        bad = copy.deepcopy(GOOD_AI)
        bad["topics"][0]["priority"] = "Urgent"
        with self.assertRaises(analyzer.AnalyzerError):
            analyzer.analyze(copy.deepcopy(PAYLOAD), llm=lambda m, c: json.dumps(bad))

    def test_provider_down_is_not_retried(self):
        set_key(True)
        calls = []
        def down(m, c):
            calls.append(1)
            raise analyzer.AnalyzerError("llm_unavailable", "down", 504)
        with self.assertRaises(analyzer.AnalyzerError) as cm:
            analyzer.analyze(copy.deepcopy(PAYLOAD), llm=down)
        self.assertEqual((cm.exception.code, len(calls)), ("llm_unavailable", 1))


class ResourceTests(unittest.TestCase):
    def test_only_catalogue_entries_and_honest_gaps(self):
        clean = analyzer.validate_payload(copy.deepcopy(PAYLOAD), analyzer._load("syllabus.json"))
        db = analyzer._load("resources.json")
        urls = {r["url"] for r in db["resources"]}
        res = analyzer.recommend_resources(clean)
        self.assertEqual([r["topic"] for r in res], ["Linked Lists", "Stacks"])  # weak first, then needs more data
        for entry in res:
            for item in entry["items"]:
                self.assertIn(item["url"], urls)
                self.assertIsNone(item["verified_on"])  # nothing is claimed verified
        empty = analyzer.recommend_resources(clean, db={"resources": [], "search_suggestions": {}})
        self.assertIn("No verified resource", empty[0]["note"])
        self.assertTrue(empty[0]["search_suggestion"])


class MockProvider(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        assert self.headers["Authorization"] == "Bearer test-key"
        assert self.headers["User-Agent"].startswith("StudyGapAnalyzer")  # not urllib default
        body = json.dumps({"choices": [{"message": {"content": json.dumps(GOOD_AI)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HttpTests(unittest.TestCase):
    def test_server_and_real_llm_call_path_with_mock_provider(self):
        provider = HTTPServer(("127.0.0.1", 0), MockProvider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        os.environ.update(LLM_API_KEY="test-key", LLM_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1")
        import server
        app = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{app.server_port}"
        try:
            health = json.load(urllib.request.urlopen(base + "/api/health"))
            self.assertTrue(health["ai_configured"])
            req = urllib.request.Request(base + "/api/study-gap-report", data=json.dumps(PAYLOAD).encode(), headers={"Content-Type": "application/json"}, method="POST")
            data = json.load(urllib.request.urlopen(req))
            self.assertEqual(data["mode"], "ai")
            bad = copy.deepcopy(PAYLOAD); bad["attempt"]["score"] = 9
            req = urllib.request.Request(base + "/api/study-gap-report", data=json.dumps(bad).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 400)
            self.assertTrue(urllib.request.urlopen(base + "/").read().startswith(b"<!DOCTYPE html>"))  # serves frontend
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/../backend/server.py")
            self.assertEqual(cm.exception.code, 404)
        finally:
            app.shutdown(); provider.shutdown()
            set_key(False)


class RejectingProvider(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = b'{"error": {"message": "API key not valid. test-key is wrong"}}'
        self.send_response(400)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProviderErrorTests(unittest.TestCase):
    def test_provider_message_shown_and_key_redacted(self):
        provider = HTTPServer(("127.0.0.1", 0), RejectingProvider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        os.environ.update(LLM_API_KEY="test-key", LLM_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1")
        try:
            with self.assertRaises(analyzer.AnalyzerError) as cm:
                analyzer.analyze(copy.deepcopy(PAYLOAD))
            self.assertEqual(cm.exception.code, "llm_http_error")
            self.assertIn("API key not valid", cm.exception.message)
            self.assertNotIn("test-key", cm.exception.message)  # secret never leaves the server
        finally:
            provider.shutdown()
            set_key(False)


if __name__ == "__main__":
    unittest.main()
