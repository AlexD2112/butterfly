import os
import unittest
from unittest.mock import patch

import providers


class TestBlankEnvVarIsTreatedAsUnset(unittest.TestCase):
    """A .env file with a key present but blank (e.g. `OPENAI_API_KEY=`, exactly what .env.example
    ships as a template before someone fills it in) used to pass an `in os.environ` presence check
    while still being unusable - real API client construction doesn't validate emptiness itself, so
    a blank key made it all the way to an uncaught client error instead of a clean ProviderError.
    Found via a real test failure (not hypothesized) once council.JUDGE_MODEL_KEY started routing
    through providers.get_provider("luna") in a full-suite run alongside a stray .env file with
    OPENAI_API_KEY="" - see NOTES.md."""

    def test_anthropic_provider_raises_provider_error_on_blank_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(providers.ProviderError):
                providers.AnthropicProvider()

    def test_openai_provider_raises_provider_error_on_blank_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(providers.ProviderError):
                providers.OpenAIProvider()

    def test_provider_available_is_false_for_a_blank_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertFalse(providers.provider_available("anthropic"))

    def test_provider_available_is_false_for_a_whitespace_only_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "   "}):
            self.assertFalse(providers.provider_available("openai"))

    def test_provider_available_is_true_for_a_real_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-real-looking-value"}):
            self.assertTrue(providers.provider_available("anthropic"))


if __name__ == "__main__":
    unittest.main()
