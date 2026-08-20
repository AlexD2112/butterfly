import unittest

import pricing


class TestEstimateCostUsd(unittest.TestCase):
    def test_anthropic_basic(self):
        usage = {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        self.assertAlmostEqual(pricing.estimate_cost_usd(usage), 2.00 + 10.00)

    def test_anthropic_cache_read_discount(self):
        usage = {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 1_000_000,
            "cache_creation_tokens": 0,
        }
        self.assertAlmostEqual(pricing.estimate_cost_usd(usage), 2.00 * 0.1)

    def test_anthropic_cache_creation_premium(self):
        usage = {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 1_000_000,
        }
        self.assertAlmostEqual(pricing.estimate_cost_usd(usage), 2.00 * 1.25)

    def test_openai_cached_tokens_are_a_subset_not_additive(self):
        # 1000 input_tokens where 400 of them are cached should NOT be billed as 1400 tokens.
        usage = {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "input_tokens": 1000,
            "output_tokens": 0,
            "cache_read_tokens": 400,
            "cache_creation_tokens": 0,
        }
        expected = (600 / 1_000_000) * 0.20 + (400 / 1_000_000) * (0.20 * 0.1)
        self.assertAlmostEqual(pricing.estimate_cost_usd(usage), expected)

    def test_unknown_model_fails_open_to_zero(self):
        usage = {
            "provider": "openai",
            "model": "some-future-model-not-in-table",
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        self.assertEqual(pricing.estimate_cost_usd(usage), 0.0)

    def test_small_realistic_turn_is_well_under_starting_budget(self):
        # A single Luna turn (~1500 input, ~400 output tokens) should cost a small fraction of a cent.
        usage = {
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "input_tokens": 1500,
            "output_tokens": 400,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        cost = pricing.estimate_cost_usd(usage)
        self.assertGreater(cost, 0)
        self.assertLess(cost, 0.01)


if __name__ == "__main__":
    unittest.main()
