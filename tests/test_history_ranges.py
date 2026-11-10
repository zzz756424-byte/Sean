import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flightseeker"))
from store import history_flight_key, history_price_ranges


class HistoryRangesTests(unittest.TestCase):
    def test_matches_times_and_carriers_and_ignores_invalid_prices(self):
        row = dict(origin="TPE", destination="NRT", depart="2027-02-20", ret="2027-02-25",
                   outbound="08:00→12:00", stops="0", airlines="EVA Air/ANA", total="12000")
        rows = [row, dict(row, total="10000"), dict(row, total=""), dict(row, total="0"),
                dict(row, outbound="09:00→13:00", total="9000"),
                dict(row, airlines="China Airlines", total="8000"),
                dict(row, ret="2027-02-26", total="7000")]
        ranges = history_price_ranges(rows)
        current = dict(row, airlines=["ANA", "EVA Air"], stops=0)
        self.assertEqual(ranges[history_flight_key(current)], (10000, 12000, 2))
        self.assertNotIn(history_flight_key(dict(current, origin="KHH")), ranges)

    def test_empty_history(self):
        self.assertEqual(history_price_ranges([]), {})


if __name__ == "__main__":
    unittest.main()
