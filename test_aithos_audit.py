import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

import aithos_audit as audit
import pilot


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = audit.run_demo()

    def test_demo_quality_and_statistics(self):
        summary = audit.summarize(self.capture)
        self.assertEqual(summary["status"], "PASS_SYNTHETIC")
        self.assertEqual(summary["sample_count_per_arm"], 6)
        self.assertGreater(summary["candidate_over_faster_control"], 1.3)
        self.assertLess(summary["control_drift_fraction"], 0.05)

    def test_quality_failure_holds_claim(self):
        capture = copy.deepcopy(self.capture)
        capture["runs"][1]["samples"][0]["answer"] = "WRONG"
        capture["runs"][1]["quality"] = False
        summary = audit.summarize(capture)
        self.assertEqual(summary["status"], "HELD")
        self.assertFalse(summary["quality_pass"])

    def test_missing_usage_and_bad_numbers_fail_closed(self):
        for field, value in (("completion_tokens", None), ("prompt_tokens", 0),
                             ("decode_tps", float("nan")), ("decode_tps", -3)):
            capture = copy.deepcopy(self.capture)
            capture["runs"][0]["samples"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                audit.validate_capture(capture)
        with self.assertRaises(ValueError):
            audit.strict_json('{"a":1,"a":2}')
        with self.assertRaises(ValueError):
            audit.strict_json('{"a":NaN}')
        with self.assertRaises(UnicodeEncodeError):
            audit.strict_json('{"a":"\\ud800"}')

    def test_control_drift_and_weak_gain_hold(self):
        capture = copy.deepcopy(self.capture)
        for sample in capture["runs"][2]["samples"]:
            sample["decode_tps"] *= 1.2
        self.assertEqual(audit.summarize(capture)["status"], "HELD")
        capture = copy.deepcopy(self.capture)
        for sample in capture["runs"][1]["samples"]:
            sample["decode_tps"] = capture["runs"][0]["samples"][0]["decode_tps"]
        self.assertEqual(audit.summarize(capture)["status"], "HELD")

    def test_exact_median_ratio_and_live_label_rejection(self):
        capture = copy.deepcopy(self.capture)
        for run, rate in zip(capture["runs"], (100.0, 110.0, 100.0)):
            for sample in run["samples"]:
                sample["decode_tps"] = rate
        summary = audit.summarize(capture)
        self.assertEqual(summary["medians_decode_tps"],
                         {"baseline_before": 100.0, "candidate": 110.0, "baseline_after": 100.0})
        self.assertEqual(summary["candidate_over_faster_control"], 1.1)
        self.assertEqual(summary["control_drift_fraction"], 0.0)
        capture["metadata"]["model"] = "real-model"
        with self.assertRaises(ValueError):
            audit.validate_capture(capture)

    def test_input_scope_and_redaction(self):
        capture = copy.deepcopy(self.capture)
        capture["metadata"]["system"]["hostname"] = "secret-host"
        with self.assertRaises(ValueError):
            audit.validate_capture(capture)
        capture = copy.deepcopy(self.capture)
        private_path = "/home/" + "example-user/private"
        capture["metadata"]["system"]["os"] = 'Linux <script>alert(1)</script> Bearer abc123 ' + private_path
        report = audit.sanitized_output(capture)
        html = audit.render_html(report)
        self.assertNotIn("abc123", json.dumps(report))
        self.assertNotIn(private_path, json.dumps(report))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_reports_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "client"
            audit.write_reports(self.capture, output)
            for name in ("report.json", "report.md", "report.html", "measurements.csv"):
                self.assertGreater((output / name).stat().st_size, 0)
            self.assertEqual(len((output / "measurements.csv").read_text().splitlines()), 19)
            with self.assertRaises(ValueError):
                audit.write_reports(self.capture, output)

    def test_capture_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "capture.json"
            target.write_text(json.dumps(self.capture))
            link = Path(temp) / "linked.json"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                audit.read_capture(link)

    def test_bad_stream_fails_instead_of_reporting_success(self):
        class Malformed(audit.MockHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"data: {broken-json}\n\n")
                self.wfile.flush()
        with patch.object(audit, "MockHandler", Malformed):
            with self.assertRaises(ValueError):
                audit.run_demo()

    def test_missing_stream_usage_fails_closed(self):
        class NoUsage(audit.MockHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"BLUE"}}]}\n\n')
                self.wfile.write(b'data: [DONE]\n\n')
                self.wfile.flush()
        with patch.object(audit, "MockHandler", NoUsage):
            with self.assertRaises(ValueError):
                audit.run_demo()

    def test_tampered_upstream_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            altered = Path(temp) / "bench.py"
            altered.write_bytes(pilot.BENCH.read_bytes() + b"\n# tampered\n")
            with patch.object(pilot, "BENCH", altered):
                with self.assertRaises(RuntimeError):
                    pilot.load_upstream()
            with patch.object(audit, "BENCH", altered):
                with self.assertRaises(ValueError):
                    audit.validate_capture(self.capture)

    def test_malicious_recording_proxy_gets_no_fixture_requests(self):
        hits = []
        class RecordingProxy(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                hits.append(self.requestline)
                self.send_error(502)
            do_POST = do_GET
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), RecordingProxy)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        inherited = {name: proxy_url for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY",
                                                 "https_proxy", "ALL_PROXY", "all_proxy")}
        inherited.update(NO_PROXY="", no_proxy="")
        try:
            with patch.dict(os.environ, inherited):
                self.assertEqual(audit.summarize(audit.run_demo())["status"], "PASS_SYNTHETIC")
                with tempfile.TemporaryDirectory() as temp:
                    self.assertEqual(pilot.run_pilot(Path(temp))["kind"], "SYNTHETIC_FIXTURE_ONLY")
                class Redirect(audit.MockHandler):
                    def do_POST(self):
                        self.send_response(307)
                        self.send_header("Location", proxy_url)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                with patch.object(audit, "MockHandler", Redirect):
                    with self.assertRaises(urllib.error.HTTPError):
                        audit.run_demo()
            self.assertEqual(hits, [], f"fixture traffic escaped through proxy: {hits}")
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
