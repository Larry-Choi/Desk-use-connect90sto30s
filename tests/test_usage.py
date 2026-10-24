import time
import unittest
from datetime import datetime, timezone

from desk.usage import claude_bars, codex_from_limits, countdown, parse_agy


class UsageTests(unittest.TestCase):
    def test_countdown_stops_at_zero(self):
        now = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(countdown("2026-10-24T05:41:00Z", now), "2h 41m")
        self.assertEqual(countdown("2026-10-24T02:00:00Z", now), "0m")
        self.assertEqual(countdown("2026-10-27T12:00:00Z", now), "3d 9h")

    def test_agy_shows_the_tightest_model(self):
        item = parse_agy(
            {
                "status": "SUCCESS",
                "command": {
                    "data": {
                        "groups": [
                            {
                                "name": "Gemini",
                                "buckets": [
                                    {"window": "5h", "remaining_fraction": 0.68, "reset_time": "2026-10-24T06:00:00Z"},
                                    {"window": "weekly", "remaining_fraction": 0.9, "reset_time": "2026-10-30T00:00:00Z"},
                                ],
                            },
                            {
                                "name": "Claude",
                                "buckets": [
                                    {"window": "5h", "remaining_fraction": 0.2, "reset_time": "2026-10-24T08:00:00Z"},
                                ],
                            },
                        ]
                    }
                },
            }
        )
        self.assertEqual(item["bars"][0]["hint"], "Claude")
        self.assertEqual(item["bars"][0]["used"], 80)
        self.assertEqual(item["bars"][1]["hint"], "Gemini")
        self.assertEqual(item["bars"][1]["used"], 10)
        self.assertIn("Gemini 32%", item["lines"])

    def test_claude_needs_a_real_percent(self):
        self.assertIsNone(claude_bars({}))
        bars = claude_bars({"five_hour": {"utilization": 41, "resets_at": "2026-10-24T06:00:00Z"}})
        self.assertEqual(bars[0]["used"], 41)

    def test_codex_uses_the_official_used_percent(self):
        item = codex_from_limits(
            "plus",
            {
                "primary": {"usedPercent": 37, "resetsAt": 1_800_000_000, "windowDurationMins": 300},
                "secondary": {"usedPercent": 12, "resetsAt": 1_800_000_000, "windowDurationMins": 10080},
            },
        )
        self.assertEqual([bar["label"] for bar in item["bars"]], ["5H", "WK"])
        self.assertEqual(item["bars"][0]["used"], 37)
        self.assertEqual(item["bars"][1]["used"], 12)
        self.assertNotIn("email", item)
        weekly = codex_from_limits("pro", {"primary": {"usedPercent": 4, "windowDurationMins": 10080, "resetsAt": 1_800_000_000}})
        self.assertEqual([bar["label"] for bar in weekly["bars"]], ["WK"])
        self.assertEqual(weekly["bars"][0]["used"], 4)

    def test_layout_hides_and_reorders(self):
        from desk.usage import apply_layout

        live = [
            {"id": "codex", "name": "Codex", "status": "ready", "detail": "", "bars": [], "lines": []},
            {"id": "claude", "name": "Claude", "status": "ready", "detail": "", "bars": [], "lines": []},
        ]
        shown = apply_layout(
            live,
            {
                "usageOrder": ["claude", "codex"],
                "usageHidden": ["codex"],
                "usageCustom": [
                    {"id": "custom-1", "name": "메모", "session": 20, "week": None, "sessionLeft": "1h", "weekLeft": ""}
                ],
            },
        )
        self.assertEqual([item["id"] for item in shown], ["claude", "custom-1"])
        self.assertEqual(shown[1]["detail"], "직접 입력")
        self.assertEqual(shown[1]["bars"][0]["used"], 20)

    def test_cpu_percent_uses_the_busy_delta(self):
        from desk.meters import cpu_used, enabled_meter_ids

        self.assertIsNone(cpu_used(None, (10, 100)))
        self.assertEqual(cpu_used((10, 100), (20, 200)), 90)
        self.assertEqual(enabled_meter_ids({}), ["meter-cpu", "meter-ram"])
        self.assertEqual(
            enabled_meter_ids({"usageMetersConfigured": True, "usageMeters": ["meter-disk", "meter-cpu", "nope"]}),
            ["meter-disk", "meter-cpu"],
        )

    def test_layout_places_enabled_meters(self):
        from desk.usage import apply_layout

        shown = apply_layout(
            [{"id": "codex", "name": "Codex", "status": "ready", "detail": "", "bars": [], "lines": []}],
            {"usageOrder": ["meter-ram", "codex"], "usageHidden": [], "usageCustom": []},
            [{"id": "meter-ram", "name": "RAM", "status": "ready", "detail": "", "bars": [{"label": "", "used": 40, "left": "8/16GB"}], "lines": []}],
        )
        self.assertEqual([item["id"] for item in shown], ["meter-ram", "codex"])

    def test_this_pc_reads_memory(self):
        from desk.meters import MachineMeters

        meter = MachineMeters()
        first = {item["id"]: item for item in meter.sample(["meter-cpu", "meter-ram", "meter-disk"])}
        self.assertEqual(first["meter-cpu"]["detail"], "측정 중")
        time.sleep(0.2)
        second = {item["id"]: item for item in meter.sample(["meter-cpu", "meter-ram", "meter-disk"])}
        self.assertGreaterEqual(second["meter-cpu"]["bars"][0]["used"], 0)
        self.assertLessEqual(second["meter-ram"]["bars"][0]["used"], 100)
        self.assertIn("GB", second["meter-ram"]["bars"][0]["left"])
        self.assertIn("GB", second["meter-disk"]["bars"][0]["left"])


if __name__ == "__main__":
    unittest.main()
