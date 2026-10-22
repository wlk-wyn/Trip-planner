"""Keep generated visiting advice separate from operational factual claims.

Times, transport, admission and prices have their own scoped structured sources.
This conservative clause filter is not a semantic fact checker. Existing user
notes and explicitly quoted note edits are never silently rewritten.
"""
import re


OPERATIONAL_CLAIM = re.compile(
    r'开放|营业|开门|闭馆|闭园|休馆|休园|停止入|入馆|入园|入院|入内|进馆|进园|进入|预约|实名|证件|'
    r'免费|免票|收费|门票|票价|购票|售票|退票|余票|有票|无票|套票|联票|通票|放票|'
    r'房价|价格|费用|预算|预订|可订|订房|满房|售罄|约满|限流|排队|'
    r'已核实|已确认|保证|肯定能|无需|不用|不必|即可|就能|全天|每天|每日|'
    r'上午|下午(?!茶)|午后|早上|晚上|傍晚|清晨|早晨|中午|夜间|'
    r'雨天|下雨|降雨|天气|多云|晴天|晴朗|气温|温度|避雨|遮雨|雨棚|淋雨|'
    r'一次逛完|全部逛完|都能逛完|全程走完|'
    r'地铁|公交|打车|乘车|驾车|直达|换乘|即可抵达|'
    r'\d{1,2}[:：]\d{2}|[0-9一二三四五六七八九十百两]+(?:天|日|小时|分钟|公里|米|元|块|月|点|时|号线)|'
    r'[¥￥$]|\d{1,2}\s*(?:am|pm)\b|'
    r'\b(?:free|tickets?|bookings?|reservations?|appointments?|open(?:ing)?|closed?|hours?|admission|available|minutes?|km|prices?|costs?|usd|rmb|cny|yuan|metro|subway|taxi)\b',
    re.I)
CLAUSES = re.compile(r'[，,。！？!?；;\r\n]+|(?<!\d)\.(?!\d)')
TIME_PREFIX = re.compile(r'^(?:上午|下午(?!茶)|午后|早上|晚上|傍晚|清晨|早晨|中午|夜间)'
                         r'(?=在|到|前往|去|从|游|建议|可|沿|参观|先|乘|拍|逛|看)')
FALLBACK = '按兴趣选择游览重点，建议停留以当前时间线为准。'


def clean_generated_advice(text):
    if not OPERATIONAL_CLAIM.search(text or ''):
        return text,0
    clauses=[clause.strip() for clause in CLAUSES.split(text) if clause.strip()]
    # A temporal lead-in can be removed without losing the visiting focus.
    # Don't delete arbitrary words or named places within the recommendation.
    candidates=[TIME_PREFIX.sub('',clause).strip() for clause in clauses]
    kept=[clause for clause in candidates if clause and not OPERATIONAL_CLAIM.search(clause)]
    removed=len(clauses)-len(kept)
    return ('；'.join(kept)+'。' if kept else FALLBACK),removed


def _identity(place):
    location=place.location
    return (place.poi_id or '',place.name,place.address,
            (location.longitude,location.latitude) if location else None)


def normalize_generated_advice(plan,previous=None,*,user_text=''):
    old={}
    if previous:
        for day in previous.days:
            for place in day.attractions:
                old.setdefault(_identity(place),set()).add(place.description)
    removed=rewritten=0
    explicit_note_edit=bool(re.search(r'备注|描述|说明|写为|写成',user_text))
    for day in plan.days:
        for place in day.attractions:
            text=place.description
            if text in old.get(_identity(place),set()):continue
            if explicit_note_edit and text and text in user_text:continue
            cleaned,count=clean_generated_advice(text)
            if cleaned!=text:
                place.description=cleaned;rewritten+=1;removed+=count
    if rewritten:
        from .observability import record_event
        record_event('generated_advice_fields_rewritten',rewritten)
        record_event('generated_advice_claim_clauses_removed',removed)
    return {'fields_rewritten':rewritten,'claim_clauses_removed':removed}
