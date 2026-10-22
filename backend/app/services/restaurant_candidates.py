"""Conservative automatic meal choices; not proof of walk-in availability."""
import re


def suitable_restaurant(name, meal_type, category='', open_hours=''):
    if not isinstance(name, str) or not name.strip():
        return False
    # These labels imply extra access/booking conditions. Keep them out of
    # automatic picks; users can still explicitly choose their own venue.
    access = ('宴会厅', '宴会中心', '宴会餐厅', '会议餐厅', '职工食堂', '员工食堂', '机关食堂', '内部食堂', '单位食堂', '学生食堂', '校内食堂')
    text = name + (category if isinstance(category, str) else '')
    dining_title=any(word in name for word in ('餐厅','餐馆','中餐','西餐','自助餐','食府','家常菜','火锅','面馆','私房菜','烤鸭','涮肉'))
    lodging_title=any(word in name for word in ('宾馆','酒店','旅馆','客栈','公寓','招待所','四合院','民宿','青旅','旅舍','度假村'))
    if not dining_title and (lodging_title or (isinstance(category,str) and '住宿' in category)):
        return False
    if any(word in text for word in access):
        return False
    if re.search(r'(?:大学|学院|中学|小学|学校|幼儿园|校区|校园).*(?:食堂|餐厅|餐饮中心)', name):
        return False
    # Map classifications can be stale or inconsistent with a venue's title.
    # Skip an obvious non-dining title unless it explicitly names a food venue;
    # this is an automatic-choice policy, not proof the business cannot serve food.
    retail = ('画框', '装裱', '五金', '建材', '汽修', '家政', '印刷', '广告制作')
    food = ('餐厅', '饭店', '餐馆', '食府', '厨房', '烧烤', '火锅', '面馆', '家常菜')
    if any(word in name for word in retail) and not any(word in name for word in food):
        return False
    if isinstance(open_hours, str) and any(word in open_hours for word in ('暂停营业', '暂不营业', '内部接待', '暂停开放')):
        return False
    if isinstance(category,str) and any(word in category for word in ('甜品','冷饮','糕饼','咖啡','茶艺','饮品')):
        return False
    if any(word in name for word in ('茶馆','茶室','奶茶','咖啡','甜品','蛋糕','面包','饮品','冰淇淋','芝麻坊','奶酪','酸奶','乳品','小餐车')):
        return False
    if re.search(r'冰[淇激][淋凌]', name):
        return False
    if meal_type == 'breakfast':
        return not any(word in name for word in ('汉堡','火锅','烧烤','酒吧'))
    if any(word in name for word in ('烧饼','豆汁','豆腐脑','油条','煎饼','包子铺')) and not any(word in name for word in food):
        return False  # A snack title alone is insufficient for an automatic full meal.
    return not any(word in name for word in ('汉堡','小吃','粥铺','豆浆','煎包'))


def automatic_restaurant(poi, meal_type, *, visit_parent_ids=()):
    from .visitor_access import scoped_restaurant
    if scoped_restaurant(poi) or (poi.get('parent_poi_id') and poi.get('parent_poi_id') in visit_parent_ids):
        return False
    return suitable_restaurant(poi.get('name'), meal_type, poi.get('category'), poi.get('open_hours'))


def meal_identity_issues(plan,request):
    from .meal_windows import protected_meal_ids
    from .fact_evidence import fact_state
    from .visitor_access import scoped_restaurant
    protected=protected_meal_ids(plan,request)
    issues=[]
    for day in plan.days:
        for meal in day.meals:
            if meal.type=='snack' or not meal.route_matched or meal.item_id in protected:continue
            name=meal.restaurant or meal.name
            relation=fact_state(meal,'parent')
            venue = None
            if meal.location and relation['source'] == 'amap' and relation['status'] == 'recent_reference':
                venue = scoped_restaurant({'parent_poi_id': meal.parent_poi_id,
                    'coordinates': [meal.location.longitude, meal.location.latitude]})
            selected = next((a.name for a in day.attractions if a.poi_id and a.poi_id == meal.parent_poi_id), None) if relation['source']=='amap' and relation['status']=='recent_reference' else None
            if venue or selected:
                issues.append({'code':'automatic_meal_access','severity':'warning','day_index':day.day_index,
                    'message':f'自动匹配的「{name}」登记属于{venue or selected}，不能直接视为场外餐厅；请核对是否需要入园、预约及用餐顺序，或更换场外用餐地点。'})
                continue
            if not automatic_restaurant({'name':name,'category':meal.category,'open_hours':meal.open_hours},meal.type):
                issues.append({'code':'automatic_meal_candidate','severity':'warning','day_index':day.day_index,
                    'message':f'第{day.day_index+1}天自动匹配的用餐地点「{name}」名称或类别需进一步核对，可能不是适合该餐次的普通餐厅；建议确认或更换。'})
    return issues
