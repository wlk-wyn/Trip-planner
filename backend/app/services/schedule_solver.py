"""Bounded OR-Tools day solver. Unknown edges are forbidden, never zero."""
import asyncio
import math
import time
from ..models.schemas import RouteSegment
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from .schedule_validator import stops_for_day, route_segment, build_schedule, minutes
from .amap_rest import get_route
from .opening_hours import parse_place_hours

MAX_ROUTE_QUERIES = 24
MAX_STOPS = 18


def _distance(a, b):
    x, y = a["place"].location, b["place"].location
    if not x or not y: return math.inf
    return (x.longitude-y.longitude)**2 + (x.latitude-y.latitude)**2


def solve_order(stops, edges, start, end, buffer, seconds=2, visit_date=None):
    n = len(stops)
    manager = pywrapcp.RoutingIndexManager(n+2, 1, [n], [n+1])
    routing = pywrapcp.RoutingModel(manager)
    def transit(a, b):
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        if i == n: return 0
        if j == n+1: return stops[i]["duration"]
        edge = edges.get((i,j))
        return stops[i]["duration"] + (edge.duration_minutes if edge and edge.status == "verified" else 100000) + buffer
    callback = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(callback)
    routing.AddDimension(callback, 1440, 2880, False, "Time")
    dimension = routing.GetDimensionOrDie("Time")
    # Penalize the whole day's span as well as transit, so a short route with
    # hours of waiting does not beat a useful morning visit before lunch.
    dimension.SetSpanCostCoefficientForVehicle(2, 0)
    dimension.CumulVar(routing.Start(0)).SetRange(start, start)
    dimension.CumulVar(routing.End(0)).SetRange(start, end)
    routing.AddConstantDimension(1, n+2, True, "Position")
    positions = routing.GetDimensionOrDie("Position")
    for i, stop in enumerate(stops):
        idx = manager.NodeToIndex(i)
        dimension.CumulVar(idx).SetRange(start, end-stop["duration"])
        if stop["fixed"]:
            appointment = minutes(stop["fixed"])
            if appointment < start or appointment+stop["duration"] > end: return None
            dimension.CumulVar(idx).SetRange(appointment, appointment)
        if visit_date and stop["kind"] != "hotel":
            rule = parse_place_hours(stop["place"], visit_date)
            if rule.closed or rule.outside_weekday:
                return None
            if rule.windows:
                allowed = sorted({t for a, b in rule.windows for t in range(max(start, a), min(end, b) - stop["duration"] + 1)
                                  if rule.last_admission is None or t < rule.last_admission})
                if not allowed or (stop["fixed"] and appointment not in allowed):
                    return None
                dimension.CumulVar(idx).SetValues(allowed)
        if stop["place"].locked:
            positions.CumulVar(idx).SetValue(i+1)
        slot = getattr(stop["place"], "type", "") if stop["kind"] == "meal" else ""
        windows = {"breakfast": (8*60,11*60), "lunch": (12*60,14*60), "dinner": (18*60,20*60)}
        if slot in windows:
            lo, hi = windows[slot]
            dimension.SetCumulVarSoftLowerBound(idx, lo, 3)
            dimension.SetCumulVarSoftUpperBound(idx, hi, 3)
        if stop["kind"] == "hotel" and stop["id"].endswith("-start"):
            routing.NextVar(routing.Start(0)).SetValue(idx)
        if stop["kind"] == "hotel" and stop["id"].endswith("-end"):
            routing.NextVar(idx).SetValue(routing.End(0))
        for j in range(n):
            edge = edges.get((i,j))
            if i != j and (edge is None or edge.status != "verified"):
                routing.NextVar(idx).RemoveValue(manager.NodeToIndex(j))
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(seconds)
    solution = routing.SolveWithParameters(params)
    if not solution: return None
    result=[]; arrivals={}; index=routing.Start(0)
    while not routing.IsEnd(index):
        node=manager.IndexToNode(index)
        if node<n:
            result.append(node)
            arrivals[stops[node]["id"]]=solution.Value(dimension.CumulVar(index))
        index=solution.Value(routing.NextVar(index))
    return result, arrivals


