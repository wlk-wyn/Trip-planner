import asyncio
import unittest
from datetime import date
from app.services.observability import usage_scope, record_event
from eval.run_eval import case_request
from eval.load_eval import load_cases


class LoadMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_scopes_do_not_mix_provider_counts(self):
        async def work(amount):
            with usage_scope() as totals:
                await asyncio.sleep(0)
                record_event('route_memory_hits',amount)
                record_event('amap_route_http_seconds',.2)
                return totals
        first,second=await asyncio.gather(work(2),work(7))
        self.assertEqual(first['events']['route_memory_hits'],2)
        self.assertEqual(second['events']['route_memory_hits'],7)
        record_event('outside_scope')
        self.assertNotIn('outside_scope',first['events'])

    async def test_eval_request_keeps_party_rooms_and_budget_scope(self):
        case={'city':'西安','days':3,'party_size':2,'room_count':1,'destination_budget':1500,'budget_mode':'active'}
        request=case_request(case)
        self.assertEqual((request.party_size,request.room_count,request.destination_budget),(2,1,1500))
        self.assertEqual((date.fromisoformat(request.end_date)-date.fromisoformat(request.start_date)).days,2)

    async def test_load_cases_cover_multiple_lengths_and_explicit_constraints(self):
        rows=load_cases()
        self.assertEqual({r['days'] for r in rows},{1,2,3,7})
        self.assertTrue(all(r.get('party_size') and r.get('room_count') and r.get('expect') for r in rows))
