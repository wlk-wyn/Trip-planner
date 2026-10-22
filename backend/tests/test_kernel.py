import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from langchain_core.messages import AIMessage
from app.models.schemas import TripPlan, PlanPatch, Meal, Hotel
from app.services.plan_patch import apply_patch, validate_changed_days
from app.services.schedule_solver import optimize_day
from app.services.planning_runs import start_run, read_run, cancel_run, execute, _tasks
from app.services.trip_repository import TripRepository, VersionConflict
from app.services.schedule_validator import validate_schedule
from test_travel_agent import REQUEST, PLAN, make_agent, FakeModel
from test_schedule import sample
from test_saved_trips import OWNER, OTHER


class KernelTests(unittest.IsolatedAsyncioTestCase):
    def test_all_place_kinds_keep_identity_and_legacy_identity_is_stable(self):
        raw=copy.deepcopy(PLAN)
        raw["days"][0]["meals"]=[dict(type="lunch",name="分店",poi_id="meal-1")]
        raw["days"][0]["hotel"]=dict(name="分店",poi_id="hotel-1")
        first=TripPlan(**raw); second=TripPlan(**raw)
        self.assertEqual(first,second)
        self.assertEqual(first.days[0].meals[0].place_id,"amap:meal-1")
        self.assertEqual(first.days[0].hotel.place_id,"amap:hotel-1")
        day=first.days[0]; ids=[p.item_id for p in [*day.attractions,*day.meals,day.hotel]]
        self.assertEqual(len(ids),len(set(ids)))

    async def test_patch_replaces_only_requested_restaurant_and_retains_visit_id(self):
        current=sample(); day=current.days[0]
        day.meals=[Meal(type="lunch",name="餐厅",poi_id="old")]; day.identify_visits()
        original=current.model_copy(deep=True)
        op={"action":"replace","date":day.date,"kind":"meal","item_id":day.meals[0].item_id,"poi_id":"new"}
        result,affected=apply_patch(current,PlanPatch(operations=[op]),{"restaurants_info":json.dumps([{"poi_id":"new","name":"新餐厅","address":"真实地址","coordinates":[120.2,30.2]}])})
        self.assertEqual(result.days[0].meals[0].poi_id,"new")
        self.assertEqual(result.days[0].meals[0].item_id,day.meals[0].item_id)
        self.assertEqual(current,original)
        self.assertEqual(result.days[0].attractions,current.days[0].attractions)
        self.assertEqual(affected,{0})
        with self.assertRaisesRegex(ValueError,"真实 POI"):
            apply_patch(current,PlanPatch(operations=[op]),{})
        with self.assertRaisesRegex(ValueError,"日期范围"):
            apply_patch(current,PlanPatch(operations=[op]),{},set())

    def test_explicit_cross_day_move_preserves_identity_and_scope(self):
        raw=sample(2).model_dump(); raw["days"][0]["attractions"][0]["item_id"]="moving"
        second=copy.deepcopy(raw["days"][0]);second.update(date="2026-10-04",day_index=1)
        for index,p in enumerate(second["attractions"]):p["item_id"]="target-"+str(index)
        raw["days"].append(second);current=TripPlan(**raw)
        operations=[dict(action="move_visit",date="2026-10-03",target_date="2026-10-04",item_id="moving")]
        result,affected=apply_patch(current,PlanPatch(operations=operations),{}, {0,1})
        self.assertEqual(affected,{0,1})
        self.assertEqual(len(result.days[0].attractions),1)
        self.assertEqual(result.days[1].attractions[-1].item_id,"moving")
        with self.assertRaisesRegex(ValueError,"跨日移动"):
            apply_patch(current,PlanPatch(operations=operations),{}, {0})

    def test_locked_or_fixed_visits_cannot_be_removed(self):
        for key,value in [("locked",True),("fixed_start_time","11:00")]:
            current=sample(); place=current.days[0].attractions[0]; setattr(place,key,value)
            with self.assertRaisesRegex(ValueError,"锁定"):
                apply_patch(current,PlanPatch(operations=[dict(action="remove",date=current.days[0].date,item_id=place.item_id)]),{})

    async def test_patch_agent_uses_one_model_call_and_preserves_diy_meals(self):
        current=sample(); day=current.days[0]
        day.meals=[Meal(type="snack",name="DIY",poi_id="snack")]; day.identify_visits()
        operations=[dict(action="update_visit",date=day.date,item_id=day.attractions[0].item_id,visit_duration=45)]
        model=FakeModel([AIMessage(content=json.dumps(dict(changed=True,reply="调整",patch=dict(operations=operations))))])
        agent=make_agent(model)
        result,*_=await agent.run(REQUEST,current,"第一天轻松一点")
        self.assertEqual(model.calls,1)
        self.assertEqual(result.days[0].meals,day.meals)
        self.assertEqual(result.days[0].attractions[0].visit_duration,45)
        agent.planner._align_meals_to_route.assert_not_awaited()

    async def test_quality_does_not_treat_missing_traffic_as_success(self):
        from app.services.plan_quality import evaluate_plan
        plan=sample()
        await validate_schedule(plan,REQUEST,AsyncMock(side_effect=RuntimeError("offline")))
        quality=evaluate_plan(plan,REQUEST)
        self.assertFalse(quality["schedule_passed"])
        self.assertEqual(quality["verified_routes"],0)
        self.assertFalse(quality["official_hours_verified"])

    async def test_only_changed_days_are_revalidated_and_client_schedule_is_discarded(self):
        old=sample(); old.days.append(old.days[0].model_copy(deep=True,update={"date":"2026-10-04","day_index":1}))
        await validate_schedule(old,REQUEST,AsyncMock(return_value={"duration_minutes":10,"distance_meters":100}))
        plan=old.model_copy(deep=True); plan.days[1].attractions[0].visit_duration=30
        plan.days[0].schedule.minimum_minutes=0
        validator=AsyncMock()
        await validate_changed_days(plan,old,REQUEST,validator)
        self.assertEqual([d.date for d in validator.await_args.args[0].days],["2026-10-04"])
        self.assertEqual(plan.days[0].schedule,old.days[0].schedule)

    async def test_agent_checkpoint_reuses_completed_run_without_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"agent.sqlite3"
            run_id=str(uuid4())
            model=FakeModel([AIMessage(content='{"changed":false,"reply":"回答","plan":null}')])
            with patch("app.agents.travel_agent.checkpoint_file",return_value=path):
                first=await make_agent(model).run(REQUEST,sample(),"有什么建议",run_id=run_id)
                resumed_model=FakeModel([])
                second=await make_agent(resumed_model).run(REQUEST,sample(),"有什么建议",run_id=run_id)
            self.assertEqual(first,second)
            self.assertEqual(resumed_model.calls,0)

    async def test_interrupted_agent_resumes_pending_tools(self):
        import asyncio
        from test_travel_agent import POI
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"agent.sqlite3"; run_id=str(uuid4())
            first_model=FakeModel([AIMessage(content="",tool_calls=[dict(name="search_attractions",args={"keyword":"博物馆"},id="tool-1")])])
            async def interrupt(stage,*args):
                if stage.startswith("查找真实地点"): raise asyncio.CancelledError()
            with patch("app.agents.travel_agent.checkpoint_file",return_value=path):
                with self.assertRaises(asyncio.CancelledError):
                    await make_agent(first_model).run(REQUEST,run_id=run_id,progress=interrupt)
                resumed=FakeModel([AIMessage(content=json.dumps(dict(changed=True,reply="完成",plan=PLAN)))])
                with patch("app.agents.travel_agent.search_pois",new=AsyncMock(return_value=[POI])) as search:
                    result=await make_agent(resumed).run(REQUEST,run_id=run_id)
            self.assertEqual(search.await_count,1)
            self.assertEqual(resumed.calls,1)
            self.assertEqual(result[0].city,REQUEST.city)

    async def test_solver_moves_early_appointment_ahead_without_dropping_visits(self):
        plan=sample(3); day=plan.days[0]; day.attractions[-1].fixed_start_time="10:00"
        provider=AsyncMock(return_value={"duration_minutes":10,"distance_meters":500})
        result,outcome=await optimize_day(day,REQUEST,"杭州",provider)
        self.assertEqual(outcome["status"],"feasible_under_assumptions")
        self.assertEqual(result.attractions[0].name,day.attractions[-1].name)
        self.assertEqual({p.item_id for p in day.attractions},{p.item_id for p in result.attractions})
        self.assertEqual(result.schedule.status,"feasible")
        self.assertEqual(day.attractions[0].name,"西湖博物馆")

    async def test_solver_budget_and_unknown_routes_never_claim_infeasible(self):
        day=sample(4).days[0]
        provider=AsyncMock(side_effect=RuntimeError("offline"))
        result,outcome=await optimize_day(day,REQUEST,"杭州",provider,budget=3)
        self.assertEqual(provider.await_count,3)
        self.assertEqual(outcome["status"],"insufficient_data")
        self.assertTrue(all(s.duration_minutes is None for s in result.schedule.segments))
        self.assertEqual(result.attractions,day.attractions)

    async def test_solver_preserves_hotel_endpoints_and_meals(self):
        day=sample(2).days[0]
        day.hotel=Hotel(name="酒店",poi_id="hotel",location=day.attractions[0].location)
        day.meals=[Meal(name="午餐",type="lunch",poi_id="meal",location=day.attractions[-1].location)]
        day.identify_visits()
        result,outcome=await optimize_day(day,REQUEST,"杭州",AsyncMock(return_value={"duration_minutes":5,"distance_meters":100}))
        self.assertEqual(outcome["status"],"feasible_under_assumptions")
        self.assertEqual(result.visit_order[0],day.hotel.item_id+"-start")
        self.assertEqual(result.visit_order[-1],day.hotel.item_id+"-end")
        self.assertEqual(result.meals,day.meals)
        lunch=next(item for item in result.schedule.items if item.kind=="meal")
        self.assertGreaterEqual(lunch.start_time,"12:00")
        await validate_schedule(TripPlan(city="杭州",start_date=day.date,end_date=day.date,days=[result],overall_suggestions=""),REQUEST,AsyncMock(return_value={"duration_minutes":5,"distance_meters":100}))
        self.assertGreaterEqual(next(item for item in result.schedule.items if item.kind=="meal").start_time,"12:00")


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=TripRepository(Path(self.tmp.name)/"trips.sqlite3")
        self.trip=self.repo.create(OWNER,REQUEST.model_dump(),TripPlan(**PLAN).model_dump())
    async def asyncTearDown(self):
        import asyncio
        tasks=list(_tasks.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.tmp.cleanup()
    def body(self): return dict(kind="optimize",date=PLAN["start_date"],base_version=1,request_id=str(uuid4()))

    def test_legacy_schema_migration_does_not_create_a_fake_edit(self):
        trip=self.repo.create(OWNER,REQUEST.model_dump(),PLAN)
        normalized=TripPlan(**PLAN).model_dump()
        updated=self.repo.commit(OWNER,trip["id"],1,normalized,"chat",[{"role":"assistant","content":"回答"}])
        self.assertEqual(updated["version"],1)
        self.assertEqual(len(self.repo.versions(OWNER,trip["id"])),1)

    async def test_job_is_owner_scoped_cancellable_and_deduplicated(self):
        body=self.body(); run=start_run(self.repo,OWNER,self.trip["id"],body)
        duplicate=start_run(self.repo,OWNER,self.trip["id"],body)
        self.assertEqual(run["id"],duplicate["id"])
        with self.assertRaises(KeyError): read_run(self.repo,OTHER,run["id"])
        cancelled=cancel_run(self.repo,OWNER,run["id"])
        self.assertEqual(cancelled["status"],"cancelled")
        await execute(self.repo,OWNER,run["id"])
        self.assertEqual(self.repo.read(OWNER,self.trip["id"])["version"],1)

    async def test_prepared_checkpoint_commits_once_after_restart(self):
        body=self.body()
        with patch("app.services.planning_runs.launch"):
            run=start_run(self.repo,OWNER,self.trip["id"],body)
        changed=TripPlan(**PLAN); changed.days[0].attractions[0].visit_duration=45
        with self.repo.connect() as db:
            row=db.execute("SELECT payload FROM planning_runs WHERE id=?",(run["id"],)).fetchone()
            payload=json.loads(row[0]); payload["prepared"]=dict(plan=changed.model_dump(),extra={"optimization":{"status":"feasible_under_assumptions"}},history=None)
            db.execute("UPDATE planning_runs SET payload=? WHERE id=?",(json.dumps(payload),run["id"]))
        reopened=TripRepository(self.repo.path)
        await execute(reopened,OWNER,run["id"])
        result=read_run(reopened,OWNER,run["id"])
        self.assertEqual(result["status"],"succeeded")
        self.assertEqual(result["result"]["version"],2)
        await execute(reopened,OWNER,run["id"])
        self.assertEqual(len(reopened.versions(OWNER,self.trip["id"])),2)

    async def test_changed_version_blocks_job_commit(self):
        with patch("app.services.planning_runs.launch"):
            run=start_run(self.repo,OWNER,self.trip["id"],self.body())
        changed=copy.deepcopy(self.trip["plan"]); changed["overall_suggestions"]="手动修改"
        self.repo.commit(OWNER,self.trip["id"],1,changed,"manual")
        await execute(self.repo,OWNER,run["id"])
        self.assertEqual(read_run(self.repo,OWNER,run["id"])["status"],"conflict")
        self.assertEqual(self.repo.read(OWNER,self.trip["id"])["plan"],changed)
