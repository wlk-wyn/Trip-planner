"""Cost units, missing data, itinerary nights, and budget comparison scope."""
import unittest
from unittest.mock import AsyncMock
from pydantic import ValidationError

from app.models.schemas import Hotel, Meal, TripCostContext
from app.services.cost_breakdown import build_cost_report, stated_budget, _money
from app.services.schedule_validator import validate_schedule
from eval.run_eval import summarize
from test_schedule import sample
from test_travel_agent import REQUEST


class CostBasisTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        plan = sample(1)
        template = plan.days[0]
        template.attractions[0].ticket_price = 10
        template.attractions[0].cost_status = 'confirmed'
        template.meals = [Meal(name='', type='lunch', restaurant='午餐', location=template.attractions[0].location,
                               estimated_cost=20, cost_status='estimated', cost_source='高德人均参考，未核实')]
        template.hotel = Hotel(name='酒店', estimated_cost=100, cost_status='estimated', location=template.attractions[0].location)
        plan.days = [template.model_copy(deep=True) for _ in range(3)]
        for i, day in enumerate(plan.days):
            day.date, day.day_index = f'2026-10-0{3+i}', i
            day.hotel.estimated_cost = [100, 200, 999][i]
            day.identify_visits()
        plan.end_date = '2026-10-05'
        request = REQUEST.model_copy(update={'end_date': plan.end_date, 'travel_days': 3, 'party_size': 2, 'room_count': 1})
        provider = AsyncMock(return_value={'duration_minutes': 10, 'distance_meters': 500, 'fare': 2})
        await validate_schedule(plan, request, provider)
        return plan, request

    async def test_group_cost_and_checkout_night_are_not_double_counted(self):
        plan, request = await self.fixture()
        before = plan.model_dump()
        report = build_cost_report(plan, request)
        self.assertEqual(report['context'], {'party_size': 2, 'room_count': 1, 'lodging_nights': 2})
        self.assertEqual(report['totals'], {'attractions': 60, 'meals': 120, 'hotels': 300, 'transport': 36})
        self.assertEqual(report['known_total'], 516)
        self.assertEqual(report['unknown_count'], 0)
        self.assertEqual(len([r for r in report['lines'] if r['kind'] == 'hotels']), 2)
        self.assertEqual(plan.model_dump(), before)

    async def test_room_count_is_independent_of_people_and_preview_never_mutates_request(self):
        plan, request = await self.fixture()
        before = request.model_dump()
        report = build_cost_report(plan, request, TripCostContext(party_size=3, room_count=2))
        self.assertEqual(report['totals']['hotels'], 600)
        self.assertEqual(report['known_total'], 924)
        self.assertEqual(request.model_dump(), before)

    async def test_single_day_does_not_charge_the_recommended_hotel(self):
        plan, request = await self.fixture()
        plan.days = plan.days[:1]; plan.end_date = plan.start_date
        report = build_cost_report(plan, request)
        self.assertEqual(report['context']['lodging_nights'], 0)
        self.assertEqual(report['totals']['hotels'], 0)
        self.assertFalse(any(line['kind'] == 'hotels' for line in report['lines']))

    async def test_unknown_is_never_zero_and_confirmed_free_is_valid(self):
        plan, request = await self.fixture()
        place = plan.days[0].attractions[0]
        place.ticket_price, place.cost_status = 0, 'unknown'
        report = build_cost_report(plan, request)
        self.assertEqual(report['unknown_count'], 1)
        self.assertIsNone(report['lines'][0]['amount'])
        place.cost_status = 'confirmed'
        report = build_cost_report(plan, request)
        self.assertEqual(report['unknown_count'], 0)
        self.assertEqual(report['lines'][0]['amount'], 0)

    async def test_missing_overnight_and_inconsistent_timeline_remain_unknown(self):
        plan, request = await self.fixture()
        plan.days[0].hotel = None
        report = build_cost_report(plan, request)
        self.assertTrue(any(line['name'] == '当晚住宿待选择' and line['amount'] is None for line in report['lines']))
        self.assertTrue(any(line['name'] == '当天交通待核实' and line['amount'] is None for line in report['lines']))

    async def test_walking_is_free_but_taxi_quote_is_not_driving_cost(self):
        plan, request = await self.fixture()
        for day in plan.days:
            for leg in day.schedule.segments:
                leg.mode, leg.fare, leg.taxi_cost = 'walking', None, 50
        self.assertEqual(build_cost_report(plan, request)['unknown_count'], 0)
        self.assertEqual(build_cost_report(plan, request)['totals']['transport'], 0)
        for day in plan.days:
            for leg in day.schedule.segments: leg.mode = 'driving'
        report = build_cost_report(plan, request)
        self.assertEqual(report['totals']['transport'], 0)
        self.assertEqual(report['unknown_count'], 9)

    async def test_budget_requires_scope_people_and_rooms_and_never_proves_full_trip_budget(self):
        plan, request = await self.fixture()
        request.free_text_input = '预算500元'
        self.assertEqual(build_cost_report(plan, request)['budget']['status'], 'needs_scope')
        request.destination_budget = 500
        self.assertEqual(build_cost_report(plan, request)['budget']['status'], 'over_reference')
        request.party_size = None
        self.assertEqual(build_cost_report(plan, request)['budget']['status'], 'needs_context')
        request.party_size, request.destination_budget = 2, 1000
        self.assertEqual(build_cost_report(plan, request)['budget']['status'], 'within_listed_reference')
        self.assertIsNone(summarize(plan, request, {'budget': 1000})['constraints']['budget'])
        plan.days[0].meals[0].estimated_cost = None
        self.assertEqual(build_cost_report(plan, request)['budget']['status'], 'incomplete')

    async def test_decimal_currency_and_nonfinite_values(self):
        plan, request = await self.fixture()
        plan.days[0].attractions[0].ticket_price = .105
        report = build_cost_report(plan, request)
        self.assertEqual(report['lines'][0]['unit_price'], .11)
        self.assertEqual(report['lines'][0]['amount'], .22)
        for invalid in (True, -1, float('nan'), float('inf'), 1e308, '100'):
            self.assertIsNone(_money(invalid))

    async def test_deferred_budget_ignores_old_limits_without_hiding_known_costs(self):
        plan, request = await self.fixture()
        request.budget_mode, request.destination_budget, request.free_text_input = 'deferred', 10, '预算500元'
        before = request.model_dump()
        report = build_cost_report(plan, request)
        self.assertEqual(report['budget'], {'status': 'deferred', 'limit': None})
        self.assertEqual(report['known_total'], 516)
        self.assertIsNone(stated_budget(request))
        self.assertEqual(request.model_dump(), before)
        preview = build_cost_report(plan, request, TripCostContext(party_size=2, room_count=1, destination_budget=600))
        self.assertEqual(preview['budget']['status'], 'within_listed_reference')

    def test_single_category_budgets_are_not_whole_destination_limits(self):
        for text in ('每餐预算100元', '酒店预算300元', '预算只用于餐饮300元'):
            self.assertIsNone(stated_budget(REQUEST.model_copy(update={'free_text_input': text})))

    async def test_invalid_or_extreme_date_ranges_do_not_guess_nights_or_expand_unboundedly(self):
        plan, request = await self.fixture()
        for end in ('not-a-date', '2026-10-02', '9999-12-31'):
            plan.end_date = end
            report = build_cost_report(plan, request)
            self.assertIsNone(report['context']['lodging_nights'])
            self.assertTrue(any(line['name'] == '住宿日期待核实' for line in report['lines']))
            self.assertLess(len(report['lines']), 30)

    def test_context_validation_rejects_ambiguous_counts_and_nonfinite_budget(self):
        for invalid in (True, 0, -1, 51, 1.5, '2'):
            for field in ('party_size', 'room_count'):
                with self.assertRaises(ValidationError): TripCostContext(**{field: invalid})
        for invalid in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValidationError): TripCostContext(destination_budget=invalid)
        for text, expected in [('预算 300 元', 300), ('预算1.5万元', 15000), ('预算¥2,000.50', 2000.5), ('预算每人1000', None), ('预算1,00元', None)]:
            self.assertEqual(stated_budget(REQUEST.model_copy(update={'free_text_input': text})), expected)


if __name__ == '__main__':
    unittest.main()
