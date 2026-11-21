#!/usr/bin/env python3
"""Aithos Opportunity 001: fixture-only endpoint audit and offline client reports.

Uses the pinned upstream MIT Server.chat transport; adds Aithos input, quality,
statistics, provenance and publication guards. No live endpoint CLI exists.
"""
import argparse
import csv
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import platform
import re
import statistics
import sys
import threading
import time

from pilot import BENCH, BENCH_SHA256, load_upstream
from local_transport import fixture_only_http

ROOT = Path(__file__).resolve().parent
UPSTREAM_COMMIT = "fda968e7fc1996aa23c67139c755fe541c9d4be0"
NAMES = ("baseline_before", "candidate", "baseline_after")
SENTINELS = (("color", "Reply with exactly BLUE.", "BLUE"),
             ("math", "Reply with exactly 4, the sum of 2 and 2.", "4"),
             ("capital", "Reply with exactly PARIS, the capital of France.", "PARIS"))
SECRET = re.compile(r"(?i)(bearer\s+\S+|\b(?:sk|ghp|gho|github_pat)[-_][A-Za-z0-9_-]{8,}|\bAIza[A-Za-z0-9_-]{20,}|(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+)")
PATH = re.compile(r"/(?:home|Users)/[^\s]+")


def redact(value):
    if not isinstance(value, str):
        raise ValueError("metadata must be text")
    return PATH.sub("/[private]", SECRET.sub("[REDACTED]", value))[:160]


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    def verify_text(value):
        if isinstance(value, str):
            value.encode("utf-8")  # rejects lone surrogate escapes before any output directory exists
        elif isinstance(value, list):
            for item in value:
                verify_text(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                verify_text(key)
                verify_text(item)
    verify_text(result)
    return result


def finite_positive(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return float(value)


def validate_capture(data):
    if hashlib.sha256(BENCH.read_bytes()).hexdigest() != BENCH_SHA256:
        raise ValueError("vendored upstream transport hash mismatch")
    if not isinstance(data, dict) or set(data) != {"schema", "kind", "source", "metadata", "runs"}:
        raise ValueError("capture schema keys differ")
    if data["schema"] != "aithos-audit-1" or data["kind"] != "SYNTHETIC_FIXTURE_ONLY":
        raise ValueError("only synthetic fixture captures are accepted")
    source = data["source"]
    if not isinstance(source, dict) or set(source) != {"upstream_commit", "upstream_bench_sha256"}:
        raise ValueError("invalid source pin")
    if source["upstream_commit"] != UPSTREAM_COMMIT or source["upstream_bench_sha256"] != BENCH_SHA256:
        raise ValueError("upstream pin mismatch")
    metadata = data["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != {"model", "engine", "system", "workload"}:
        raise ValueError("invalid metadata fields")
    if metadata["model"] != "synthetic-fixture" or metadata["engine"] != "aithos-mock-http-v1":
        raise ValueError("live model or engine is forbidden")
    if not isinstance(metadata["system"], dict) or set(metadata["system"]) != {"os", "architecture", "python"}:
        raise ValueError("system metadata must be allowlisted")
    for value in metadata["system"].values():
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError("invalid system metadata")
    if metadata["workload"] != "three exact sentinels, twice each; temperature 0; six samples per arm":
        raise ValueError("workload differs")
    runs = data["runs"]
    if not isinstance(runs, list) or [r.get("name") if isinstance(r, dict) else None for r in runs] != list(NAMES):
        raise ValueError("expected baseline/candidate/baseline order")
    for run in runs:
        if set(run) != {"name", "samples", "quality", "simulated_delay_ms"}:
            raise ValueError("invalid run fields")
        if not isinstance(run["samples"], list) or len(run["samples"]) != 6:
            raise ValueError("six samples required per arm")
        for sample in run["samples"]:
            if not isinstance(sample, dict) or set(sample) != {"sentinel", "decode_tps", "prompt_tokens", "completion_tokens", "answer"}:
                raise ValueError("invalid sample fields")
            if sample["sentinel"] not in [s[0] for s in SENTINELS]:
                raise ValueError("unknown sentinel")
            finite_positive(sample["decode_tps"], "decode rate")
            if type(sample["prompt_tokens"]) is not int or sample["prompt_tokens"] <= 0:
                raise ValueError("missing prompt usage")
            if type(sample["completion_tokens"]) is not int or sample["completion_tokens"] <= 1:
                raise ValueError("missing completion usage")
            if not isinstance(sample["answer"], str) or len(sample["answer"]) > 160:
                raise ValueError("invalid answer")
        if [s["sentinel"] for s in run["samples"]] != [s[0] for s in SENTINELS] * 2:
            raise ValueError("sentinel panel differs")
        if run["quality"] is not True and run["quality"] is not False:
            raise ValueError("quality must be boolean")
        expected_quality = all(s["answer"] == next(item[2] for item in SENTINELS if item[0] == s["sentinel"])
                               for s in run["samples"])
        if run["quality"] != expected_quality:
            raise ValueError("quality flag disagrees with answers")
        finite_positive(run["simulated_delay_ms"], "simulated delay")
    if runs[0]["simulated_delay_ms"] != runs[2]["simulated_delay_ms"]:
        raise ValueError("control settings drift")
    return data


def summarize(data):
    validate_capture(data)
    medians = [statistics.median(s["decode_tps"] for s in r["samples"]) for r in data["runs"]]
    control_drift = abs(medians[2] - medians[0]) / medians[0]
    ratio = medians[1] / max(medians[0], medians[2])
    quality = all(r["quality"] for r in data["runs"])
    # These thresholds are a fixture demonstration, not statistical significance.
    passed = quality and control_drift <= 0.05 and ratio >= 1.05
    return {"status": "PASS_SYNTHETIC" if passed else "HELD",
            "reason": "fixture quality and control gates passed" if passed else "quality, drift or improvement gate failed",
            "medians_decode_tps": dict(zip(NAMES, [round(v, 3) for v in medians])),
            "candidate_over_faster_control": round(ratio, 3),
            "control_drift_fraction": round(control_drift, 4),
            "quality_pass": quality, "sample_count_per_arm": 6,
            "limitation": "Synthetic HTTP fixture and invented usage counts; no model or GPU performance measured."}


def sanitized_output(data):
    summary = summarize(data)
    system = {k: redact(v) for k, v in data["metadata"]["system"].items()}
    return {"schema": "aithos-client-report-1", "kind": data["kind"], "source": data["source"],
            "metadata": {"model": data["metadata"]["model"], "engine": data["metadata"]["engine"],
                         "system": system, "workload": data["metadata"]["workload"]},
            "summary": summary,
            "runs": [{"name": r["name"], "simulated_delay_ms": r["simulated_delay_ms"],
                      "quality_pass": r["quality"],
                      "decode_tps": [round(s["decode_tps"], 3) for s in r["samples"]]}
                     for r in data["runs"]]}


def render_markdown(report):
    s = report["summary"]
    m = s["medians_decode_tps"]
    rows = "\n".join(f"| {name.replace('_', ' ').title()} | {next(r['simulated_delay_ms'] for r in report['runs'] if r['name'] == name):.0f} | {m[name]:.2f} | {next(r['quality_pass'] for r in report['runs'] if r['name'] == name)} |"
                     for name in NAMES)
    return ("# Local inference audit — synthetic sample\n\n"
            "> **Demonstration only. No GPU or model benchmark was run.**\n\n"
            f"**Gate:** {s['status']} — {s['reason']}.\n\n"
            "| Arm | Simulated delay (ms) | Median client-observed decode tokens/s | Exact-answer gate |\n|---|---:|---:|---|\n" + rows + "\n\n"
            f"Candidate / faster control: **{s['candidate_over_faster_control']:.3f}×**. "
            f"Control drift: **{s['control_drift_fraction']:.2%}**. Six samples per arm.\n\n"
            f"**Scope:** {s['limitation']}\n\n"
            f"**Method:** pinned Sybil MIT bench transport `{report['source']['upstream_commit'][:12]}`; "
            "Aithos exact-answer, input and control gates. No hardware telemetry or production changes.\n\n"
            "**Next decision:** Live GPU work is out of scope for this release.\n")


def render_html(report):
    s = report["summary"]
    m = s["medians_decode_tps"]
    rows = "".join(f"<tr><th>{escape(n.replace('_', ' ').title())}</th><td>{next(r['simulated_delay_ms'] for r in report['runs'] if r['name'] == n):.0f} ms</td><td>{m[n]:.2f}</td>"
                   f"<td>{'Pass' if next(r['quality_pass'] for r in report['runs'] if r['name'] == n) else 'Fail'}</td></tr>"
                   for n in NAMES)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Aithos Local Inference Audit — Synthetic Sample</title><style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f7fb;color:#17243a}}main{{max-width:900px;margin:auto;padding:32px}}header{{background:#142c4d;color:white;padding:38px;border-radius:20px}}h1{{margin:0;font-size:2.2rem}}.tag{{display:inline-block;background:#ffdb85;color:#3c2800;border-radius:99px;padding:5px 12px;font-weight:700}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:20px 0}}.card,section{{background:white;border-radius:16px;padding:20px;box-shadow:0 5px 22px #10243a12}}.big{{font-size:1.8rem;font-weight:800;color:#0a6072}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #dce4ed}}.note{{border-left:5px solid #d99000;padding-left:14px}}footer{{color:#58677a;font-size:.9rem}}</style></head><body><main>
<header><span class="tag">SYNTHETIC DEMO · NO GPU RUN</span><h1>Local inference audit</h1><p>A reproducible example of Aithos measurement, quality and provenance gates.</p></header>
<div class="grid"><div class="card"><div>Decision</div><div class="big">{escape(s['status'])}</div></div><div class="card"><div>Candidate / faster control</div><div class="big">{s['candidate_over_faster_control']:.3f}×</div></div><div class="card"><div>Control drift</div><div class="big">{s['control_drift_fraction']:.2%}</div></div></div>
<section><h2>Baseline → candidate → baseline</h2><table><thead><tr><th>Arm</th><th>Mock delay</th><th>Median decode tokens/s</th><th>Exact answers</th></tr></thead><tbody>{rows}</tbody></table><p>Six client-observed samples per arm; the fixture changes only a simulated response delay.</p></section>
<section><h2>What this proves</h2><p>The report pipeline rejects malformed usage, wrong exact answers and drifting controls. It produces a traceable result from a pinned MIT upstream transport.</p><p class="note">{escape(s['limitation'])}</p></section>
<section><h2>Client handoff</h2><p>Model: {escape(report['metadata']['model'])} · Engine: {escape(report['metadata']['engine'])}</p><p>Host class: {escape(report['metadata']['system']['os'])} / {escape(report['metadata']['system']['architecture'])}</p><p>Next decision: live GPU work is out of scope for this release.</p></section><footer>Synthetic demonstration · Upstream commit {escape(report['source']['upstream_commit'][:12])} · MIT attribution in NOTICE.md</footer></main></body></html>'''


def write_reports(capture, out):
    report = sanitized_output(capture)
    out = Path(out)
    if out.exists():
        raise ValueError("output directory already exists")
    out.mkdir(parents=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (out / "report.md").write_text(render_markdown(report))
    (out / "report.html").write_text(render_html(report))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("arm", "sample", "simulated_delay_ms", "decode_tps", "quality_pass", "kind"))
    for run in report["runs"]:
        for index, rate in enumerate(run["decode_tps"], 1):
            writer.writerow((run["name"], index, run["simulated_delay_ms"], rate,
                             run["quality_pass"], report["kind"]))
    (out / "measurements.csv").write_text(buffer.getvalue())
    return report


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path not in ("/aithos-fixture", "/v1/models"):
            self.send_error(404)
            return
        body = ({"fixture": "aithos-mock-http-v1"} if self.path == "/aithos-fixture"
                else {"data": [{"id": "synthetic-fixture"}]})
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            body = strict_json(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = body["messages"][0]["content"]
            answer = next(item[2] for item in SENTINELS if item[1] == prompt)
            if body["model"] != "synthetic-fixture" or body["temperature"] != 0 or body["max_tokens"] != 16:
                raise ValueError("request outside fixture scope")
        except (ValueError, KeyError, IndexError, StopIteration, TypeError):
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(f'data: {json.dumps({"choices": [{"delta": {"content": answer}}]})}\n\n'.encode())
        self.wfile.flush()
        time.sleep(self.server.delay_s)
        self.wfile.write(b'data: {"usage":{"prompt_tokens":12,"completion_tokens":6}}\n\n')
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def run_demo():
    upstream = load_upstream()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    server.delay_s = 0.04
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        client = upstream.Server({"base_url": url, "model": "synthetic-fixture", "temperature": 0})
        with fixture_only_http(url):
            if client.get("/aithos-fixture") != {"fixture": "aithos-mock-http-v1"}:
                raise RuntimeError("fixture identity failed")
            runs = []
            for name, delay in zip(NAMES, (0.04, 0.02, 0.04)):
                server.delay_s = delay
                samples = []
                for sentinel, prompt, expected in SENTINELS * 2:
                    response = client.chat(prompt, 16, temperature=0, timeout=5)
                    rate = finite_positive(response["decode"], "decode rate")
                    pt = response["prompt_tokens"]
                    ct = response["completion_tokens"]
                    if type(pt) is not int or pt <= 0 or type(ct) is not int or ct <= 1:
                        raise ValueError("server usage missing or malformed")
                    samples.append({"sentinel": sentinel, "decode_tps": round(rate, 3),
                                    "prompt_tokens": pt, "completion_tokens": ct,
                                    "answer": response["text"]})
                runs.append({"name": name, "samples": samples,
                             "quality": all(s["answer"] == next(x[2] for x in SENTINELS if x[0] == s["sentinel"])
                                            for s in samples), "simulated_delay_ms": delay * 1000})
        capture = {"schema": "aithos-audit-1", "kind": "SYNTHETIC_FIXTURE_ONLY",
                   "source": {"upstream_commit": UPSTREAM_COMMIT, "upstream_bench_sha256": BENCH_SHA256},
                   "metadata": {"model": "synthetic-fixture", "engine": "aithos-mock-http-v1",
                                "system": {"os": platform.system(), "architecture": platform.machine(),
                                           "python": platform.python_version()},
                                "workload": "three exact sentinels, twice each; temperature 0; six samples per arm"},
                   "runs": runs}
        return validate_capture(capture)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def read_capture(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("capture must be a regular file at most 1 MiB")
    return validate_capture(strict_json(path.read_text(encoding="utf-8")))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aithos fixture-only local inference audit")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="measure built-in loopback fixture and render client reports")
    demo.add_argument("--out", required=True)
    render = sub.add_parser("render", help="validate an offline synthetic capture and render reports")
    render.add_argument("--input", required=True)
    render.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            if Path(args.out).exists():
                raise ValueError("output directory already exists")
            capture = run_demo()
            out = Path(args.out)
            out.mkdir(parents=True)
            (out / "capture.json").write_text(json.dumps(capture, indent=2, allow_nan=False) + "\n")
            # write_reports refuses an existing directory; use a dedicated report child.
            report = write_reports(capture, out / "client-report")
        else:
            report = write_reports(read_capture(args.input), args.out)
        print(json.dumps({"status": report["summary"]["status"], "out": str(Path(args.out).resolve())}))
        return 0 if report["summary"]["status"] == "PASS_SYNTHETIC" else 2
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError, UnicodeError) as exc:
        print(f"audit refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
