import datetime as dt
import io
import json
import sys
import socket
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flightseeker"))
from combos import Combo
from config import Route, Settings
from fetch import serpapi_price_insights


class SerpApiTests(unittest.TestCase):
    def setUp(self):
        context_patch = patch("fetch._serpapi_ssl_context", return_value=ssl.create_default_context())
        context_patch.start()
        self.addCleanup(context_patch.stop)
        self.combo = Combo("test", "TPE", "NRT", dt.date(2027, 2, 20), dt.date(2027, 2, 25))
        self.route = Route("test", ["TPE"], ["NRT"], self.combo.depart, self.combo.ret,
                           [6], max_stops=0, airlines=["CI", "BR"])

    @patch.dict("os.environ", {"SERPAPI_API_KEY": ""})
    @patch("fetch.urlopen")
    def test_missing_key_does_not_request(self, request):
        text, insights = serpapi_price_insights(self.combo, self.route, Settings())
        self.assertIn("未設定", text)
        self.assertEqual(insights, {})
        request.assert_not_called()

    @patch.dict("os.environ", {"SERPAPI_API_KEY": "test-secret"})
    @patch("fetch.urlopen")
    def test_history_and_matching_search_parameters(self, request):
        insights = {"price_level": "low", "typical_price_range": [12000, 18000],
                    "price_history": [[1700000000, 11000], [1700086400, 10000]]}
        request.return_value = io.StringIO(json.dumps({"price_insights": insights}))
        text, actual = serpapi_price_insights(self.combo, self.route, Settings(adults=2))
        self.assertEqual(actual, insights)
        self.assertIn("歷史最低 10,000 TWD（2 筆）", text)
        self.assertIn("常態區間 12,000–18,000", text)
        params = parse_qs(urlsplit(request.call_args.args[0]).query)
        self.assertEqual(request.call_args.kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(request.call_args.kwargs["context"].check_hostname)
        for key, value in {"departure_id": "TPE", "arrival_id": "NRT", "stops": "1",
                           "adults": "2", "include_airlines": "CI,BR",
                           "outbound_date": "2027-02-20", "return_date": "2027-02-25"}.items():
            self.assertEqual(params[key], [value])

    @patch.dict("os.environ", {"SERPAPI_API_KEY": "test-secret"})
    @patch("fetch.urlopen")
    def test_errors_do_not_expose_secret(self, request):
        request.side_effect = HTTPError("https://example.test/?api_key=test-secret", 429,
                                        "test-secret", None, None)
        text, insights = serpapi_price_insights(self.combo, self.route, Settings())
        self.assertEqual(text, "SerpApi HTTP 429")
        self.assertNotIn("test-secret", text)
        self.assertEqual(insights, {})

    @patch.dict("os.environ", {"SERPAPI_API_KEY": "test-secret"})
    @patch("fetch.urlopen")
    def test_missing_insights(self, request):
        request.return_value = io.StringIO('{"best_flights": []}')
        text, insights = serpapi_price_insights(self.combo, self.route, Settings())
        self.assertIn("未提供", text)
        self.assertEqual(insights, {})

    @patch.dict("os.environ", {"SERPAPI_API_KEY": "test-secret"})
    @patch("fetch.urlopen")
    def test_connection_errors_are_distinguishable_and_redacted(self, request):
        cases = [
            (URLError(ssl.SSLCertVerificationError(1, "test-secret")), "憑證驗證失敗"),
            (URLError(socket.gaierror(11001, "test-secret")), "DNS 解析失敗"),
            (URLError(TimeoutError("test-secret")), "逾時"),
            (ConnectionResetError(10054, "test-secret"), "連線被重設"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                request.side_effect = error
                text, insights = serpapi_price_insights(self.combo, self.route, Settings())
                self.assertIn(expected, text)
                self.assertNotIn("test-secret", text)
                self.assertEqual(insights, {})

    @patch.dict("os.environ", {"SERPAPI_API_KEY": "test-secret"})
    @patch("fetch.urlopen")
    def test_invalid_json(self, request):
        request.return_value = io.StringIO('<html>test-secret</html>')
        text, insights = serpapi_price_insights(self.combo, self.route, Settings())
        self.assertIn("不是有效的 JSON", text)
        self.assertNotIn("test-secret", text)
        self.assertEqual(insights, {})


if __name__ == "__main__":
    unittest.main()
