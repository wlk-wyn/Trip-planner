"""Rule-based plan checks and the pre-departure checklist."""
import unittest
from datetime import date, timedelta

from app.models.schemas import Hotel, Location, Meal, TripRequest
from app.services.plan_checks import run_checks, stated_budget
from app.services.trip_prep import build_prep
from test_weather_and_arrange import two_day_plan
from test_travel_agent import REQUEST


def codes(issues):
    return [issue["code"] for issue in issues]


class PlanCheckTests(unittest.TestCase):
    def test_duplicates_missing_meals_and_hotel_change(self):
        plan = two_day_plan()  # day 2 repeats day 1's attractions and has no meals
        plan.days[0].meals = [Meal(type="lunch", name="", restaurant="知味观", poi_id="r1"),
                              Meal(type="dinner", name="", restaurant="楼外楼", poi_id="r2")]
        plan.days[1].meals = [Meal(type="lunch", name="", restaurant="知味观", poi_id="r1")]
        here = Location(longitude=120.15, latitude=30.24)
        plan.days[0].hotel = Hotel(name="甲酒店", address="", location=here, poi_id="h1")
        issues = run_checks(plan, REQUEST.model_copy(update={"travel_days": 2, "end_date": "2026-10-04"}))
        self.assertIn("duplicate_attraction", codes(issues))
        self.assertIn("duplicate_restaurant", codes(issues))
        self.assertEqual([i["day_index"] for i in issues if i["code"] == "missing_meal"], [1])
        self.assertNotIn("missing_hotel", codes(issues))  # the only night has a hotel

    def test_outliers_and_invalid_coordinates(self):
        plan = two_day_plan()
        plan.days[1].attractions[0].location = Location(longitude=121.47, latitude=31.23)  # Shanghai, ~165 km away
        plan.days[1].attractions[1].location = Location(longitude=2.35, latitude=48.85)
        issues = run_checks(plan, REQUEST)
        self.assertIn("location_outlier", codes(issues))
        self.assertEqual([i["severity"] for i in issues if i["code"] == "invalid_location"], ["error"])

    def test_stated_budget_compares_known_costs_only(self):
        request = REQUEST.model_copy(update={"free_text_input": "两个人，预算 300 元，想轻松一点"})
        self.assertEqual(stated_budget(request), 300)
        plan = two_day_plan()
        self.assertNotIn("over_budget", codes(run_checks(plan, request)))  # unknown prices are not counted
        plan.days[0].attractions[0].ticket_price = 400
        plan.days[0].attractions[0].cost_status = "confirmed"
        self.assertNotIn("over_budget", codes(run_checks(plan, request)))  # scope and headcount still unconfirmed
        request = request.model_copy(update={'party_size': 2, 'room_count': 1, 'destination_budget': 300})
        self.assertIn("over_budget", codes(run_checks(plan, request)))
        self.assertIsNone(stated_budget(REQUEST))


class TripPrepTests(unittest.TestCase):
    def test_booking_reminders_stays_and_weather_packing(self):
        plan = two_day_plan()
        plan.days[0].attractions[1].fixed_start_time = "10:00"
        hotel = Hotel(name="甲酒店", address="湖滨路", location=Location(longitude=120.16, latitude=30.25), poi_id="h1")
        plan.days[0].hotel = hotel
        prep = build_prep(plan, {"2026-10-03": {"date": "2026-10-03", "dayweather": "小雨", "nightweather": "阴", "daytemp": "8", "nighttemp": "3"}})
        kinds = {(r["name"], r["kind"]) for r in prep["reminders"]}
        self.assertIn(("西湖博物馆", "likely_booking"), kinds)
        self.assertIn(("太子湾公园", "appointment"), kinds)
        museum = next(r for r in prep["reminders"] if r["kind"] == "likely_booking")
        self.assertIsNone(museum["confirm_by"])
        self.assertRegex(prep['stays'][0]['id'],r'^[0-9a-f]{32}$')
        self.assertEqual([{k:v for k,v in s.items() if k!='id'} for s in prep["stays"]], [{"name": "甲酒店", "address": "湖滨路", "check_in": "2026-10-03", "check_out": "2026-10-04", "nights": 1}])
        items = [p["item"] for p in prep["packing"]]
        self.assertTrue(any("雨伞" in item for item in items))
        self.assertTrue(any("保暖" in item for item in items))
        self.assertIn("1/2", prep["weather_note"])

    def test_unknown_policy_does_not_invent_a_booking_deadline(self):
        plan = two_day_plan()
        visit = date.today() + timedelta(days=30)
        plan.days[0].date = visit.isoformat()
        reminder = next(r for r in build_prep(plan)["reminders"] if r["day_index"] == 0)
        self.assertIsNone(reminder["confirm_by"])
        self.assertIsNone(reminder["booking_opens_at"])
        self.assertIsNone(reminder["policy_source_url"])


if __name__ == "__main__":
    unittest.main()
