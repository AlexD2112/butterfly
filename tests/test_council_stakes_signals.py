import unittest

import council


class TestDetectStakesSignals(unittest.TestCase):
    """Formerly should_convene_council - a yes/no gate before the council ran on every turn
    unconditionally (2026-08-20). Renamed to detect_stakes_signals and repurposed as a pure signal
    detector feeding build_context_summary's router input - these tests cover the same signal
    detection logic, just against the new list-only return shape."""

    def test_routine_turn_has_no_signals(self):
        debug_events = [
            {"type": "check", "attribute": "oration", "degree": "success"},
            {"type": "state_update", "changes": [
                {"kind": "resource", "key": "mass_influence", "before_label": "Moderate", "after_label": "Moderate"},
            ]},
        ]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertEqual(reasons, [])

    def test_critical_success_is_a_signal(self):
        debug_events = [{"type": "check", "attribute": "oration", "degree": "critical_success"}]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertIn("critical success on oration", reasons[0])

    def test_critical_failure_is_a_signal(self):
        debug_events = [{"type": "check", "attribute": "resolve", "degree": "critical_failure"}]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertEqual(len(reasons), 1)

    def test_canon_event_firing_is_a_signal(self):
        debug_events = [{"type": "canon_event", "key": "october_revolution", "status": "fired"}]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertIn("october_revolution", reasons[0])

    def test_resource_crossing_into_extreme_band_is_a_signal(self):
        debug_events = [{"type": "state_update", "changes": [
            {"kind": "resource", "key": "personal_safety", "before_label": "Low", "after_label": "Suspect/Negative"},
        ]}]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertIn("Suspect/Negative", reasons[0])

    def test_ordinary_band_crossing_between_middle_bands_is_still_a_signal(self):
        debug_events = [{"type": "state_update", "changes": [
            {"kind": "resource", "key": "party_standing", "before_label": "Moderate", "after_label": "Rising/New"},
        ]}]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertIn("shifted from Moderate to Rising/New", reasons[0])

    def test_liability_gained_is_a_signal(self):
        debug_events = [{"type": "state_update", "changes": [
            {"kind": "liability_gained", "id": "arrogance", "name": "Arrogance"},
        ]}]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertEqual(len(reasons), 1)

    def test_multiple_signals_all_collected(self):
        debug_events = [
            {"type": "check", "attribute": "cunning", "degree": "critical_failure"},
            {"type": "canon_event", "key": "civil_war_begins", "status": "fired"},
        ]
        reasons = council.detect_stakes_signals(debug_events)
        self.assertEqual(len(reasons), 2)

    def test_empty_debug_events_has_no_signals(self):
        self.assertEqual(council.detect_stakes_signals([]), [])


class TestBuildContextSummary(unittest.TestCase):
    def test_includes_turn_number_and_date(self):
        state = {"turn_number": 5, "date": "1917-10-01", "campaign_log_summary": []}
        summary = council.build_context_summary(state, [])
        self.assertIn("Turn 5", summary)
        self.assertIn("1917-10-01", summary)

    def test_includes_last_campaign_log_entry(self):
        state = {"turn_number": 5, "date": "1917-10-01",
                  "campaign_log_summary": [{"turn": 4, "summary": "Trotsky addressed the Soviet."}]}
        summary = council.build_context_summary(state, [])
        self.assertIn("Trotsky addressed the Soviet.", summary)

    def test_includes_stakes_signals_when_present(self):
        state = {"turn_number": 5, "date": "1917-10-01", "campaign_log_summary": []}
        debug_events = [{"type": "check", "attribute": "oration", "degree": "critical_success"}]
        summary = council.build_context_summary(state, debug_events)
        self.assertIn("critical success on oration", summary)

    def test_says_routine_when_no_signals(self):
        state = {"turn_number": 5, "date": "1917-10-01", "campaign_log_summary": []}
        summary = council.build_context_summary(state, [])
        self.assertIn("routine", summary)


if __name__ == "__main__":
    unittest.main()