async def optimize_day(day, request, city, provider=None, progress=None, budget=MAX_ROUTE_QUERIES, *, gate_lookup=None):
    day = day.model_copy(deep=True)
    stops = stops_for_day(day)
    if len(stops)>MAX_STOPS or len(stops)<2:
        return day, {"status":"unsupported", "note":"当天地点过多或不足两个，保留原安排。", "queries":0}
    start, end = minutes(day.start_time or request.daily_start_time), minutes(day.end_time or request.daily_end_time)
    buffer = day.buffer_minutes if day.buffer_minutes is not None else request.buffer_minutes
    if end<=start or any(s["duration"]>end-start for s in stops):
        return day, {"status":"proven_infeasible", "note":"单个停留时长或当天起止时间已超出可用范围，请调整时间。", "queries":0}
    # Original edges first. Coordinate distances only prune candidates, never supply times.
    pairs = list(zip(range(len(stops)-1),range(1,len(stops))))
    remaining = [(i,j) for i in range(len(stops)) for j in range(len(stops)) if i!=j and (i,j) not in pairs]
    remaining.sort(key=lambda pair:_distance(stops[pair[0]],stops[pair[1]]))
    pairs += remaining
    edges={}; queries=0; semaphore=asyncio.Semaphore(1)
    mode="walking" if day.transportation=="步行" else "driving" if day.transportation=="自驾" else "transit"
    selected_provider=provider or get_route
    deadline=time.monotonic()+75
    for i,j in pairs[:budget]:
        if progress: await progress("核实交通", queries, budget)
        remaining_seconds=deadline-time.monotonic()
        if remaining_seconds<=0: break
        queries+=1
        try:
            edges[i,j]=await asyncio.wait_for(route_segment(stops[i],stops[j],city,mode,semaphore,selected_provider,gate_lookup=gate_lookup),timeout=min(20,remaining_seconds))
        except asyncio.TimeoutError:
            edges[i,j]=RouteSegment(from_id=stops[i]["id"],to_id=stops[j]["id"],from_name=stops[i]["place"].name,to_name=stops[j]["place"].name,mode=mode,status="unverified",note="本次候选交通查询超时")
            break
        if progress: await progress("核实交通", queries, budget)
        segment=edges[i,j]
        if segment.status!="verified" and any(code in segment.note for code in ["10003", "10044", "40000", "10001", "10002", "10005", "10009", "10012", "10041"]):
            break
    if progress: await progress("排出当天路线",queries,budget)
    order=await asyncio.to_thread(solve_order,stops,edges,start,end,buffer,visit_date=day.date)
    if order is None:
        # Preserve the checked original path; never claim a sparse search proves infeasibility.
        original=[edges.get((i,i+1)) for i in range(len(stops)-1)]
        if all(original): day.schedule=build_schedule(day,stops,original,request)
        status="insufficient_data" if len(edges)<len(stops)*(len(stops)-1) or any(e.status!="verified" for e in edges.values()) else "no_solution_within_budget"
        note="本次查询和求解范围内未找到满足全部地点与预约的方案，保留原安排；可减少地点或调整时间。"
    else:
        order, arrivals = order
        from .schedule_validator import clock
        day.planned_start_times={identity:clock(value) for identity,value in arrivals.items() if clock(value)}
        ordered=[stops[i] for i in order]
        segments=[edges[a,b] for a,b in zip(order,order[1:])]
        day.visit_order=[s["id"] for s in ordered]
        day.schedule=build_schedule(day,ordered,segments,request)
        day.attractions.sort(key=lambda p:day.visit_order.index(p.item_id))
        day.description=" → ".join(p.name for p in day.attractions)
        status="feasible_under_assumptions" if day.schedule.status=="feasible" else "no_solution_within_budget"
        note="在本次已核实路段范围内调整顺序和时间，保留全部地点、用餐与预约；开放时间仍需核实。"
    if day.schedule:
        day.schedule.solver_status=status; day.schedule.solver_note=note; day.schedule.route_queries=queries
    return day, {"status":status,"note":note,"queries":queries}
