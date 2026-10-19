import unittest

from custom_components.im_motors.trip_parser import detail_query, parse_day, request_plan


def response(data, code="200"):
    return {"resultCode": code, "data": data}


def trip(index):
    return {"tripId": f"synthetic-{index}",
            "tripStartTime": "2026-10-18 23:40:00",
            "tripEndTime": "2026-10-19 00:20:00",
            "tripMileage": "12.3", "tripDuration": 40}


class TripParserTests(unittest.TestCase):
    def parse(self, pages, details=None, summary=None):
        return parse_day("2026-10-18", response(summary or {"tripStatus": 1, "dayMileage": 15.2}), pages, details)

    def test_all_pages_and_detail_required_for_addresses(self):
        pages = [response({"totalCount": 6, "blocks": [trip(i) for i in range(5)]}),
                 response({"totalCount": 6, "blocks": [trip(5)]})]
        partial = self.parse(pages[:1])
        self.assertFalse(partial["list_complete"])
        self.assertEqual(partial["total_count"], 6)
        result = self.parse(pages)
        self.assertTrue(result["list_complete"])
        self.assertFalse(result["addresses_complete"])
        self.assertEqual(result["distance_km"], 15.2)  # Not the sum of listed trips.
        self.assertTrue(result["trips"][0]["crosses_date"])
        self.assertEqual(result["trips"][0]["end_time"], "2026-10-19 00:20:00")
        self.assertEqual(result["trips"][0]["distance_km"], 12.3)

    def test_detail_can_contain_coordinates_without_addresses(self):
        result = self.parse([response({"totalCount": 1, "blocks": [trip(1)]})],
                            {"synthetic-1": response({"tripStartLat": 31, "tripStartLon": 121,
                                                      "tripEndLat": 0, "tripEndLon": 0})})
        row = result["trips"][0]
        self.assertTrue(row["detail_loaded"])
        self.assertIsNone(row["start_address"])
        self.assertEqual(row["start_coordinates"]["latitude"], 31)
        self.assertIsNone(row["end_coordinates"])
        self.assertFalse(result["addresses_complete"])

    def test_detail_addresses_and_identity(self):
        pages = [response({"totalCount": 1, "blocks": [trip(1)]})]
        result = self.parse(pages, {"synthetic-1": response({"startTripAddress": "测试起点", "endTripAddress": "测试终点"})})
        self.assertTrue(result["addresses_complete"])
        with self.assertRaises(ValueError):
            self.parse(pages, {"synthetic-1": response({"tripId": "different"})})

    def test_failure_not_interpreted_as_zero_trips(self):
        with self.assertRaises(ValueError):
            self.parse([response({}, "500")])
        with self.assertRaises(ValueError):
            parse_day("2026-10-18", response({}, "401"), [])
        for data in [{"totalCount": 0}, {"totalCount": "0", "blocks": []}]:
            with self.assertRaises(ValueError):
                self.parse([response(data)])

    def test_empty_vs_missing_and_permission(self):
        self.assertFalse(self.parse([])["list_complete"])
        empty = self.parse([response({"totalCount": 0, "blocks": []})], summary={"tripStatus": 2, "dayMileage": 0})
        self.assertEqual(empty["status"], "no_trips")
        self.assertEqual(empty["distance_km"], 0)
        self.assertIsNone(empty["duration_minutes"])
        self.assertTrue(empty["list_complete"])
        denied = self.parse([], summary={"tripStatus": 3})
        self.assertEqual(denied["status"], "no_access")
        self.assertIsNone(denied["distance_km"])

    def test_duplicate_pages_and_changing_total(self):
        page = response({"totalCount": 2, "blocks": [trip(1)]})
        result = self.parse([page, page])
        self.assertEqual(result["parsed_count"], 1)
        self.assertFalse(result["list_complete"])
        result = self.parse([page, response({"totalCount": 3, "blocks": [trip(2), trip(3)]})])
        self.assertFalse(result["page_counts_consistent"])
        self.assertFalse(result["list_complete"])

    def test_invalid_measurements_not_defaulted(self):
        row = trip(1)
        row.update(tripMileage=True, tripDuration="NaN", tripEndTime="invalid")
        result = self.parse([response({"totalCount": 1, "blocks": [row]})])
        for key in ["distance_km", "duration_minutes", "crosses_date", "end_time"]:
            self.assertIsNone(result["trips"][0][key])

    def test_page_number_and_cross_day_detail_query(self):
        plan = request_plan("2026-10-18", 2)
        self.assertEqual(plan["trips"]["query"], {"tripDate": "2026-10-18", "offset": 2, "limit": 5})
        for day, page in [("2026-02-30", 1), ("20261018", 1), ("2026-10-18", 0)]:
            with self.assertRaises(ValueError):
                request_plan(day, page)
        row = self.parse([response({"totalCount": 1, "blocks": [trip(1)]})])["trips"][0]
        query = detail_query(row)
        self.assertEqual(int(query["tripEndTime"]) - int(query["tripStartTime"]), 40 * 60 * 1000)


if __name__ == "__main__":
    unittest.main()
