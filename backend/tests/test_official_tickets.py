"""Official page parsing, visit identity, season scope and honest failure paths."""
import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from langchain_core.messages import AIMessage

from app.models.schemas import Attraction, Location, TripPlan
from app.services import official_tickets as tickets
from app.services.cost_breakdown import build_cost_report
from app.services.fact_evidence import fact_report, fact_state, needs_refresh, reconcile_manual_facts
from app.services.place_facts import fill_ticket_prices
from test_schedule import sample

PALACE = '<h1>故宫博物院票务政策</h1><p>每年4月1日至10月31日为旺季，大门票60元/人；</p><p>每年11月1日至次年3月31日为淡季，大门票40元/人；</p><p>珍宝馆门票：10元/人；钟表馆门票：10元/人；</p>'
BMY = '<h1>秦始皇兵马俑博物馆</h1><p>Standard Adult Ticket: 120 RMB</p><p>Student Half-Price Ticket: 60 RMB</p>'


def plan_for(key='palace', day='2026-10-31'):
    source = next(s for s in tickets.SOURCES if s.key == key)
    left, bottom, right, top = source.bounds
    place = Attraction(name=source.names[0], poi_id=source.poi_id, address='公开示例地址',
        location=Location(longitude=(left+right)/2, latitude=(bottom+top)/2), visit_duration=120, description='')
    plan = sample(1)
    plan.city = source.city
    plan.start_date = plan.end_date = plan.days[0].date = day
    plan.days[0].attractions = [place]
    plan.days[0].meals = []
    plan.days[0].hotel = None
    plan.days[0].identify_visits()
    return plan


def policy(source, stamp=None):
    return tickets.parse_policy(source, PALACE if source.key=='palace' else BMY,
                                stamp or datetime.now(timezone.utc).isoformat())


class OfficialTicketsTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_adult_rates_and_season_boundaries(self):
        palace, bmy = (policy(source) for source in tickets.SOURCES)
        self.assertEqual([palace.price_on(date.fromisoformat(d)) for d in
            ['2026-03-31','2026-04-01','2026-10-31','2026-11-01','2027-01-10']], [40,60,60,40,40])
        self.assertEqual([bmy.price_on(date(2026,m,1)) for m in (1,6,12)], [120,120,120])
        self.assertEqual(len(palace.page_sha256),64)

    def test_missing_conflicting_or_script_prices_do_not_become_a_policy(self):
        source = tickets.SOURCES[0]
        for text in ('故宫博物院 珍宝馆门票10元/人', PALACE.replace('淡季','未知季'),
                     PALACE+PALACE.replace('60元','80元'), '<script>'+PALACE+'</script>'):
            self.assertIsNone(tickets.parse_policy(source,text,'2026-10-16T00:00:00Z'))
        self.assertIsNone(tickets.parse_policy(tickets.SOURCES[1],BMY.replace('120','9999'),'2026-10-16T00:00:00Z'))

    def test_only_exact_venue_city_id_name_and_area_are_eligible(self):
        plan = plan_for()
        place = plan.days[0].attractions[0]
        self.assertIsNotNone(tickets.matching_source(plan.city,place))
        for changes in ({'name':'故宫珍宝馆'}, {'poi_id':'different'}, {'location':Location(longitude=121,latitude=31)}):
            self.assertIsNone(tickets.matching_source(plan.city,place.model_copy(update=changes)))
        self.assertIsNone(tickets.matching_source('台北',place))

    async def test_shared_page_used_once_but_prices_are_bound_to_each_visit_date(self):
        plan = plan_for()
        day = plan.days[0].model_copy(deep=True)
        day.date = '2026-11-01'; day.day_index=1
        day.attractions[0].item_id += '-next'
        plan.days.append(day)
        lookup = AsyncMock(side_effect=lambda source: policy(source))
        summary = await tickets.refresh_official_tickets(plan,lookup)
        self.assertEqual(summary, {'official_sources':1,'official_updated':2,'official_failed':0})
        self.assertEqual(lookup.await_count,1)
        self.assertEqual([d.attractions[0].ticket_price for d in plan.days],[60,40])
        restored = TripPlan.model_validate_json(plan.model_dump_json())
        evidence = restored.days[0].attractions[0].price_evidence
        self.assertEqual((evidence.source,evidence.applies_on),('official','2026-10-31'))
        self.assertEqual(evidence.source_url,tickets.SOURCES[0].url)
        self.assertEqual(len(evidence.document_hash),64)

    async def test_confirmed_user_price_is_never_overwritten_or_queried(self):
        plan=plan_for(); place=plan.days[0].attractions[0]
        place.ticket_price,place.cost_status,place.cost_source=20,'confirmed','用户填写'
        lookup=AsyncMock()
        summary=await tickets.refresh_official_tickets(plan,lookup)
        lookup.assert_not_awaited()
        self.assertEqual((place.ticket_price,summary['official_updated']),(20,0))

    async def test_recent_official_reference_does_not_refetch_or_freshen_its_time(self):
        plan=plan_for(); lookup=AsyncMock(side_effect=lambda source: policy(source))
        await tickets.refresh_official_tickets(plan,lookup)
        stamp=plan.days[0].attractions[0].price_evidence.recorded_at
        lookup.reset_mock()
        await tickets.refresh_official_tickets(plan,lookup)
        lookup.assert_not_awaited()
        self.assertEqual(plan.days[0].attractions[0].price_evidence.recorded_at,stamp)

    async def test_date_move_invalidates_price_until_recomputed_without_relabeling_as_user(self):
        plan=plan_for()
        stamp=datetime.now(timezone.utc).isoformat()
        lookup=AsyncMock(side_effect=lambda source: policy(source,stamp))
        await tickets.refresh_official_tickets(plan,lookup)
        moved=plan.model_copy(deep=True); moved.days[0].date='2026-11-01'
        reconcile_manual_facts(moved,plan)
        place=moved.days[0].attractions[0]
        self.assertEqual(place.price_evidence.source,'official')
        self.assertEqual(fact_state(place,'price',visit_date='2026-11-01')['status'],'scope_mismatch')
        self.assertTrue(needs_refresh(place,'price',visit_date='2026-11-01'))
        line=build_cost_report(moved)['lines'][0]
        self.assertIsNone(line['amount'])
        self.assertEqual(fact_report(moved)['scope_mismatch'],1)
        await tickets.refresh_official_tickets(moved,lookup)
        self.assertEqual(place.ticket_price,40)
        self.assertEqual(place.price_evidence.recorded_at,stamp)

    async def test_failed_or_wrong_source_keeps_existing_estimate_and_reports_failure(self):
        for lookup in (AsyncMock(return_value=None), AsyncMock(side_effect=RuntimeError('offline')),
                       AsyncMock(return_value=policy(tickets.SOURCES[1]))):
            plan=plan_for(); place=plan.days[0].attractions[0]
            place.ticket_price,place.cost_status=30,'estimated'
            summary=await tickets.refresh_official_tickets(plan,lookup)
            self.assertEqual((place.ticket_price,summary['official_failed']),(30,1))

    async def test_official_success_excludes_venue_from_model_estimation(self):
        plan=plan_for()
        llm=SimpleNamespace(ainvoke=AsyncMock())
        summary=await fill_ticket_prices(plan,llm,AsyncMock(side_effect=lambda source:policy(source)))
        llm.ainvoke.assert_not_awaited()
        self.assertEqual((summary['official_updated'],summary['tickets_estimated']),(1,0))

    async def test_official_failure_retains_explicit_model_fallback_label(self):
        plan=plan_for(); place=plan.days[0].attractions[0]
        llm=SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='{"'+place.item_id+'":60}')))
        summary=await fill_ticket_prices(plan,llm,AsyncMock(return_value=None))
        self.assertEqual((summary['official_failed'],summary['tickets_estimated']),(1,1))
        self.assertEqual(place.price_evidence.source,'model')

    async def test_stale_official_reference_refreshes_and_invalid_identity_cannot_price(self):
        plan=plan_for(); old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        await tickets.refresh_official_tickets(plan,AsyncMock(side_effect=lambda source:policy(source,old)))
        place=plan.days[0].attractions[0]
        self.assertTrue(needs_refresh(place,'price',visit_date=plan.days[0].date))
        await tickets.refresh_official_tickets(plan,AsyncMock(side_effect=lambda source:policy(source)))
        place.poi_id='replaced-id'
        self.assertIsNone(build_cost_report(plan)['lines'][0]['amount'])

    async def test_cancellation_propagates_without_a_partial_ticket_commit(self):
        plan=plan_for()
        with self.assertRaises(asyncio.CancelledError):
            await tickets.refresh_official_tickets(plan,AsyncMock(side_effect=asyncio.CancelledError))
        self.assertIsNone(plan.days[0].attractions[0].ticket_price)

    async def test_http_cache_keeps_original_query_time_and_never_follows_redirect(self):
        client_type=httpx.AsyncClient
        calls=[]
        def reply(request):
            calls.append(str(request.url))
            return httpx.Response(200,text=PALACE,headers={'content-type':'text/html; charset=utf-8'})
        transport=httpx.MockTransport(reply)
        def client(**kwargs): return client_type(transport=transport,**kwargs)
        with patch.object(tickets,'_cache',{}),patch.object(tickets.httpx,'AsyncClient',side_effect=client):
            first=await tickets.load_policy(tickets.SOURCES[0])
            again=await tickets.load_policy(tickets.SOURCES[0])
            self.assertEqual((first.observed_at,first.page_sha256),(again.observed_at,again.page_sha256))
            self.assertEqual(len(calls),1)
        def redirected(request):return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
        transport=httpx.MockTransport(redirected)
        with patch.object(tickets,'_cache',{}),patch.object(tickets.httpx,'AsyncClient',side_effect=client):
            self.assertIsNone(await tickets.load_policy(tickets.SOURCES[0]))

    async def test_slow_page_is_cancelled_at_deadline_and_keeps_price_unknown(self):
        plan=plan_for()
        async def slow(source):
            await asyncio.sleep(1)
            return policy(source)
        with patch.object(tickets,'LOOKUP_TIMEOUT',.001):
            summary=await tickets.refresh_official_tickets(plan,slow)
        self.assertEqual(summary['official_failed'],1)
        self.assertIsNone(plan.days[0].attractions[0].ticket_price)

    async def test_non_html_and_oversized_pages_are_not_parsed_and_failure_is_briefly_cached(self):
        client_type=httpx.AsyncClient
        for content_type,limit in [('application/json',2000),('text/html',16)]:
            calls=[]
            def reply(request):
                calls.append(str(request.url))
                return httpx.Response(200,text=PALACE,headers={'content-type':content_type})
            transport=httpx.MockTransport(reply)
            def client(**kwargs):return client_type(transport=transport,**kwargs)
            with patch.object(tickets,'_cache',{}),patch.object(tickets,'MAX_PAGE_BYTES',limit),patch.object(tickets.httpx,'AsyncClient',side_effect=client):
                self.assertIsNone(await tickets.load_policy(tickets.SOURCES[0]))
                # A failed response never manufactures an amount or evidence.
                self.assertIsNone(await tickets.load_policy(tickets.SOURCES[0]))
                self.assertEqual(len(calls),1)

    async def test_concurrent_readers_share_page_and_one_cancel_does_not_cancel_other(self):
        started,release=asyncio.Event(),asyncio.Event()
        async def fetch(source):
            started.set()
            await release.wait()
            return policy(source)
        query=AsyncMock(side_effect=fetch)
        with patch.object(tickets,'_cache',{}),patch.object(tickets,'_flights',{}),patch.object(tickets,'_fetch_policy',query):
            first=asyncio.create_task(tickets.load_policy(tickets.SOURCES[0]))
            second=asyncio.create_task(tickets.load_policy(tickets.SOURCES[0]))
            await started.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            self.assertFalse(second.done())
            release.set()
            self.assertIsNotNone(await second)
            self.assertEqual(query.await_count,1)
            self.assertFalse(tickets._flights)

    async def test_last_cancel_releases_http_work_and_allows_retry(self):
        started,cancelled=asyncio.Event(),asyncio.Event()
        async def fetch(source):
            started.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        with patch.object(tickets,'_cache',{}),patch.object(tickets,'_flights',{}),patch.object(tickets,'_fetch_policy',side_effect=fetch):
            task=asyncio.create_task(tickets.load_policy(tickets.SOURCES[0]))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(cancelled.is_set())
            self.assertFalse(tickets._flights)
            with patch.object(tickets,'_fetch_policy',new=AsyncMock(return_value=policy(tickets.SOURCES[0]))) as retry:
                self.assertIsNotNone(await tickets.load_policy(tickets.SOURCES[0]))
                self.assertEqual(retry.await_count,1)
