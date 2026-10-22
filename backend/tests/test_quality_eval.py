import asyncio
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.models.schemas import DaySchedule
from app.services.plan_quality import evaluate_plan
from app.services.plan_checks import run_checks
from app.services.schedule_validator import validate_schedule
from app.services.trip_repository import TripRepository
from app.services import planning_runs as jobs
from eval.run_eval import aggregate, constraint_results, load_cases, run_one, summarize, replay
from test_schedule import sample
from test_travel_agent import REQUEST


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def good(self):
        plan = sample(2)
        for i, place in enumerate(plan.days[0].attractions):
            place.poi_id = f'test-{i}'
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={'duration_minutes': 10, 'distance_meters': 500}))
        return plan

    async def test_valid_draft_is_distinct_from_execution_ready(self):
        plan = await self.good()
        before = plan.model_dump()
        row = summarize(plan, REQUEST, {'max_attractions_per_day': 3})
        self.assertTrue(row['schedule_passed'])
        self.assertTrue(row['reference_checks_passed'])
        self.assertFalse(row['execution_ready'])
        self.assertFalse(row['official_hours_verified'])
        self.assertEqual(plan.model_dump(), before)

    async def test_empty_or_unrelated_timeline_is_not_a_pass(self):
        plan = await self.good()
        plan.days[0].schedule = DaySchedule(status='feasible', window_start='09:00', window_end='20:00', minimum_minutes=0, available_minutes=660)
        self.assertFalse(evaluate_plan(plan, REQUEST)['schedule_passed'])
        plan = await self.good()
        plan.days[0].schedule.segments[0].from_id = 'another-trip'
        self.assertFalse(evaluate_plan(plan, REQUEST)['schedule_passed'])

    async def test_edited_stop_duration_or_fixed_appointment_invalidates_old_timeline(self):
        for field, value in [('visit_duration', 120), ('fixed_start_time', '14:00'), ('name', '另一个地点')]:
            plan = await self.good()
            setattr(plan.days[0].attractions[0], field, value)
            self.assertTrue(any(i['code'] == 'schedule_integrity' for i in run_checks(plan, REQUEST)))

    async def test_time_overlap_bad_clock_or_changed_window_cannot_claim_feasible(self):
        for mutate in [lambda d: setattr(d.schedule.items[1], 'start_time', '09:01'),
                       lambda d: setattr(d.schedule.items[0], 'start_time', '09:99'),
                       lambda d: setattr(d.schedule, 'minimum_minutes', 0),
                       lambda d: setattr(d, 'end_time', '10:00')]:
            plan = await self.good(); mutate(plan.days[0])
            self.assertFalse(evaluate_plan(plan, REQUEST)['schedule_passed'])

    async def test_old_ids_can_be_checked_without_renaming_saved_visits(self):
        plan = await self.good(); day = plan.days[0]
        for i, item in enumerate(day.schedule.items): item.id = f'attraction-{i}'
        day.schedule.segments[0].from_id = 'attraction-0'
        day.schedule.segments[0].to_id = 'attraction-1'
        self.assertTrue(evaluate_plan(plan, REQUEST)['schedule_passed'])

    async def test_wrong_city_or_plan_header_dates_fail(self):
        for field, value in [('city', '北京'), ('end_date', '2026-10-09')]:
            plan = await self.good(); setattr(plan, field, value)
            self.assertFalse(evaluate_plan(plan, REQUEST)['schedule_passed'])

    async def test_missing_costs_never_prove_budget(self):
        plan = await self.good()
        self.assertIsNone(constraint_results(plan, {'budget': 1500})['budget'])
        self.assertEqual(summarize(plan, REQUEST, {'budget': 1500})['constraints_status'], 'unverified')
        plan.days[0].attractions[0].ticket_price = 2000
        plan.days[0].attractions[0].cost_status = 'confirmed'
        self.assertIsNone(constraint_results(plan, {'budget': 1500})['budget'])  # party size still unknown
        request = REQUEST.model_copy(update={'party_size': 1})
        self.assertFalse(constraint_results(plan, {'budget': 1500}, request)['budget'])

    async def test_keywords_and_config_do_not_silently_ignore_requirements(self):
        plan = await self.good()
        self.assertFalse(constraint_results(plan, {'include_all_keywords': ['博物馆', '不存在']})['include_all_keywords'])
        with self.assertRaises(ValueError): constraint_results(plan, {'unimplemented': True})
        with self.assertRaises(ValueError): load_cases(['typo-case'])
        with self.assertRaises(ValueError): load_cases(limit=0)

    async def test_evening_requirement_checks_clock_not_a_night_description(self):
        plan = await self.good()
        plan.days[0].attractions[0].description = '夜景灯光'
        self.assertFalse(constraint_results(plan, {'activity_after':'18:00'})['activity_after'])
        plan.days[0].schedule.items[-1].start_time = '18:00'
        plan.days[0].schedule.items[-1].end_time = '19:00'
        self.assertTrue(constraint_results(plan, {'activity_after':'18:00'})['activity_after'])
        for item in plan.days[0].schedule.items: item.start_time = item.end_time = None
        self.assertIsNone(constraint_results(plan, {'activity_after':'18:00'})['activity_after'])

    async def test_walking_requirement_checks_real_modes_and_unknown_is_not_a_pass(self):
        plan = await self.good(); segment = plan.days[0].schedule.segments[0]
        self.assertFalse(constraint_results(plan, {'allowed_route_modes':['walking']})['allowed_route_modes'])
        segment.mode = 'walking'
        self.assertTrue(constraint_results(plan, {'allowed_route_modes':['walking']})['allowed_route_modes'])
        segment.status, segment.duration_minutes = 'unverified', None
        self.assertIsNone(constraint_results(plan, {'allowed_route_modes':['walking']})['allowed_route_modes'])

    async def test_latency_is_per_case_and_small_samples_do_not_claim_p95(self):
        report = aggregate([{'id': 'one', 'status': 'completed', 'seconds': 10}, {'id': 'one', 'status': 'timeout', 'seconds': 180}, {'id': 'two', 'status': 'completed', 'seconds': 30}])
        self.assertEqual(report['one']['completed'], 1)
        self.assertEqual(report['one']['timeouts'], 1)
        self.assertEqual(report['one']['median_seconds'], 10)
        self.assertIsNone(report['one']['p95_seconds'])
        self.assertEqual(report['two']['median_seconds'], 30)
        report = aggregate([{'id': 'one', 'status': 'completed', 'seconds': value} for value in [10, 20]])
        self.assertEqual(report['one']['median_seconds'], 15)

    async def test_snapshot_replay_does_not_generate_or_rewrite_plan(self):
        plan = await self.good()
        row = summarize(plan, REQUEST, {'max_attractions_per_day': 3})
        row.update(id='replay', plan_snapshot=plan.model_dump(), case_request=REQUEST.model_dump(), case_expect={'max_attractions_per_day': 3})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            text = json.dumps([row], ensure_ascii=False)
            path.write_text(text, encoding='utf-8')
            with patch('eval.run_eval.write', return_value=Path(folder)/'result.md'):
                result = replay(path)
            self.assertFalse(result[0]['assessment_changed'])
            self.assertEqual(path.read_text(encoding='utf-8'), text)


class FullPipelineEvalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await jobs.shutdown_runs()

    async def test_evaluation_uses_persistent_creation_and_temporary_save(self):
        async def generate(request, *_):
            plan = sample(1); day = plan.days[0]
            plan.city = request.city
            plan.start_date = plan.end_date = day.date = request.start_date
            await validate_schedule(plan, request, AsyncMock())
            return plan
        case = {'id': 'synthetic', 'city': '杭州', 'days': 1, 'expect': {'max_attractions_per_day': 3}}
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / 'eval.sqlite3')
            with patch.object(jobs, '_generate', side_effect=generate), patch.object(jobs, '_estimate_tickets', new=AsyncMock()) as prices:
                row = await run_one(case, 1, 5, repo, 'synthetic-owner')
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(row['saved_version'], 1)
            self.assertEqual(row['pipeline'], 'persistent_create_with_prices_and_save')
            self.assertEqual(len(repo.list('synthetic-owner')), 1)
            prices.assert_awaited_once()

    async def test_evaluation_timeout_cancels_and_does_not_save_partial_plan(self):
        async def never(*_): await asyncio.Event().wait()
        case = {'id': 'slow', 'city': '杭州', 'days': 1}
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / 'eval.sqlite3')
            with patch.object(jobs, '_generate', side_effect=never):
                row = await run_one(case, 1, .03, repo, 'synthetic-owner')
            self.assertEqual(row['status'], 'timeout')
            self.assertFalse(repo.list('synthetic-owner'))
            with repo.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM planning_runs').fetchone()['status'], 'cancelled')

    async def test_slow_progress_write_does_not_overwrite_later_progress(self):
        entered, release = asyncio.Event(), asyncio.Event()
        real_thread = asyncio.to_thread
        committed = []
        async def reordered(fn, *args, **kwargs):
            stage = args[0] if fn.__name__ == 'write_progress' else ''
            if stage == 'route-1':
                entered.set(); await release.wait()
            result = await real_thread(fn, *args, **kwargs)
            if stage.startswith('route-'): committed.append(stage)
            return result
        async def generate(request, _run, progress):
            first = asyncio.create_task(progress('route-1', 1))
            await entered.wait()
            second = asyncio.create_task(progress('route-2', 2))
            await asyncio.sleep(.01)
            release.set()
            await asyncio.gather(first, second)
            plan = sample(1)
            plan.start_date = plan.end_date = plan.days[0].date = request.start_date
            return plan
        case = {'id': 'progress-order', 'city': '杭州', 'days': 1}
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / 'eval.sqlite3')
            with patch.object(jobs, '_generate', side_effect=generate), patch.object(jobs, '_estimate_tickets', new=AsyncMock()), patch.object(jobs.asyncio, 'to_thread', side_effect=reordered):
                row = await run_one(case, 1, 5, repo, 'synthetic-owner')
            self.assertEqual(row['status'], 'completed')
            self.assertEqual(committed, ['route-1', 'route-2'])
