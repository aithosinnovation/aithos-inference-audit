import tempfile
import unittest
from pathlib import Path

import pilot


class PilotTests(unittest.TestCase):
    def test_pinned_upstream_and_mock_measurement(self):
        self.assertTrue(hasattr(pilot.load_upstream(), "run"))
        with tempfile.TemporaryDirectory() as directory:
            report = pilot.run_pilot(Path(directory))
            self.assertEqual(report["kind"], "SYNTHETIC_FIXTURE_ONLY")
            self.assertEqual([x["name"] for x in report["runs"]],
                             ["control-before", "candidate", "control-after"])
            self.assertGreater(report["candidate_over_faster_control"], 1.3)
            self.assertLess(report["candidate_over_faster_control"], 2.7)
            self.assertLess(report["control_drift_fraction"], 0.25)
            self.assertTrue((Path(directory) / "ledger.jsonl").is_file())

    def test_config_rejects_live_target_or_commands(self):
        cfg = {"name": "control-before", "base_url": "http://127.0.0.1:12345",
               "model": "synthetic-fixture", "concurrency": 1,
               "prefill_lengths": [], "temperature": 0, "memory": {}}
        pilot.validate_config(cfg, cfg["base_url"])
        for change in ({"base_url": "http://192.0.2.1:8000"},
                       {"launch": ["sh", "untrusted.sh"]}, {"stop": ["pkill", "server"]},
                       {"model": "real-model"}, {"concurrency": 4},
                       {"prefill_lengths": [4096]}):
            bad = dict(cfg, **change)
            with self.assertRaises(ValueError):
                pilot.validate_config(bad, cfg["base_url"])

    def test_missing_usage_cannot_be_validated(self):
        with self.assertRaises(RuntimeError):
            pilot.validate_result({"ok": True, "flags": [], "c1_all": []})
        with self.assertRaises(RuntimeError):
            pilot.validate_result({"ok": True, "flags": ["LOOP"], "c1_all": [100.0] * 6})


if __name__ == "__main__":
    unittest.main()
