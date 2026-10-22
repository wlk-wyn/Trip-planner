"""Offline regression tests for the Agent's decision and grounding loop."""

import json
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.agents.langgraph_planner import LangGraphTripPlanner
from app.agents.travel_agent import TravelAgent, _check_places
from app.models.schemas import TripPlan, TripRequest


REQUEST = TripRequest(
    city="杭州", start_date="2026-10-03", end_date="2026-10-03", travel_days=1,
    transportation="公共交通", accommodation="酒店", preferences=["历史文化"],
)
POI = {"name": "西湖博物馆", "address": "南山路89号", "coordinates": [120.15, 30.24]}
PLAN = {
    "city": "杭州", "start_date": "2026-10-03", "end_date": "2026-10-03",
    "overall_suggestions": "参观已核实的博物馆。",
    "days": [{
        "date": "2026-10-03", "day_index": 0, "description": "西湖博物馆",
        "transportation": "公共交通", "accommodation": "酒店", "meals": [],
        "attractions": [{
            "name": POI["name"], "address": POI["address"],
            "location": {"longitude": 120.15, "latitude": 30.24},
            "visit_duration": 90, "description": "参观展览",
        }],
    }],
}


class FakeModel:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def make_agent(model):
    agent = TravelAgent.__new__(TravelAgent)
    agent.llm = model
    agent.planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
    agent.planner._align_meals_to_route = AsyncMock()
    agent._check_schedule = AsyncMock()
    return agent


class TravelAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_can_choose_a_tool_and_ground_its_plan(self):
        model = FakeModel([
            AIMessage(content="", tool_calls=[{
                "name": "search_attractions", "args": {"keyword": "博物馆"}, "id": "call-1"
            }]),
            AIMessage(content=json.dumps({"changed": True, "reply": "行程已规划", "plan": PLAN}, ensure_ascii=False)),
        ])
        agent = make_agent(model)
        with patch("app.agents.travel_agent.search_pois", new=AsyncMock(return_value=[POI])) as search:
            result, reply, changed, actions = await agent.run(REQUEST)
        self.assertEqual(search.await_args.args[:2], ("杭州", "博物馆"))
        self.assertEqual(result.days[0].attractions[0].name, "西湖博物馆")
        self.assertEqual(reply, "行程已规划")
        self.assertIn("search_attractions", actions)
        self.assertFalse(changed)  # No existing plan was revised.

    async def test_question_does_not_change_current_plan(self):
        current = TripPlan(**PLAN)
        model = FakeModel([AIMessage(content=json.dumps({
            "changed": False, "reply": "第一天参观西湖博物馆。", "plan": None,
        }, ensure_ascii=False))])
        agent = make_agent(model)
        result, reply, changed, actions = await agent.run(REQUEST, current, "第一天做什么？")
        self.assertEqual(result, current)
        self.assertEqual(reply, "第一天参观西湖博物馆。")
        self.assertFalse(changed)
        self.assertEqual(actions, [])

    def test_new_places_without_evidence_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "未经地图查询核实"):
            _check_places(TripPlan(**PLAN), {
                "names": {"attractions": set(), "hotels": set(), "restaurants": set()}
            })


if __name__ == "__main__":
    unittest.main()
