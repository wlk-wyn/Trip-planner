"""Budget extraction backed by quotes from user turns, not assistant questions.

This validates a bounded extraction, not arbitrary natural-language semantics or
booking prices. Unclear scope is a clarification, never an assumed group total.
"""
import math
import re
from decimal import Decimal

NUMBER = r'(?<![\d一二三四五六七八九十两])(\d{1,2}|[一二三四五六七八九十两]{1,3})'
MONEY = r'(?<![\dA-Za-z,.+\-])(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d,.])\s*(万)?'


def _integer(text):
    if text.isdigit():
        return int(text)
    values = dict(zip('一二三四五六七八九两', [1,2,3,4,5,6,7,8,9,2]))
    if text == '十': return 10
    if '十' in text:
        if not re.fullmatch(r'[一二三四五六七八九两]?十[一二三四五六七八九]?', text):
            return None
        a,b = text.split('十',1)
        return values.get(a,1)*10+values.get(b,0)
    return values.get(text)


def _quote(raw, key, user_texts):
    evidence = raw.get('cost_evidence')
    quote = evidence.get(key) if isinstance(evidence,dict) else None
    return quote if isinstance(quote,str) and quote.strip() and len(quote)<=500 and any(quote in t for t in user_texts) else ''


def _count(raw,key,user_texts):
    value = raw.get(key)
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=50:
        return None
    quote = _quote(raw,key,user_texts)
    unit = r'(?:人|位|名)' if key=='party_size' else r'(?:间|套)'
    found = [_integer(m) for m in re.findall(NUMBER+r'\s*个?\s*'+unit,quote)]
    if key=='party_size':
        if re.search(r'我俩|我们俩|俩个人|俩人',quote): found.append(2)
        if re.search(r'我们仨|仨个人|仨人',quote): found.append(3)
        found += [_integer(m) for m in re.findall(r'一家'+NUMBER+r'口',quote)]
        family = re.findall(NUMBER+r'\s*(?:位|名|个)?(?:成人|大人|大|儿童|孩子|小孩|小)',quote)
        if len(family)>1:
            parts = [_integer(m) for m in family]
            if all(p is not None for p in parts): found.append(sum(parts))
    return value if value in found else None


def _amounts(text):
    matches = [*re.findall(r'(?:预算|总额|合计|总共|共计|一共|人均|每人|每位|￥|¥)\s*(?:为|是|约|不超过)?\s*'+MONEY,text),
               *re.findall(MONEY+r'\s*(?:元|块)',text)]
    return [Decimal(amount.replace(',',''))*(10000 if wan else 1) for amount,wan in matches]


def _deferred(user_texts):
    for message in reversed(user_texts):
        # New wording outranks the earlier form context appended by the client.
        for text in message.split('\n已填写条件（本条新描述中的更改优先）：',1):
            if re.search(r'(?:先|暂时|暂).{0,5}(?:不算|不看|不管|不考虑|不核算|不设).{0,4}(?:预算|费用)|(?:预算|费用).{0,8}(?:再说|稍后|以后|后面|待定|未定|取消|先不|暂不)|先规划.{0,8}(?:行程|路线).{0,8}(?:预算|费用).{0,8}(?:后|再)',text):
                return True
            if re.search(r'(?:不用|不必|无需)(?:考虑|核算|限制|设置)?预算|预算(?:不限|无所谓|不限制)', text):
                return True
            if (_amounts(text) and not partial_budget(text)) or re.search(r'恢复预算|开始核算|现在算预算',text):
                return False
    return False


def partial_budget(text):
    return bool(re.search(r'(?:餐饮|餐费|吃饭|单餐|每餐|酒店|住宿|每晚|门票|机票|车票).{0,4}预算|预算.{0,4}(?:只含|只算|只用于|仅含)',text)) and not bool(re.search(r'总预算|全程预算|全体预算|目的地内.*预算',text))


def budget_context(raw,user_texts,days):
    people,rooms = _count(raw,'party_size',user_texts),_count(raw,'room_count',user_texts)
    amount = raw.get('budget_amount')
    quote = _quote(raw,'budget_amount',user_texts)
    if (isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount)
            or not 0<=amount<=10000000 or partial_budget(quote) or Decimal(str(amount)) not in _amounts(quote)):
        amount = None
    basis = raw.get('budget_basis')
    basis_quote = _quote(raw,'budget_basis',user_texts)
    if basis=='party' and not re.search(r'全体|合计|总额|总计|总预算|总共|一共|全部|共计',basis_quote): basis=None
    if basis=='person' and not re.search(r'每人|人均|每位',basis_quote): basis=None
    if basis not in {'party','person'}: basis=None
    coverage = raw.get('budget_coverage')
    coverage_quote = _quote(raw,'budget_coverage',user_texts)
    if coverage=='destination' and not re.search(r'目的地|当地|不含往返|不包括往返|往返另|车票另|机票另|大交通另',coverage_quote): coverage=None
    if coverage=='whole_trip' and (re.search(r'不含|不包括|另算',coverage_quote) or not re.search(r'含往返|包含往返|包括往返|含机票|含车票|大交通也',coverage_quote)): coverage=None
    if coverage not in {'destination','whole_trip'}: coverage=None
    deferred = _deferred(user_texts)
    requested = amount is not None or any('预算' in t and not partial_budget(t) for t in user_texts)
    total = None
    if not deferred and amount is not None and coverage=='destination':
        if basis=='party' or people==1: total=amount
        elif basis=='person' and people is not None: total=float(Decimal(str(amount))*people)
    if total is not None and total>10000000: total=None
    missing=[]
    if requested and not deferred:
        if amount is None: missing.append('预算金额（请用数字）')
        if people is None: missing.append('同行总人数')
        if days and days>1 and rooms is None: missing.append('每晚房间数')
        if basis is None and people!=1: missing.append('金额是每人还是全体合计')
        if coverage!='destination': missing.append('不含往返大交通的目的地内预算')
        if total is None and not missing: missing.append('全体合计的目的地内预算')
    return dict(party_size=people,room_count=rooms,destination_budget=total,
        budget_mode='deferred' if deferred else 'active',budget_amount=amount,
        budget_basis=basis,budget_coverage=coverage,budget_clarification=bool(missing)),missing
