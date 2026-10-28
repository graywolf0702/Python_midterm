"""測試日期範圍、單次請求、參數與總筆數限制，不連線外部 API。"""

import csv
from dataclasses import replace
from pathlib import Path
import tempfile
import queue
import unittest
from unittest.mock import MagicMock, patch

import requests
import main_file.collectors as c


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="weather-test-")
        self.addCleanup(self.temp.cleanup)
        self.options = c.DownloadOptions(end_date="2026-10-27", output_dir=Path(self.temp.name), limit=3)

    def session(self, *, text="", payload=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.text = text
        response.json.return_value = payload
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = response
        return session

    def test_date_range_and_leap_year(self):
        self.assertEqual(c.date_window(30, "2026-10-27"), ("2026-09-28", "2026-10-28"))
        self.assertEqual(c.date_window(2, "2024-03-01"), ("2024-02-29", "2024-03-02"))
        self.assertEqual(c.date_window(2, "2026-01-01"), ("2025-12-31", "2026-01-02"))
        for invalid in ("2026-02-29", "2026-2-01", "wrong"):
            with self.assertRaises(ValueError):
                c.date_window(30, invalid)

    def test_validate_rejects_bad_parameters(self):
        for options in (
            replace(self.options, limit=0), replace(self.options, limit=1001),
            replace(self.options, days=0), replace(self.options, counties=()),
            replace(self.options, station_ids="123"),
        ):
            with self.assertRaises(ValueError):
                options.validate()

    def test_blank_limit_is_none(self):
        for value in ("", "   ", None):
            self.assertIsNone(c.parse_limit(value))
        self.assertEqual(c.parse_limit(" 20 "), 20)
        self.assertIsNone(c.DownloadOptions().limit)
        replace(self.options, limit=None).validate()
        for invalid in ("0", "-1", "1001", "abc", "1.5"):
            with self.assertRaises(ValueError):
                c.parse_limit(invalid)

    def test_pm_none_omits_limit_and_keeps_all_returned_rows(self):
        text = "siteid,county,monitordate,concentration\n"
        text += "".join(f"{i},臺北市,2026-10-27,10\n" for i in range(25))
        session = self.session(text=text)
        with patch.object(c, "create_session", return_value=session):
            result = c.download_pm25(replace(self.options, limit=None), log=lambda _: None)
        session.get.assert_called_once()
        self.assertNotIn("limit", session.get.call_args.kwargs["params"])
        self.assertEqual(len(result.rows), 25)

    def test_rain_none_omits_limit_and_keeps_all_days(self):
        stations = {"466920": {"stationname": "臺北", "county": "臺北市"}}
        payload = {"success": "true", "records": {"location": [{
            "station": {"StationID": "466920"},
            "stationObsTimes": {"stationObsTime": [
                {"Date": f"2026-10-{day:02d}", "weatherElements": {"Precipitation": "0.0"}}
                for day in range(1, 28)
            ]},
        }]}}
        session = self.session(payload=payload)
        with patch.object(c, "create_session", return_value=session), patch.object(c.rainfall, "load_stations", return_value=stations):
            result = c.download_rainfall(replace(self.options, limit=None), log=lambda _: None)
        session.get.assert_called_once()
        self.assertNotIn("limit", session.get.call_args.kwargs["params"])
        self.assertEqual(len(result.rows), 27)

    def test_pm_single_request_and_local_limit(self):
        text = "siteid,sitename,county,monitordate,concentration\n"
        text += "99,不符地區,高雄市,2026-10-27,1\n"
        text += "98,範圍外,臺北市,2026-09-27,1\n"
        text += "".join(f"{i},站{i},台北市,2026-10-27,10\n" for i in range(8))
        session = self.session(text=text)
        with patch.object(c, "create_session", return_value=session):
            result = c.download_pm25(self.options, log=lambda _: None)
        session.get.assert_called_once()
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["limit"], 3)
        self.assertEqual(params["offset"], 0)
        self.assertIn("|monitordate,GR,2026-09-28|", params["filters"])
        self.assertEqual(len(result.rows), 3)
        self.assertTrue(all(row["county"] == "臺北市" for row in result.rows))
        with result.path.open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual(len(list(csv.DictReader(file))), 3)

    def test_rain_single_request_limit_and_trace_rain(self):
        stations = {"466920": {"stationid": "466920", "stationname": "臺北", "county": "臺北市"}}
        observations = [
            {"Date": f"2026-10-{day:02d}", "weatherElements": {"Precipitation": "T"}}
            for day in range(20, 28)
        ]
        observations.append({"Date": "2026-10-28", "weatherElements": {"Precipitation": "999"}})
        payload = {"success": "true", "records": {"location": [{
            "station": {"StationID": "466920"},
            "stationObsTimes": {"stationObsTime": observations},
        }]}}
        session = self.session(payload=payload)
        with patch.object(c, "create_session", return_value=session), patch.object(c.rainfall, "load_stations", return_value=stations):
            result = c.download_rainfall(replace(self.options, station_ids="466920"), log=lambda _: None)
        session.get.assert_called_once()
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["StationID"], "466920")
        self.assertEqual(params["timeTo"], "2026-10-27")
        self.assertEqual(params["limit"], 3)
        self.assertEqual([row["monitordate"] for row in result.rows], ["2026-10-27", "2026-10-26", "2026-10-25"])
        self.assertTrue(all(row["precipitation"] == "T" for row in result.rows))

    def test_invalid_station_does_not_query_rain(self):
        session = self.session()
        with patch.object(c, "create_session", return_value=session), patch.object(c.rainfall, "load_stations", return_value={}):
            with self.assertRaisesRegex(ValueError, "現存站表"):
                c.download_rainfall(replace(self.options, station_ids="466920"), log=lambda _: None)
        session.get.assert_not_called()

    def test_empty_result_preserves_existing_file(self):
        output = self.options.output_dir / "pm25_2026-09-28_to_2026-10-28_exclusive.csv"
        output.write_text("previous data", encoding="utf-8")
        session = self.session(text="siteid,county,monitordate,concentration\n")
        with patch.object(c, "create_session", return_value=session):
            with self.assertRaises(ValueError):
                c.download_pm25(self.options, log=lambda _: None)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous data")

    def test_errors_do_not_leak_keys(self):
        error = requests.exceptions.ConnectionError("https://example.test?api_key=secret")
        self.assertNotIn("secret", c.error_message(error))
        response = requests.Response()
        response.status_code = 403
        message = c.error_message(requests.exceptions.HTTPError("secret", response=response))
        self.assertIn("403", message)
        self.assertNotIn("secret", message)

    def test_gui_worker_keeps_success_if_other_source_fails(self):
        from main_file.gui import DownloadApp
        harness = MagicMock()
        harness.messages = queue.Queue()
        result = c.DownloadResult("PM2.5", Path("result.csv"), [{}], "done")

        def successful(options, log):
            return result

        def failing(options, log):
            raise requests.exceptions.ConnectionError("api_key=secret")

        DownloadApp._worker(harness, self.options, [("PM2.5", successful), ("雨量", failing)])
        events = [harness.messages.get_nowait() for _ in range(3)]
        self.assertEqual(events[0], ("result", result))
        self.assertEqual(events[1][0], "error")
        self.assertNotIn("secret", events[1][1][1])
        self.assertEqual(events[2], ("done", (1, 2)))


if __name__ == "__main__":
    unittest.main()
