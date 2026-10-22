"""Budget grounding, clarification, deferral, and server-side party arithmetic."""
import unittest
from datetime import date

from app.agents.trip_intake import normalize_intake
from app.models.schemas import TripRequest
from app.services.budget_intake import budget_context

BASE = {'city': '西安', 'start_date': '2026-11-06', 'travel_days': 3}
TODAY = date(2026, 10, 16)


def extraction(text, **values):
    return {**BASE, **values, 'cost_evidence': {key: text for key in values}}


class BudgetIntakeTests(unittest.TestCase):
    def test_student_budget_requires_scope_and_counts_without_defaulting(self):
        text = '学生党，预算1500元'
        result = normalize_intake(extraction(text, budget_amount=1500), TODAY, [text])
        self.assertFalse(result.ready)
        self.assertTrue(result.budget_clarification)
        self.assertIsNone(result.party_size)
        self.assertIsNone(result.room_count)
        self.assertIsNone(result.destination_budget)
        for phrase in ('同行总人数', '每晚房间数', '每人还是全体', '不含往返', '稍后再定'):
            self.assertIn(phrase, result.reply)

    def test_explicit_destination_total_is_carried_to_request(self):
        text = '2人，每晚1间，目的地内全体总预算1500元，不含往返大交通'
        result = normalize_intake(extraction(text, party_size=2, room_count=1, budget_amount=1500,
                                 budget_basis='party', budget_coverage='destination'), TODAY, [text])
        self.assertTrue(result.ready)
        request = TripRequest.model_validate({**result.model_dump(), 'transportation': '公共交通',
            'accommodation': '经济型酒店', 'reference_type': 'none', 'reference_content': '', 'reference_mode': 'flexible'})
        self.assertEqual((request.party_size, request.room_count, request.destination_budget), (2, 1, 1500))
        self.assertEqual(TripRequest.model_validate_json(request.model_dump_json()), request)

    def test_per_person_budget_is_multiplied_only_on_server(self):
        text = '2人1间，目的地内每人预算1500元，不含往返'
        result = normalize_intake(extraction(text, party_size=2, room_count=1, budget_amount=1500,
                                 budget_basis='person', budget_coverage='destination'), TODAY, [text])
        self.assertTrue(result.ready)
        self.assertEqual(result.destination_budget, 3000)
        forged = extraction(text, party_size=2, room_count=1, budget_amount=3000,
                            budget_basis='person', budget_coverage='destination')
        self.assertIsNone(normalize_intake(forged, TODAY, [text]).destination_budget)

    def test_whole_trip_budget_is_not_spent_again_on_destination(self):
        text = '2人1间，全体总预算1500元，包含往返机票'
        result = normalize_intake(extraction(text, party_size=2, room_count=1, budget_amount=1500,
                                 budget_basis='party', budget_coverage='whole_trip'), TODAY, [text])
        self.assertFalse(result.ready)
        self.assertIsNone(result.destination_budget)
        self.assertIn('不含往返大交通', result.reply)

    def test_one_day_does_not_require_room_count(self):
        text = '我1人，目的地内预算300元，不含往返'
        raw = extraction(text, party_size=1, budget_amount=300, budget_coverage='destination')
        raw['travel_days'] = 1
        result = normalize_intake(raw, TODAY, [text])
        self.assertTrue(result.ready)
        self.assertEqual(result.destination_budget, 300)
        self.assertIsNone(result.room_count)

    def test_no_budget_does_not_add_cost_questions_to_quick_planning(self):
        result = normalize_intake(BASE, TODAY, ['西安11月6日出发玩3天'])
        self.assertTrue(result.ready)
        self.assertFalse(result.budget_clarification)

    def test_assistant_examples_and_unquoted_values_are_not_user_confirmation(self):
        quote = '2人1间，目的地内全体预算1500元'
        raw = extraction(quote, party_size=2, room_count=1, budget_amount=1500,
                         budget_basis='party', budget_coverage='destination')
        result = normalize_intake(raw, TODAY, ['学生党，预算1500元'])
        self.assertFalse(result.ready)
        self.assertIsNone(result.party_size)
        self.assertIsNone(result.room_count)
        self.assertIsNone(result.destination_budget)

    def test_counts_need_matching_units_and_valid_integer_bounds(self):
        for text, people in [('我们俩住1间', 2), ('我们仨，每晚1间', 3), ('一家三口，每晚1间', 3),
                             ('2大1小，每晚1间', 3), ('二十三人，每晚1间', 23)]:
            with self.subTest(text=text):
                context, _ = budget_context(extraction(text, party_size=people, room_count=1), [text], 3)
                self.assertEqual((context['party_size'], context['room_count']), (people, 1))
        for value in (True, 0, -1, 51, 1.5, '2'):
            context, _ = budget_context(extraction('2人1间', party_size=value, room_count=value), ['2人1间'], 3)
            self.assertIsNone(context['party_size'])
            self.assertIsNone(context['room_count'])
        for text, value in [('123人', 23), ('十十人', 10), ('双人房', 2), ('玩3天', 3)]:
            context, _ = budget_context(extraction(text, party_size=value), [text], 3)
            self.assertIsNone(context['party_size'])

    def test_invalid_amounts_do_not_create_a_total(self):
        for value in (True, -1, float('nan'), float('inf'), 1e308, '1500'):
            context, _ = budget_context(extraction('预算1500元', budget_amount=value), ['预算1500元'], 3)
            self.assertIsNone(context['budget_amount'])
        for text, value in [('预算1,00元', 0), ('预算-1元', 1)]:
            context, missing = budget_context(extraction(text, budget_amount=value), [text], 3)
            self.assertIsNone(context['budget_amount'])
            self.assertTrue(missing)

    def test_defer_overrides_prior_budget_and_old_form_context(self):
        old = '2人1间，目的地内全体预算1500元'
        latest = '先规划行程，预算稍后再定。\n已填写条件（本条新描述中的更改优先）：' + old
        raw = extraction(old, party_size=2, room_count=1, budget_amount=1500,
                         budget_basis='party', budget_coverage='destination')
        result = normalize_intake(raw, TODAY, [old, latest])
        self.assertTrue(result.ready)
        self.assertEqual(result.budget_mode, 'deferred')
        self.assertIsNone(result.destination_budget)
        self.assertIn('暂不作达标判断', result.reply)
        result = normalize_intake(raw, TODAY, [old, latest, old])
        self.assertEqual(result.budget_mode, 'active')
        self.assertEqual(result.destination_budget, 1500)

    def test_partial_meal_or_hotel_budget_does_not_become_total(self):
        for text in ('每餐预算100元', '酒店预算300元', '预算只用于餐饮300元'):
            raw = extraction(text, party_size=1, budget_amount=100 if '100' in text else 300,
                             budget_basis='party', budget_coverage='destination')
            result = normalize_intake(raw, TODAY, [text])
            self.assertTrue(result.ready)
            self.assertIsNone(result.destination_budget)
        raw = extraction('每餐预算100元', budget_amount=100)
        result = normalize_intake(raw, TODAY, ['预算稍后再定', '每餐预算100元'])
        self.assertEqual(result.budget_mode, 'deferred')
