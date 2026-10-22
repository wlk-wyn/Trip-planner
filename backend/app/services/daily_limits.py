"""Only explicit, general daily attraction maxima from the user's own text.

No inferred cap for 'relaxed', no parsing of third-party reference prose, and
no automatic deletion of visits to make a score pass.
"""
import re

_NUMBER=r'(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})'
_PATTERN=re.compile(
    r'(?<![第头首末零一二两三四五六七八九十\d])(?:每一天|每天|每日|单日|一天)\s*(?:安排|游览|参观|游玩|去|看)?\s*'
    r'(?:最多|至多|不超过|不多于|控制在)\s*(?:安排|游览|参观|游玩|去|看)?\s*'
    rf'(?P<count>{_NUMBER})\s*(?:个|处|座)?\s*(?:景点|地点|游览点)(?!\s*(?:左右|上下))')


def _number(value):
    if value.isdigit():return int(value)
    digits={'零':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
    if value in digits:return digits[value]
    if value=='十':return 10
    if value.count('十')==1:
        before,after=value.split('十')
        if (not before or before in digits) and (not after or after in digits):return (digits.get(before,1)*10)+digits.get(after,0)
    return None


def daily_attraction_limit(request):
    text=request.free_text_input or ''
    limit=None
    for match in _PATTERN.finditer(text):
        prefix=re.split(r'[，,。；;\n]',text[:match.start()])[-1]
        if re.search(r'不用|不必|取消|不再|不限制|不要限制|不是|并非',prefix[-12:]):continue
        value=_number(match['count'])
        if value is not None and 0<=value<=30:limit=value
    return limit


def daily_limit_issues(plan, request):
    limit=daily_attraction_limit(request)
    if limit is None:return []
    return [{'code':'daily_attraction_limit','severity':'error','day_index':day.day_index,
             'message':f'第{day.day_index+1}天安排了 {len(day.attractions)} 个景点，超过你明确要求的每天最多 {limit} 个；请减少可选地点或修改要求。'}
            for day in plan.days if len(day.attractions)>limit]
