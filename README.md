# Aithos Local Inference Audit

Small, standard-library-only CLI for a **synthetic local endpoint audit** and client-ready reports. It reuses the `Server.chat` streaming transport from the pinned [Sybil inference optimisation skill](https://github.com/sybil-solutions/inference-optimisation-skill/tree/fda968e7fc1996aa23c67139c755fe541c9d4be0) under MIT; [NOTICE.md](NOTICE.md) retains attribution and the vendor license. Aithos adds strict capture validation, exact-answer quality gates, interleaved baseline/candidate/baseline controls, simple statistics, sanitized metadata and four report formats.

**Synthetic only:** The CLI starts only its built-in mock server on a random `127.0.0.1` port. It has no live endpoint, GPU, driver, service, download, purchase, cloud or background-loop command. The displayed token rates are synthetic fixture timings with invented usage counts; they are not hardware performance evidence. This release performs no live GPU work.

**Upstream execution warning:** The vendored `bench.py` is included byte-for-byte for attribution and reuse of its streaming client. Run this package through `aithos_audit.py` or the fixture-only `pilot.py`. Do not run the upstream `mem.sh`, `queue.sh`, or Docker examples: they include privileged memory probes, detached jobs and service stop commands and are outside this release's safety boundary.

## Install and run

Python 3.10+ is enough. No package installation or API key is needed.

```sh
python3 aithos_audit.py demo --out my-sample
python3 aithos_audit.py render --input my-sample/capture.json --out my-replay
python3 -m unittest -q test_aithos_audit.py test_pilot.py
```

The demo writes `capture.json` plus `client-report/report.json`, `measurements.csv`, `report.md`, and `report.html`. Open the HTML file in a browser to see the [checked-in sample](sample/client-report/report.html). `render` accepts a validated, synthetic capture only. Both commands refuse an existing output directory; use a new path for each run. A nonpassing quality or control gate exits with code 2 and marks the report `HELD`. Invalid inputs are refused before report creation.

The committed [sample capture](sample/capture.json) and [reports](sample/client-report/report.md) are a dry-run dataset. Six requests per arm repeat three exact-answer sentinels at temperature zero. The sole simulated lever is a 40 ms → 20 ms → 40 ms delay after the first streamed content. The candidate is compared with the *faster* neighboring control and passes only if all answers match exactly, control drift is at most 5%, and the candidate ratio is at least 1.05. These thresholds demonstrate enforcement, not statistical significance or a production service-level objective.

System metadata is restricted to OS, architecture and Python version. No hostname, username, environment variables, credentials, device IDs or raw responses enter client reports. The raw synthetic capture contains only fixture answers. User-supplied capture input is size-limited, rejects symlinks, duplicate keys, nonfinite numbers, unknown fields and live-model labels. HTML escapes user-controlled text; metadata is redacted and truncated. This is a narrow sample pipeline, not a general secret scanner.

## Why this remains small

Do not treat the sample's `PASS_SYNTHETIC` result as a GPU qualification.

The earlier `pilot.py` and `test_pilot.py` remain as the pinned upstream dry-run proof. The new CLI uses `aithos_audit.py`; neither path alters the vendored upstream code.

