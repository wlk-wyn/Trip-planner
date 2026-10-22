import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from langchain_core.messages import AIMessage
from app.services.place_recommendations import recommend_places
from app.models.schemas import Location

POIS = [{"poi_id": "map-1", "name": "测试博物馆", "address": "测试路1号", "coordinates": [116.4,39.9], "category": "博物馆"}, {"poi_id": "map-2", "name": "已安排地点", "address": "测试路2号", "coordinates": [116.41,39.9], "category": "博物馆"}]

class PlaceRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignores_invented_ids_and_preserves_map_identity(self):
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='{"recommendations":[{"poi_id":"invented","reason":"假地点"},{"poi_id":"map-1","name":"假名","reason":"人文游览备选"},{"poi_id":"map-1"},{"poi_id":"map-2"}]}')))
        with patch("app.services.place_recommendations.search_pois", new=AsyncMock(return_value=POIS)) as query:
            result = await recommend_places("北京", "attraction", "喜欢博物馆", "第一天", None, ["已安排地点"], model)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "测试博物馆")
        self.assertEqual(result[0]["location"], {"longitude":116.4, "latitude":39.9})
        self.assertIsNone(result[0]["estimated_cost"])
        self.assertEqual(query.call_args.args[1], "博物馆")

    async def test_agent_can_refine_search_when_initial_candidates_do_not_match(self):
        from test_travel_agent import FakeModel
        park={"poi_id":"park","name":"附近公园","address":"公园路","coordinates":[116.42,39.9],"category":"公园"}
        model=FakeModel([AIMessage(content="",tool_calls=[dict(name="search_more",args={"keyword":"公园","nearby":False},id="search-1")]),AIMessage(content='{"recommendations":[{"poi_id":"park","reason":"按名称推断适合散步"}]}')])
        with patch("app.services.place_recommendations.search_pois",new=AsyncMock(side_effect=[POIS,[park]])) as query:
            result=await recommend_places("北京","attraction","不想逛博物馆，想散步","第一天",None,[],model)
        self.assertEqual(result[0]["poi_id"],"park")
        self.assertEqual(query.await_count,2)
        self.assertEqual(query.call_args.args[1],"公园")

    async def test_hotels_are_queried_near_anchor(self):
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content='{"recommendations":[{"poi_id":"map-1","reason":"位置可作为备选"}]}')))
        with patch("app.services.place_recommendations.search_nearby_restaurants", new=AsyncMock(return_value=POIS)) as query:
            result = await recommend_places("北京", "hotel", "近地铁", "第一天", Location(longitude=116.4,latitude=39.9), [], model)
        self.assertEqual(query.call_args.kwargs["category"], "100100")
        self.assertEqual(len(result), 1)

    async def test_no_map_results_do_not_call_model(self):
        model = SimpleNamespace(ainvoke=AsyncMock())
        with patch("app.services.place_recommendations.search_pois", new=AsyncMock(return_value=[])):
            self.assertEqual(await recommend_places("北京", "meal", "", "", None, [], model), [])
        model.ainvoke.assert_not_awaited()

    async def test_invalid_model_format_is_not_applied(self):
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=AIMessage(content="无法给出JSON")))
        with patch("app.services.place_recommendations.search_pois", new=AsyncMock(return_value=POIS)):
            with self.assertRaises(ValueError):
                await recommend_places("北京", "attraction", "", "", None, [], model)

    def test_hotel_ancillary_pois_are_not_offered_as_lodging(self):
        from app.services.amap_rest import hotel_candidates
        rows = [{"name": "王府井酒店"}, {"name": "王府井酒店-大堂"}, {"name": "北京饭店金色大厅"}, {"name": "王府井酒店停车场"}]
        self.assertEqual(hotel_candidates(rows), [{"name": "王府井酒店"}])
