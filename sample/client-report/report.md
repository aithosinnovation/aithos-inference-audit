# Local inference audit — synthetic sample

> **Demonstration only. No GPU or model benchmark was run.**

**Gate:** PASS_SYNTHETIC — fixture quality and control gates passed.

| Arm | Simulated delay (ms) | Median client-observed decode tokens/s | Exact-answer gate |
|---|---:|---:|---|
| Baseline Before | 40 | 124.28 | True |
| Candidate | 20 | 246.76 | True |
| Baseline After | 40 | 124.33 | True |

Candidate / faster control: **1.985×**. Control drift: **0.04%**. Six samples per arm.

**Scope:** Synthetic HTTP fixture and invented usage counts; no model or GPU performance measured.

**Method:** pinned Sybil MIT bench transport `fda968e7fc19`; Aithos exact-answer, input and control gates. No hardware telemetry or production changes.

**Next decision:** Live GPU work is out of scope for this release.
