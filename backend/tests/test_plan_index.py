"""Regression test for day numbering in generated plans."""

import json
import unittest

from app.agents.langgraph_planner import _parse_plan_json
from app.models.schemas import TripRequest


class PlanIndexTests(unittest.TestCase):
    def test_zero_based_model_indices_do_not_duplicate_day_one(self):
        request = TripRequest(
            city="杭州", start_date="2026-10-03", end_date="2026-10-04", travel_days=2,
            transportation="公共交通", accommodation="经济型酒店",
        )
        days = []
        for index, day_date in enumerate(("2026-10-03", "2026-10-04")):
            days.append({
                "date": day_date, "day_index": index,
                "description": "参观博物馆", "transportation": "公共交通",
                "accommodation": "经济型酒店", "meals": [],
                "attractions": [{
                    "name": "杭州博物馆", "address": "杭州", "location": {"longitude": 120.17, "latitude": 30.24},
                    "visit_duration": 90, "description": "参观展览",
                }],
            })
        raw = {"city": "杭州", "start_date": request.start_date, "end_date": request.end_date,
               "overall_suggestions": "轻松游览", "days": days}
        plan = _parse_plan_json(json.dumps(raw, ensure_ascii=False), request)
        self.assertEqual([day.day_index for day in plan.days], [0, 1])
