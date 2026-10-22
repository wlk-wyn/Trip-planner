"""Offline tests for the natural-language trip entry."""

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage

from app.agents.trip_intake import interpret_trip_intake, normalize_intake
from app.models.schemas import ChatTurn


class TripIntakeTests(unittest.TestCase):
    def test_requires_exact_departure_date(self):
        result = normalize_intake({"city": "杭州", "travel_days": 2}, date(2026, 9, 30))
        self.assertFalse(result.ready)
        self.assertIsNone(result.start_date)
        self.assertIn("什么时候出发", result.reply)

    def test_derives_end_date_from_start_and_days(self):
        result = normalize_intake({
            "city": "杭州", "start_date": "2026-10-03", "travel_days": 2,
            "preferences": ["历史文化"], "free_text_input": "喜欢慢节奏",
        }, date(2026, 9, 30))
        self.assertTrue(result.ready)
        self.assertEqual(result.end_date, "2026-10-04")
        self.assertEqual(result.preferences, ["历史文化"])

    def test_rejects_past_or_overlong_dates(self):
        past = normalize_intake({
            "city": "杭州", "start_date": "2026-09-01", "travel_days": 2,
        }, date(2026, 9, 30))
        long = normalize_intake({
            "city": "杭州", "start_date": "2026-10-01", "end_date": "2026-11-01",
        }, date(2026, 9, 30))
        self.assertFalse(past.ready)
        self.assertFalse(long.ready)

    def test_literal_trip_days_override_invented_end_date_and_old_form(self):
        raw = {'city': '西安', 'start_date': '2026-11-06', 'end_date': '2026-11-09', 'travel_days': 4}
        for description in ('西安2026-11-06出发玩3天', '西安2026-11-06三天游',
                            '改成玩三天\n已填写条件（本条新描述中的更改优先）：返程日期：2026-11-09'):
            result = normalize_intake(raw, date(2026, 10, 16), [description])
            self.assertTrue(result.ready)
            self.assertEqual((result.travel_days, result.end_date), (3, '2026-11-08'))

    def test_explicit_return_date_conflicting_with_length_needs_clarification(self):
        raw = {'city': '西安', 'start_date': '2026-11-06', 'end_date': '2026-11-09', 'travel_days': 3}
        result = normalize_intake(raw, date(2026, 10, 16), ['11月6日出发，11月9日返程，玩3天。'])
        self.assertFalse(result.ready)
        self.assertIn('日期范围还是按天数', result.reply)

    def test_latest_return_date_change_is_not_overwritten_by_old_duration(self):
        raw = {'city': '西安', 'start_date': '2026-11-06', 'end_date': '2026-11-10', 'travel_days': 5}
        result = normalize_intake(raw, date(2026, 10, 16), ['11月6日出发玩3天', '改成11月10日返程'])
        self.assertTrue(result.ready)
        self.assertEqual((result.travel_days, result.end_date), (5, '2026-11-10'))


class TripIntakeModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_latest_correction(self):
        model = SimpleNamespace(fast_llm=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(
            content=json.dumps({
                "city": "杭州", "start_date": "2026-10-03", "travel_days": 2,
                "transportation": None, "accommodation": None,
                "preferences": ["历史文化"], "free_text_input": "慢节奏",
            }, ensure_ascii=False)
        ))))
        turns = [
            ChatTurn(role="user", content="想去苏州"),
            ChatTurn(role="assistant", content="什么时候出发？"),
            ChatTurn(role="user", content="改去杭州，10月3日出发玩两天"),
        ]
        with patch("app.agents.trip_intake.get_langgraph_planner", return_value=model), \
             patch("app.agents.trip_intake.date") as mock_date:
            mock_date.today.return_value = date(2026, 9, 30)
            mock_date.fromisoformat.side_effect = date.fromisoformat
            result = await interpret_trip_intake(turns)
        self.assertEqual(result.city, "杭州")
        self.assertTrue(result.ready)
        self.assertEqual(model.fast_llm.ainvoke.await_count, 1)

    async def test_ignores_text_after_first_json_object(self):
        payload = {"city": "杭州", "start_date": None, "end_date": None,
                   "travel_days": 2, "preferences": [], "free_text_input": ""}
        model = SimpleNamespace(fast_llm=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(
            content=json.dumps(payload, ensure_ascii=False) + "\n补充说明：请确认日期。"
        ))))
        with patch("app.agents.trip_intake.get_langgraph_planner", return_value=model):
            result = await interpret_trip_intake([ChatTurn(role="user", content="杭州玩两天")])
        self.assertFalse(result.ready)
        self.assertEqual(result.city, "杭州")

    async def test_repairs_malformed_json_once(self):
        model = SimpleNamespace(fast_llm=SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            AIMessage(content='{"city":"杭州" "travel_days":2}'),
            AIMessage(content='{"city":"杭州","travel_days":2,"start_date":null,"end_date":null}'),
        ])))
        with patch("app.agents.trip_intake.get_langgraph_planner", return_value=model):
            result = await interpret_trip_intake([ChatTurn(role="user", content="杭州玩两天")])
        self.assertEqual(result.city, "杭州")
        self.assertFalse(result.ready)
        self.assertEqual(model.fast_llm.ainvoke.await_count, 2)
