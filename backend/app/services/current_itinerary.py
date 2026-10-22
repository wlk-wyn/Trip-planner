"""High-confidence saved-place lookups need no model or new map search.

Only enumerations of the existing itinerary qualify. Recommendations, changes,
feasibility and broader questions continue through the agent.
"""
import re
from .schedule_validator import stops_for_day,display_name,minutes
from .schedule_integrity import check_integrity
from .daily_limits import _number


def timeline_reference(day,request=None):
    if day.schedule is None or check_integrity(day,request):return []
    rows=[]
    for item in day.schedule.items:
        start,end=item.start_time,item.end_time
        if (not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',start or '')
                or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',end or '')
                or minutes(end)<minutes(start) or (start==end and item.kind!='hotel')):
            start,end=None,None
        rows.append(dict(id=item.id,name=item.name,kind=item.kind,start_time=start,end_time=end,
                         suggested_duration_minutes=item.duration_minutes))
    return rows


def saved_places_answer(plan,text):
    text=(text or '').strip()
    if re.search(r'推荐|建议|增加|添加|换|修改|调整|改成|改为|重新|优化|能否|可以|合适|怎么样|怎么|为何|为什么|删|移到|移动',text):return None
    if re.search(r'预约|开放|门票|免费|收费|费用|价格|多少钱|花费|多久|交通|耗时|附近|值得|亲子|室内|室外|美食|好吃|除了|除去|只列|只看|不在|没在|还没|未|今天|明天|后天|昨天|上午|下午|晚上|周[一二三四五六日天]|星期|\d+月|\d{4}-\d',text):return None
    if not re.search(r'哪些.{0,5}(?:景点|地方|地点)|(?:景点|地方|地点).{0,5}有哪些',text):return None
    if not re.search(r'当前|目前|现在|行程|安排|每天|每日|各天|第[\d一二两三四五六七八九十]+天|Day\s*\d|D\d',text,re.I):return None
    days=re.findall(r'第([\d一二两三四五六七八九十]+)天|Day\s*(\d+)|\bD(\d+)\b',text,re.I)
    requested={_number(next(v for v in group if v)) for group in days}
    if requested:
        selected=[d for d in plan.days if d.day_index+1 in requested]
        if len(selected)!=len(requested):return f'当前保存的行程共{len(plan.days)}天，请选择已有日期查看。'
    else:selected=plan.days
    lines=['按当前保存的地点列表，实际安排如下：']
    for day in selected:
        reference=timeline_reference(day)
        items={item['id']:item for item in reference}
        visits=[]
        for stop in stops_for_day(day.model_copy(deep=True)):
            if stop['kind']!='attraction':continue
            item=items.get(stop['id'])
            clock=f'（已保存计划 {item["start_time"]}—{item["end_time"]}）' if item and item['start_time'] and item['end_time'] else '（时间待校验）'
            visits.append(display_name(stop)+clock)
        lines.append(f'第{day.day_index+1}天（{day.date}）：'+('；'.join(visits) if visits else '暂无景点安排')+'。')
        if reference and day.schedule.conflicts:lines.append('这一天仍有时间冲突，请查看时间线提示。')
    lines.append('以上为已保存的计划参考，不代表实际到达或预约成功；每日出发窗口不是入园时刻。')
    return '\n'.join(lines)
