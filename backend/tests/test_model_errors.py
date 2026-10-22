"""Offline checks for actionable, sanitized model failure messages."""
import unittest
import httpx
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError
from app.services.model_errors import model_service_error


class ModelErrorTests(unittest.TestCase):
    def setUp(self):
        self.request = httpx.Request("POST", "https://example.com/chat/completions")

    def test_connection_and_timeout_are_distinct(self):
        connection = model_service_error(APIConnectionError(request=self.request))
        timeout = model_service_error(APITimeoutError(request=self.request))
        self.assertEqual(connection[0], 502)
        self.assertIn("代理", connection[1])
        self.assertEqual(timeout[0], 504)
        self.assertIn("超时", timeout[1])

    def test_credentials_and_provider_body_are_not_exposed(self):
        for error_type, code in [(AuthenticationError, 401), (RateLimitError, 429)]:
            response = httpx.Response(code, request=self.request)
            result = model_service_error(error_type("secret-token", response=response,
                                                   body={"secret": "secret-token"}))
            self.assertIsNotNone(result)
            self.assertNotIn("secret-token", result[1])

    def test_wrapped_errors_and_unrelated_failures(self):
        wrapped = RuntimeError("planning failed")
        wrapped.__cause__ = APIConnectionError(request=self.request)
        self.assertEqual(model_service_error(wrapped)[0], 502)
        self.assertIsNone(model_service_error(ValueError("invalid JSON")))
        wrapped.__cause__ = wrapped
        self.assertIsNone(model_service_error(wrapped))
