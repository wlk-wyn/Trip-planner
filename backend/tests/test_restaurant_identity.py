import unittest
from app.models.schemas import Meal
from app.services.place_facts import apply_provider_facts
from app.services.restaurant_candidates import automatic_restaurant
from app.services.restaurant_candidates import meal_identity_issues
from test_schedule import sample
from test_travel_agent import REQUEST


class RestaurantIdentityTests(unittest.TestCase):
    def test_lodging_name_is_not_automatic_proof_of_a_dining_venue(self):
        for name in ('升旗宾馆(北京天安门店)','中心酒店','湖畔客栈','北京饭店'):
            self.assertFalse(automatic_restaurant({'name':name,'category':'住宿服务;宾馆酒店'},'dinner'),name)
        self.assertFalse(automatic_restaurant({'name':'升旗宾馆','category':'餐饮服务;中餐厅'},'dinner'))

    def test_specifically_named_hotel_restaurant_is_distinct_from_the_hotel(self):
        self.assertTrue(automatic_restaurant({'name':'中心酒店中餐厅','category':'餐饮服务;中餐厅'},'dinner'))
        self.assertTrue(automatic_restaurant({'name':'四季家常菜','category':'餐饮服务;中餐厅'},'dinner'))
        self.assertFalse(automatic_restaurant({'name':'中心酒店宴会厅'},'dinner'))

    def test_dessert_category_and_mobile_snack_title_are_not_full_meal_evidence(self):
        for poi in ({'name':'三元梅园小餐车','category':'餐饮服务;中餐厅'},{'name':'品牌门店','category':'餐饮服务;甜品店'},{'name':'奶酪店'}):
            self.assertFalse(automatic_restaurant(poi,'dinner'),poi)

    def test_provider_category_replaces_old_identity_metadata(self):
        meal=Meal(name='',restaurant='新餐厅',type='dinner',category='旧类别')
        apply_provider_facts(meal,{'category':'餐饮服务;中餐厅'})
        self.assertEqual(meal.category,'餐饮服务;中餐厅')
        apply_provider_facts(meal,{})
        self.assertIsNone(meal.category)

    def test_saved_auto_choices_still_expose_identity_problems(self):
        plan=sample(1)
        meal=Meal(name='',restaurant='升旗宾馆',type='dinner',route_matched=True)
        plan.days[0].meals=[meal];plan.days[0].identify_visits()
        issues=meal_identity_issues(plan,REQUEST)
        self.assertEqual(issues[0]['code'],'automatic_meal_candidate')
        self.assertEqual(issues[0]['day_index'],0)
        meal.locked=True
        self.assertEqual(meal_identity_issues(plan,REQUEST),[])
