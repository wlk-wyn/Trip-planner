import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.opening_hours import parse_hours, parse_place_hours, hours_issues
from app.services.schedule_validator import validate_schedule, stops_for_day
from app.services.schedule_solver import solve_order
from app.services.plan_quality import evaluate_plan
from test_schedule import sample
from test_travel_agent import REQUEST


class HoursParsingTests(unittest.TestCase):
    def test_whole_month_seasons_use_visit_date_and_keep_last_entry_distinct(self):
        text = ('1月至3月,11月至12月 周一至周日 06:30-20:00 最晚进入19:30；'
                '4月至10月 周一至周日 06:00-21:00 最晚进入20:30')
        for day, window, last in (('2026-03-31', (390,1200),1170), ('2026-04-01',(360,1260),1230),
                                  ('2026-10-31',(360,1260),1230), ('2026-11-01',(390,1200),1170)):
            rule = parse_hours(text, day)
            self.assertEqual(rule.windows, (window,))
            self.assertEqual(rule.last_admission, last)
        for day in ('2028-02-29', '2027-02-28', '2026-12-31', '2026-01-01'):
            self.assertEqual(parse_hours(text, day).windows, ((390,1200),))
        self.assertEqual(parse_hours('11月至3月09:00-17:00；4月至10月09:00-18:00','2026-01-20').windows, ((540,1020),))

    def test_month_scope_ambiguity_invalid_months_and_inner_venues_stay_unknown(self):
        for text in ('13月至15月09:00-17:00',
                     '1月至6月09:00-17:00；6月至12月08:00-18:00',
                     '1月至3月,11月至12月06:30-22:00 最晚进入21:00 园中园08:00-17:00(16:30停止入园)',
                     '1月至12月09:00-17:00 最晚进入18:00',
                     '1月至12月09:00-17:00；临时展览另行通知'):
            self.assertFalse(parse_hours(text,'2026-06-01').understood)

    def test_weekday_opening_is_not_erased_by_an_unrelated_monday_holiday_exception(self):
        text = '周二至周日08:30-17:00(16:10停止检票),周一闭馆(法定节假日除外)'
        for day in ('2026-10-20', '2026-10-21', '2027-10-19'):
            rule = parse_hours(text, day)
            self.assertTrue(rule.understood)
            self.assertEqual(rule.windows, ((510, 1020),))
            self.assertEqual(rule.last_admission, 970)
        self.assertFalse(parse_hours(text, '2026-10-19').understood)
        self.assertFalse(parse_hours(text, '2026-10-05', calendar_known=True).understood)
        self.assertTrue(parse_hours(text, '2026-10-19', calendar_known=True).closed)
        # Ordinary split hours retain their weekday scope across a comma.
        self.assertEqual(parse_hours('周二至周日09:00-12:00,13:00-17:00；周一闭馆', '2026-10-20').windows,
                         ((540, 720), (780, 1020)))
        self.assertFalse(parse_hours(text + '；临时活动另行通知', '2026-10-20').understood)

    def test_art_museum_weekly_closure_survives_unsupported_seasonal_hours(self):
        place = {'name': '中国美术馆', 'poi_id': 'B000A7BC2S',
                 'location': {'longitude': 116.4079, 'latitude': 39.9255},
                 'open_hours': '8月至12月 周二至周日 09:00-17:00 最晚进入16:00；'
                               '07-15至08-30 周二至周五 09:00-18:00；周一闭馆（法定节假日除外）'}
        self.assertTrue(parse_place_hours(place, '2026-10-19').closed)
        for day in ('2026-10-05', '2027-10-18', '2026-10-20'):
            self.assertFalse(parse_place_hours(place, day).understood)
        for change in ({'poi_id': 'other'}, {'name': '其他美术馆'},
                       {'location': {'longitude': 116.2, 'latitude': 39.9}}):
            self.assertFalse(parse_place_hours({**place, **change}, '2026-10-19').understood)

    def test_complete_last_admission_is_distinct_from_closing_time(self):
        rule=parse_hours('09:00-17:00，16:00停止入馆','2026-10-20')
        self.assertEqual(rule.windows,((540,1020),))
        self.assertEqual(rule.last_admission,960)
        for value in ('09:00-17:00 18:00停止入馆','09:00-17:00 99:00停止入馆','09:00-17:00 16:00停止入馆，活动期间例外'):
            self.assertFalse(parse_hours(value,'2026-10-20').understood)

    def test_seasonal_hours_are_parsed_from_provider_text_and_exceptions_stay_scoped(self):
        import json
        data=json.loads((Path(__file__).parent/'fixtures/seasonal_hours_failure.json').read_text(encoding='utf-8'))
        palace=data['plan_snapshot']['days'][0]['attractions'][0]
        national=next(p for p in data['plan_snapshot']['days'][1]['attractions'] if p['name']=='中国国家博物馆')
        for place, day, window, cutoff in ((palace,'2026-10-20',(510,1020),960),
            (palace,'2026-11-19',(510,990),930),(national,'2026-10-21',(540,1050),990),
            (national,'2026-11-19',(540,1020),960)):
            rule=parse_place_hours(place,day)
            self.assertEqual(rule.windows,(window,));self.assertEqual(rule.last_admission,cutoff)
        for day in ('2026-10-05','2027-10-19'):
            self.assertFalse(parse_place_hours(palace,day).understood)
        self.assertFalse(parse_place_hours({**palace,'poi_id':'unknown'},'2026-10-20').understood)

    def test_unknown_seasonal_residue_and_overlapping_seasons_do_not_get_guessed(self):
        for text in ('4月1日至10月31日周二至周日09:00-17:00；活动期间延长',
                     '4月1日至10月31日周二至周日09:00-17:00；5月1日至11月30日周二至周日08:00-18:00'):
            self.assertFalse(parse_hours(text,'2026-10-20').understood)

    def test_real_snapshot_no_longer_passes_after_museum_closing_time(self):
        import json
        from app.models.schemas import TripPlan,TripRequest
        from eval.run_eval import summarize
        data=json.loads((Path(__file__).parent/'fixtures/seasonal_hours_failure.json').read_text(encoding='utf-8'))
        score=summarize(TripPlan(**data['plan_snapshot']),TripRequest(**data['case_request']),data['case_expect'])
        self.assertFalse(score['reference_checks_passed'])
        self.assertEqual(score['opening_hours_conflicts'],2)

    def test_holiday_calendar_requires_known_identity_and_location(self):
        place = {'name': '故宫博物院', 'poi_id': 'B000A8UIN8',
                 'location': {'longitude': 116.397029, 'latitude': 39.917839},
                 'open_hours': '周一闭馆（节假日除外）'}
        self.assertTrue(parse_place_hours(place, '2026-10-19').closed)
        raw = {k: v for k, v in place.items() if k != 'location'}
        self.assertTrue(parse_place_hours({**raw, 'coordinates': [116.397029, 39.917839]}, '2026-10-19').closed)
        self.assertFalse(parse_place_hours(place, '2026-10-05').understood)
        for change in ({'poi_id': 'unidentified'}, {'name': '地方博物馆'},
                       {'location': {'longitude': 100, 'latitude': 25}},
                       {'location': {'longitude': 'invalid', 'latitude': 25}}):
            with self.subTest(change=change):
                self.assertFalse(parse_place_hours({**place, **change}, '2026-10-19').understood)

    def test_weekly_closure_and_daily_default(self):
        value = "09:00-17:00；周一全天不开放"
        self.assertTrue(parse_hours(value, "2026-10-19").closed)
        self.assertEqual(parse_hours(value, "2026-10-20").windows, ((540, 1020),))

    def test_weekly_ranges_split_hours_and_wrap(self):
        self.assertEqual(parse_hours("周二至周日 09:00-12:00,13:00-17:00；周一闭馆", "2026-10-20").windows, ((540, 720), (780, 1020)))
        self.assertTrue(parse_hours("周六至周二休馆", "2026-10-19").closed)
        self.assertFalse(parse_hours("周二至周日 09:00-17:00", "2026-10-19").understood)

    def test_does_not_guess_exceptions_negation_or_bad_clock(self):
        for value in [None, "周一闭馆（节假日除外）", "周一不闭馆", "周一正常开放，周二闭馆", "夏季09:00-18:00", "11:00-02:00", "09:90-17:00", "09:00-24:30"]:
            with self.subTest(value=value):
                self.assertFalse(parse_hours(value, "2026-10-19").understood)

    def test_ordinary_monday_closure_is_not_lost_among_seasonal_or_holiday_text(self):
        for value in ('周一全天关闭(法定节假日除外)；11月1日至次年5月31日周二至周日09:00-17:00(16:00停止入馆)',
                      '旺季4月1日至10月31日 周二至周日08:30-17:00 16:00停止检票，周一全天关闭，节假日营业时间以官方通知为准'):
            self.assertTrue(parse_hours(value,'2026-10-19',calendar_known=True).closed)
            self.assertFalse(parse_hours(value,'2026-10-20').understood)

    def test_holiday_exceptions_and_uncovered_years_are_not_assumed_open_or_closed(self):
        for day in ('2026-10-05','2026-04-06','2026-02-23','2027-10-18'):
            self.assertFalse(parse_hours('周一闭馆（节假日除外）',day,calendar_known=True).understood)
        self.assertTrue(parse_hours('周一闭馆（节假日除外）','2026-10-19',calendar_known=True).closed)
        self.assertFalse(parse_hours('冬季周一闭馆','2026-10-19').understood)
        self.assertFalse(parse_hours('周一闭馆，遇特殊活动开放','2026-10-19').understood)

    def test_explicit_weekday_range_excludes_automatic_scheduling_without_claiming_closure(self):
        rule=parse_hours('周二至周日08:30-15:30','2026-10-19')
        self.assertFalse(rule.understood);self.assertFalse(rule.closed);self.assertTrue(rule.outside_weekday)

    def test_closed_and_specific_open_is_ambiguous(self):
        self.assertFalse(parse_hours("周一闭馆；周一09:00-17:00", "2026-10-19").understood)
        self.assertEqual(parse_hours("全天开放", "2026-10-19").windows, ((0, 1440),))


class ScheduleHoursTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_admission_is_enforced_in_validator_read_checks_and_solver(self):
        plan=sample(1);place=plan.days[0].attractions[0]
        place.open_hours='09:00-17:00 16:00停止入馆'
        place.fixed_start_time='16:00';place.visit_duration=30
        schedule=await self.checked(plan)
        self.assertEqual(schedule.status,'conflict')
        self.assertTrue(any('停止入馆' in c for c in schedule.conflicts))
        self.assertTrue(any('停止入馆' in i['message'] for i in hours_issues(plan.days[0])))
        self.assertIsNone(solve_order(stops_for_day(plan.days[0]),{},540,1200,15,seconds=1,visit_date=plan.days[0].date))

    async def checked(self, plan):
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 10, "distance_meters": 500}))
        return plan.days[0].schedule

    async def test_wait_for_opening_counts_against_day_budget(self):
        plan = sample(1)
        place = plan.days[0].attractions[0]
        place.open_hours = "10:00-17:00"
        place.visit_duration = 90
        schedule = await self.checked(plan)
        self.assertEqual(schedule.items[0].start_time, "10:00")
        self.assertEqual(schedule.minimum_minutes, 150)
        self.assertEqual(schedule.status, "feasible")

    async def test_full_visit_must_fit_and_fixed_time_is_not_shifted(self):
        plan = sample(1)
        place = plan.days[0].attractions[0]
        place.open_hours = "09:00-10:00"
        place.visit_duration = 90
        self.assertEqual((await self.checked(plan)).status, "conflict")
        place.open_hours = "10:00-17:00"; place.fixed_start_time = "09:00"
        schedule = await self.checked(plan)
        self.assertEqual(schedule.status, "conflict")
        self.assertEqual(schedule.items[0].start_time, "09:00")

    async def test_midday_break_wait_and_unknown_exception(self):
        plan = sample(1); place = plan.days[0].attractions[0]
        plan.days[0].start_time = "11:30"; place.visit_duration = 90
        place.open_hours = "09:00-12:00,13:00-17:00"
        schedule = await self.checked(plan)
        self.assertEqual(schedule.items[0].start_time, "13:00")
        self.assertEqual(schedule.minimum_minutes, 180)
        place.open_hours = "09:00-17:00，节假日可能调整"
        schedule = await self.checked(plan)
        self.assertEqual(schedule.items[0].start_time, "11:30")
        self.assertTrue(any("未支持" in value for value in schedule.warnings))

    async def test_unknown_travel_does_not_create_false_opening_conflict(self):
        plan = sample(2); plan.days[0].attractions[1].open_hours = "09:00-10:00"
        await validate_schedule(plan, REQUEST, AsyncMock(side_effect=RuntimeError("offline")))
        self.assertEqual(plan.days[0].schedule.status, "unverified")
        self.assertFalse(any("营业时段内" in value for value in plan.days[0].schedule.conflicts))

    async def test_read_checks_detect_old_schedule_after_hours_change(self):
        plan = sample(1); await self.checked(plan)
        plan.days[0].attractions[0].open_hours = "15:00-16:00"
        self.assertTrue(any(i["code"] == "opening_hours_conflict" for i in hours_issues(plan.days[0])))
        self.assertFalse(evaluate_plan(plan, REQUEST)["schedule_passed"])

    async def test_quality_rejects_unverified_zero_edges_and_rule_errors(self):
        plan = sample(1); schedule = await self.checked(plan)
        schedule.status = "unverified"
        self.assertFalse(evaluate_plan(plan, REQUEST)["schedule_passed"])
        schedule.status = "feasible"
        plan.days[0].attractions[0].location.longitude = 2.35
        self.assertFalse(evaluate_plan(plan, REQUEST)["schedule_passed"])

    async def test_solver_respects_registered_window(self):
        plan = sample(1); place = plan.days[0].attractions[0]
        place.open_hours = "13:00-16:00"; place.visit_duration = 90
        stops = stops_for_day(plan.days[0])
        result = solve_order(stops, {}, 540, 1200, 15, seconds=1, visit_date=plan.days[0].date)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result[1][stops[0]["id"]], 780)
        place.fixed_start_time = "09:00"
        self.assertIsNone(solve_order(stops_for_day(plan.days[0]), {}, 540, 1200, 15, seconds=1, visit_date=plan.days[0].date))

    async def test_enrichment_rechecks_new_hours_before_saving_one_version(self):
        from app.api.routes import saved_trips
        from app.services.trip_repository import TripRepository
        plan = sample(1); await self.checked(plan)
        async def enrich(candidate, *_):
            candidate.days[0].attractions[0].open_hours = "15:00-16:00"
            return {"looked_up": 1, "found": 1, "failed": 0, "tickets_estimated": 0}
        async def validate(candidate, request):
            return await validate_schedule(candidate, request, AsyncMock(return_value={"duration_minutes": 10, "distance_meters": 500}))
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / "trips.sqlite3")
            current = repo.create("test-owner", REQUEST.model_dump(), plan.model_dump())
            with patch('app.services.place_facts.enrich_plan', side_effect=enrich), patch.object(saved_trips, 'get_langgraph_planner', return_value=SimpleNamespace(fast_llm=None)), patch.object(saved_trips, 'validate_schedule', side_effect=validate) as check:
                result = await saved_trips.enrich_trip(current['id'], saved_trips.ValidateTrip(base_version=1), owner="test-owner", repo=repo)
            self.assertEqual(result['version'], 2)
            self.assertEqual(result['plan']['days'][0]['schedule']['status'], 'conflict')
            self.assertEqual(len(repo.versions("test-owner", current['id'])), 2)
            check.assert_awaited_once()

    async def test_price_only_enrichment_keeps_schedule_without_extra_route_queries(self):
        from app.api.routes import saved_trips
        from app.services.trip_repository import TripRepository
        plan = sample(1); await self.checked(plan)
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / "trips.sqlite3")
            current = repo.create("test-owner", REQUEST.model_dump(), plan.model_dump())
            with patch('app.services.place_facts.enrich_plan', return_value={}), patch.object(saved_trips, 'get_langgraph_planner', return_value=SimpleNamespace(fast_llm=None)), patch.object(saved_trips, 'validate_schedule') as check:
                result = await saved_trips.enrich_trip(current['id'], saved_trips.ValidateTrip(base_version=1), owner="test-owner", repo=repo)
            self.assertEqual(result['plan']['days'][0]['schedule'], current['plan']['days'][0]['schedule'])
            check.assert_not_awaited()
