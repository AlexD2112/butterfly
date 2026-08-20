import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db


class TestBudgetTracking(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_new_user_gets_default_budget_and_zero_spend(self):
        user = db.get_or_create_local_user()
        status = db.get_budget_status(user["id"])
        self.assertEqual(status["budget_usd"], db.DEFAULT_BUDGET_USD)
        self.assertEqual(status["spent_usd"], 0.0)
        self.assertEqual(status["remaining_usd"], db.DEFAULT_BUDGET_USD)

    def test_record_usage_reduces_remaining(self):
        user = db.get_or_create_local_user()
        db.record_usage(user["id"], None, "openai", "gpt-5.6-luna", 1000, 500, 0, 0, 0.03)
        status = db.get_budget_status(user["id"])
        self.assertAlmostEqual(status["spent_usd"], 0.03)
        self.assertAlmostEqual(status["remaining_usd"], db.DEFAULT_BUDGET_USD - 0.03)

    def test_remaining_never_goes_negative(self):
        user = db.get_or_create_local_user()
        db.record_usage(user["id"], None, "openai", "gpt-5.6-luna", 1000, 500, 0, 0, 5.00)
        status = db.get_budget_status(user["id"])
        self.assertEqual(status["remaining_usd"], 0.0)

    def test_init_db_is_idempotent_across_process_restarts(self):
        # server.py calls db.init_db() on every process start against the same persistent file -
        # the budget_usd ALTER TABLE must not error on the 2nd/3rd/... call.
        db.init_db()
        db.init_db()
        user = db.get_or_create_local_user()
        status = db.get_budget_status(user["id"])
        self.assertEqual(status["budget_usd"], db.DEFAULT_BUDGET_USD)

    def test_two_accounts_have_independent_budgets(self):
        local_user = db.get_or_create_local_user()
        discord_user = db.get_or_create_discord_user("12345", "Someone", None)
        db.record_usage(local_user["id"], None, "openai", "gpt-5.6-luna", 1000, 500, 0, 0, 0.05)
        local_status = db.get_budget_status(local_user["id"])
        discord_status = db.get_budget_status(discord_user["id"])
        self.assertAlmostEqual(local_status["spent_usd"], 0.05)
        self.assertEqual(discord_status["spent_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
