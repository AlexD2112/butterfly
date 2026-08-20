import unittest

import council


class TestShouldConveneCouncil(unittest.TestCase):
    def test_routine_turn_does_not_convene(self):
        debug_events = [
            {"type": "check", "attribute": "oration", "degree": "success"},
            {"type": "state_update", "changes": [
                {"kind": "resource", "key": "mass_influence", "before_label": "Moderate", "after_label": "Moderate"},
            ]},
        ]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertFalse(convened)
        self.assertEqual(reasons, [])

    def test_critical_success_convenes(self):
        debug_events = [{"type": "check", "attribute": "oration", "degree": "critical_success"}]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)
        self.assertIn("critical success on oration", reasons[0])

    def test_critical_failure_convenes(self):
        debug_events = [{"type": "check", "attribute": "resolve", "degree": "critical_failure"}]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)

    def test_canon_event_firing_convenes(self):
        debug_events = [{"type": "canon_event", "key": "october_revolution", "status": "fired"}]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)
        self.assertIn("october_revolution", reasons[0])

    def test_resource_crossing_into_extreme_band_convenes(self):
        debug_events = [{"type": "state_update", "changes": [
            {"kind": "resource", "key": "personal_safety", "before_label": "Low", "after_label": "Suspect/Negative"},
        ]}]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)
        self.assertIn("Suspect/Negative", reasons[0])

    def test_ordinary_band_crossing_between_middle_bands_still_convenes_but_with_softer_reason(self):
        # A Moderate -> Rising/New shift is real but not an extreme-band crossing - still worth
        # flagging (any label change is a real narrative beat), just phrased differently.
        debug_events = [{"type": "state_update", "changes": [
            {"kind": "resource", "key": "party_standing", "before_label": "Moderate", "after_label": "Rising/New"},
        ]}]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)
        self.assertIn("shifted from Moderate to Rising/New", reasons[0])

    def test_liability_gained_convenes(self):
        debug_events = [{"type": "state_update", "changes": [
            {"kind": "liability_gained", "id": "arrogance", "name": "Arrogance"},
        ]}]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)

    def test_multiple_signals_collect_multiple_reasons(self):
        debug_events = [
            {"type": "check", "attribute": "cunning", "degree": "critical_failure"},
            {"type": "canon_event", "key": "civil_war_begins", "status": "fired"},
        ]
        convened, reasons = council.should_convene_council(debug_events)
        self.assertTrue(convened)
        self.assertEqual(len(reasons), 2)

    def test_empty_debug_events_does_not_convene(self):
        convened, reasons = council.should_convene_council([])
        self.assertFalse(convened)
        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
