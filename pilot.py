#!/usr/bin/env python3
"""Offline, mock-only A/B/A pilot using the pinned upstream benchmark module."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from local_transport import fixture_only_http

ROOT = Path(__file__).resolve().parent
BENCH = ROOT / "vendor/inference-optimisation/scripts/bench.py"
BENCH_SHA256 = "acca9912c0013afa2bdfd47acbdc6417e079ae5b29596732029b897b3ba872ef"


def load_upstream():
    if hashlib.sha256(BENCH.read_bytes()).hexdigest() != BENCH_SHA256:
        raise RuntimeError("pinned upstream bench.py hash mismatch")
    spec = importlib.util.spec_from_file_location("pinned_upstream_bench", BENCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_config(cfg, fixture_url):
    allowed = {"name", "base_url", "model", "concurrency", "prefill_lengths", "temperature", "memory"}
    if set(cfg) != allowed or cfg["base_url"] != fixture_url or not fixture_url.startswith("http://127.0.0.1:"):
        raise ValueError("only the local fixture and exact safe config keys are allowed")
    if cfg["model"] != "synthetic-fixture" or cfg["concurrency"] != 1 or cfg["prefill_lengths"] != []:
        raise ValueError("live model, concurrent and long-context runs are forbidden")
    if cfg["temperature"] != 0 or cfg["memory"] != {}:
        raise ValueError("fixture settings changed")
    if cfg["name"] not in ("control-before", "candidate", "control-after"):
        raise ValueError("unexpected run name")


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        data = json.dumps({"data": [{"id": "synthetic-fixture"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("model") != "synthetic-fixture" or not body.get("stream"):
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n')
        self.wfile.flush()
        time.sleep(self.server.delay_s)
        self.wfile.write(b'data: {"choices":[{"delta":{"content":" done"}}]}\n\n')
        if self.server.include_usage:
            self.wfile.write(b'data: {"usage":{"prompt_tokens":12,"completion_tokens":6}}\n\n')
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def validate_result(result):
    if not result["ok"] or result["flags"] or len(result["c1_all"]) != 6:
        raise RuntimeError("upstream result incomplete or flagged")
    if not all(isinstance(x, (int, float)) and x > 0 for x in result["c1_all"]):
        raise RuntimeError("invalid server-usage-derived decode samples")


def run_pilot(output_dir=ROOT / "results"):
    bench = load_upstream()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.delay_s, server.include_usage = 0.04, True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fixture_url = f"http://127.0.0.1:{server.server_port}"
    rows = []
    prior = Path.cwd()
    try:
        os.chdir(output_dir)
        (output_dir / "ledger.jsonl").unlink(missing_ok=True)
        with fixture_only_http(fixture_url):
            for name, delay in (("control-before", 0.04), ("candidate", 0.02), ("control-after", 0.04)):
                server.delay_s = delay  # the single simulated lever; identical request/config otherwise
                cfg = {"name": name, "base_url": fixture_url, "model": "synthetic-fixture",
                       "concurrency": 1, "prefill_lengths": [], "temperature": 0, "memory": {}}
                validate_config(cfg, fixture_url)
                path = output_dir / f"{name}.json"
                path.write_text(json.dumps(cfg, indent=2) + "\n")
                result = bench.run(str(path), skip_cn=True, reps=6)
                validate_result(result)
                rows.append({"name": name, "simulated_decode_delay_s": delay,
                             "median_decode_tokens_per_s": result["c1_decode"],
                             "samples": result["c1_all"], "flags": result["flags"]})
        candidate = rows[1]["median_decode_tokens_per_s"]
        controls = [rows[0]["median_decode_tokens_per_s"], rows[2]["median_decode_tokens_per_s"]]
        report = {"kind": "SYNTHETIC_FIXTURE_ONLY", "upstream_bench_sha256": BENCH_SHA256,
                  "token_source": "fixture server usage.completion_tokens=6; two SSE content chunks",
                  "runs": rows, "candidate_over_faster_control": round(candidate / max(controls), 3),
                  "control_drift_fraction": round(abs(controls[1] - controls[0]) / controls[0], 3)}
        (output_dir / "mock-results.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        os.chdir(prior)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    print(json.dumps(run_pilot(), indent=2))
