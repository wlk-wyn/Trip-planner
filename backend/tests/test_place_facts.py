"""Amap price/opening-hours facts, model ticket estimates and closed-day checks."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

from app.models.schemas import Hotel, Meal
from app.services.amap_rest import _parse_pois
from app.services.place_facts import (AMAP_MEAL_SOURCE, MODEL_TICKET_SOURCE, apply_provider_facts, closed_weekdays,
                                      enrich_plan, estimate_ticket_prices)
from app.services.plan_checks import run_checks
from test_travel_agent import REQUEST
from test_weather_and_arrange import two_day_plan

HOURS = "周二至周日 09:00-17:00开放,最晚进入16:00；周一 全天不开放；每周一不开放,逢法定节假日顺延"


def fake_llm(content):
    return SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content=content)))


class ParseTests(unittest.TestCase):
    def test_business_fields_treat_empty_lists_as_missing(self):
        pois = _parse_pois({"pois": [
            {"id": "r1", "name": "文和友", "location": "112.9,28.2", "type": "餐饮服务", "biz_ext": {"cost": "88.00", "rating": "4.8", "open_time": "11:00-02:00", "opentime2": []}},
            {"id": "a1", "name": "博物馆", "location": "112.9,28.2", "type": "科教文化服务;博物馆", "biz_ext": {"cost": [], "rating": [], "opentime2": HOURS}},
        ]})
        self.assertEqual((pois[0]["avg_cost"], pois[0]["rating"], pois[0]["open_hours"]), (88.0, 4.8, "11:00-02:00"))
        self.assertNotIn("avg_cost", pois[1])
        self.assertEqual(pois[1]["open_hours"], HOURS)

    def test_closed_weekdays(self):
        self.assertEqual(closed_weekdays(HOURS), {0})
        self.assertEqual(closed_weekdays("周一至周日 08:30-17:00"), set())
        self.assertEqual(closed_weekdays("每周日闭馆"), {6})


class FactTests(unittest.IsolatedAsyncioTestCase):
    def test_provider_price_labels_source_and_never_overrides_user(self):
        meal = Meal(type="lunch", name="", restaurant="文和友")
        apply_provider_facts(meal, {"avg_cost": 88.0, "open_hours": "11:00-02:00"})
        self.assertEqual((meal.estimated_cost, meal.cost_status, meal.cost_source), (88.0, "estimated", AMAP_MEAL_SOURCE))
        mine = Meal(type="lunch", name="", restaurant="文和友", estimated_cost=50, cost_status="confirmed", cost_source="用户填写")
        apply_provider_facts(mine, {"avg_cost": 88.0})
        self.assertEqual(mine.estimated_cost, 50)
        hotel = Hotel(name="酒店")
        apply_provider_facts(hotel, {"hotel_price": 299.0, "rating": 4.4})
        self.assertEqual((hotel.estimated_cost, hotel.rating), (299.0, "4.4"))

    async def test_ticket_estimates_are_labelled_and_bounded(self):
        plan = two_day_plan()
        museum, park = plan.days[0].attractions
        park.ticket_price, park.cost_status, park.cost_source = 20, "confirmed", "用户填写"
        llm = fake_llm('{"%s": 0, "%s": 99999, "unknown-id": 50}' % (museum.item_id, plan.days[1].attractions[1].item_id))
        count = await estimate_ticket_prices(plan, llm)
        self.assertEqual(count, 1)
        self.assertEqual((museum.ticket_price, museum.cost_status, museum.cost_source), (0, "estimated", MODEL_TICKET_SOURCE))
        self.assertEqual(park.ticket_price, 20)  # user price untouched and not sent
        self.assertNotIn(park.item_id, llm.ainvoke.await_args.args[0][1].content)
        self.assertIsNone(plan.days[1].attractions[1].ticket_price)  # out-of-range value rejected

    async def test_enrich_fills_missing_facts_and_survives_lookup_failures(self):
        plan = two_day_plan()
        plan.days[0].meals = [Meal(type="lunch", name="", restaurant="文和友", poi_id="r1")]

        async def detail(poi_id):
            if poi_id == "park":
                raise RuntimeError("限流")
            return {"poi_id": poi_id, "avg_cost": 88.0, "open_hours": HOURS}
        summary = await enrich_plan(plan, fake_llm("{}"), detail)
        self.assertEqual(plan.days[0].meals[0].estimated_cost, 88.0)
        self.assertEqual((summary["looked_up"], summary["failed"]), (2, 1))

    def test_closed_day_check(self):
        plan = two_day_plan()  # 2026-10-03 is a Saturday, 2026-10-05 is a Monday
        plan.days[1].date = "2026-10-05"
        plan.days[1].attractions[0].open_hours = "周一全天不开放"
        issues = [i for i in run_checks(plan, REQUEST) if i["code"] == "closed_day"]
        self.assertEqual([i["day_index"] for i in issues], [1])
        self.assertIn("当天不开放", issues[0]["message"])

    def test_holiday_exception_is_not_a_proven_closure(self):
        plan = two_day_plan()
        plan.days[1].date = "2026-10-05"
        plan.days[1].attractions[0].open_hours = HOURS
        self.assertFalse(any(i["code"] == "closed_day" for i in run_checks(plan, REQUEST)))
        self.assertTrue(any(i["code"] == "opening_hours_unknown" for i in run_checks(plan, REQUEST)))


if __name__ == "__main__":
    unittest.main()
