"""Reviewed park/inner-venue scope, not live admission or ticket inventory."""
import re
from datetime import date,datetime
from .visitor_access import CHINA,REFERENCE_DAYS,matching_policy

PARKS=(
    dict(key='temple_heaven',city='北京',poi_id='B000A81CB2',names=('天坛公园',),
         bounds=(116.39,39.86,116.43,39.90),reviewed_on='2026-10-19',
         source_url='https://www.tiantanpark.cn/guide_service.html',opens_days=None,opens_time=None,
         inner_names=('祈年殿','回音壁','圜丘','斋宫','北神厨','北宰牲亭'),
         peak=('06:00','22:00','21:00','08:00','18:00','17:30'),low=('06:30','22:00','21:00','08:00','17:00','16:30'),
         message='公园与祈年殿、回音壁等景点开放范围不同；公园门票不能代替相应景点票。园内上述景点逢周一关闭，法定节假日例外需核对官方公告。'),
    dict(key='summer_palace',city='北京',poi_id='B000A7O1CU',names=('颐和园',),
         bounds=(116.25,39.97,116.30,40.02),reviewed_on='2026-10-19',
         source_url='https://www.summerpalace.net.cn/',opens_days=None,opens_time=None,
         inner_names=('佛香阁','德和园','颐和园博物馆','苏州街'),
         peak=('06:00','20:00','19:00','08:00','18:00','17:30'),low=('06:30','19:00','18:00','08:30','17:00','16:30'),
         message='普通门票与联票、园中园单独票的范围不同；联票包含公园门票和园中园门票，游船等另核对。佛香阁、德和园、颐和园博物馆、苏州街周一关闭，重点节假日例外需查官方公告。'),
)


def matching_park(city,place):
    return matching_policy(city,place,policies=PARKS)


def park_message(policy,visiting):
    day=date.fromisoformat(visiting)
    park_start,park_end,park_last,inner_start,inner_end,last=policy['peak' if 4<=day.month<=10 else 'low']
    return (policy['message']+f' 当前季节常规参考：公园{park_start}—{park_end}、{park_last}停止入园，'
            f'园内上述景点{inner_start}—{inner_end}、{last}停止进入；公园入口到场馆还需园内步行。')


def _inner_intent(policy,place,request):
    texts=[place.description or '',request.free_text_input or '',request.reference_content or '',
           *(c.place.name if c.place else c.name for c in request.reference_candidates if c.priority=='required')]
    wanted=[]
    for name in policy['inner_names']:
        for text in texts:
            for clause in re.split(r'[，,。；;\n]',text):
                if name not in clause:continue
                if re.search(r'不去|不进|不入|不看|不参观|不游览|只看外观|门外|外面|外观',clause):continue
                wanted.append(name);break
            if name in wanted:break
    return wanted


def park_scope_issues(plan,request,*,now=None):
    from .current_itinerary import timeline_reference
    from .holiday_calendar import outside_published_breaks
    from .schedule_validator import minutes
    now=now or datetime.now(CHINA)
    if now.tzinfo is None:raise ValueError('Reference time must have a timezone')
    today=now.astimezone(CHINA).date()
    issues=[]
    for day in plan.days:
        for place in day.attractions:
            policy=matching_park(plan.city,place)
            if not policy:continue
            wanted=_inner_intent(policy,place,request)
            def add(code,message,severity='warning'):
                issues.append(dict(code=code,severity=severity,day_index=day.day_index,item_id=place.item_id,
                                   message=f'{place.name}：{message}',source_url=policy['source_url']))
            age=(today-date.fromisoformat(policy['reviewed_on'])).days
            if not 0<=age<=REFERENCE_DAYS:
                add('park_scope_expired','园中园规则参考已过期，请重新核对开放及票种范围。');continue
            visiting=date.fromisoformat(day.date)
            _,park_end,park_last,opening,_,last=policy['peak' if 4<=visiting.month<=10 else 'low']
            row=next((r for r in timeline_reference(day,request) if r['id']==place.item_id),None)
            if row and row['start_time'] and minutes(row['start_time'])>=minutes(park_last):
                add('park_last_entry',f'计划{row["start_time"]}才到公园，已到或晚于公园常规{park_last}停止入园时间。')
            if row and row['end_time'] and minutes(row['end_time'])>minutes(park_end):
                add('park_closing_time',f'计划停留至{row["end_time"]}，超过公园常规{park_end}闭园时间。')
            if not wanted:continue
            if visiting.weekday()==0:
                if outside_published_breaks(visiting) is True:
                    add('inner_venue_closed',f'涉及{"、".join(wanted)}；常规周一关闭，不可用公园仍开放推断可入内，请调整日期或游览范围。')
                else:
                    add('inner_venue_holiday_unconfirmed','周一节假日例外尚未确认，不能据此保证园内场馆开放。')
            if not row or not row['start_time']:
                add('inner_venue_time_unverified','园内场馆已列入游览建议，但到达时间尚待校验。')
            else:
                if minutes(row['start_time'])>=minutes(last):
                    add('inner_venue_last_entry',f'计划{row["start_time"]}才到公园，已到或晚于上述场馆常规{last}停止进入时间，园内步行还需另留时间。')
                elif minutes(row['start_time'])<minutes(opening):
                    add('inner_venue_wait',f'到公园早于上述场馆常规{opening}开放时间，需另留等待及园内步行。','info')
            add('park_ticket_scope_unconfirmed',f'涉及{"、".join(wanted)}，请核对所选票种是否覆盖；已有门票金额不能单独证明覆盖这些场馆。')
    return issues
