"""Conservative interpretation of provider hours, never official availability.

Only complete clock ranges, weekly scopes, seasonal date ranges and explicit
last admission are interpreted. Unsupported exceptions and overnight ranges stay
unknown. Holiday calendars apply only to identified venues with matching scope.
"""
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

WEEK = "一二三四五六日"
SCOPE = re.compile(r"(?:每)?(?:周|星期)([一二三四五六日天])(?:\s*(?:至|到|[-—~～])\s*(?:周|星期)?([一二三四五六日天]))?")
RANGE = re.compile(r"(\d{1,2})[:：](\d{2})\s*(?:至|到|[-—~～])\s*(\d{1,2})[:：](\d{2})")
CLOSED = {"闭馆", "闭园", "休馆", "不开放", "休息", "关闭"}


@dataclass(frozen=True)
class HoursRule:
    windows: tuple[tuple[int, int], ...] = ()
    closed: bool = False
    understood: bool = False
    reason: str = "营业时间缺失"
    outside_weekday: bool = False
    last_admission: int | None = None


ADMISSION = re.compile(r'\s*[（(]?\s*(\d{1,2})[:：](\d{2})\s*(?:停止入馆|停止入园|停止检票)\s*[）)]?\s*$')
SEASON = re.compile(r'^(?:旺季|淡季)?\s*(\d{1,2})月(\d{1,2})日\s*至\s*(次年|来年)?\s*(\d{1,2})月(\d{1,2})日\s*(.+)$')
MONTH_SCOPES = re.compile(r'^(\d{1,2}月至\d{1,2}月(?:\s*[,，、]\s*\d{1,2}月至\d{1,2}月)*)\s*(.+)$')


def _normalize_month_scopes(text, visiting):
    """Expand explicit whole-month scopes; retain unsupported residue verbatim."""
    clauses = []
    for clause in re.split(r'[;；\n]+', text):
        clause = clause.strip()
        # Only a suffix can be normalized: a following inner-venue rule has a
        # different admission scope and must remain uninterpreted.
        clause = re.sub(r'最晚进入\s*(\d{1,2}[:：]\d{2})\s*$', r'\1停止入园', clause)
        match = MONTH_SCOPES.fullmatch(clause)
        if not match:
            clauses.append(clause)
            continue
        ranges, body = match.groups()
        expanded = []
        for start, end in re.findall(r'(\d{1,2})月至(\d{1,2})月', ranges):
            start, end = int(start), int(end)
            if not 1 <= start <= 12 or not 1 <= end <= 12:
                return text
            last = monthrange(visiting.year, end)[1]
            expanded.append(f'{start}月1日至{end}月{last}日 {body}')
        clauses.extend(expanded)
    return '；'.join(clauses)


def _seasonal_rule(text, visiting, calendar_known):
    if not re.search(r'\d{1,2}月\d{1,2}日\s*至', text):
        return None
    unknown = HoursRule(reason='季节营业时间或例外规则无法完整确认')
    if '节假日' in text:
        from .holiday_calendar import outside_published_breaks
        if not calendar_known or outside_published_breaks(visiting) is not True:
            return unknown
    active = []
    for clause in re.split(r'[;；,，\n]+', text):
        clause = clause.strip()
        if not clause:
            continue
        if clause == '节假日营业时间以官方通知为准' and calendar_known:
            continue
        plain = re.sub(r'[（(](?:国家)?(?:法定)?节假日除外[）)]$', '', clause).strip()
        scope = SCOPE.match(plain)
        if scope and plain[scope.end():].strip().removeprefix('全天') in CLOSED:
            continue  # Applicable weekly closure was already checked separately.
        match = SEASON.fullmatch(clause)
        if not match:
            return unknown
        sm, sd, wrap, em, ed, body = match.groups()
        sm, sd, em, ed = map(int, (sm, sd, em, ed))
        try:
            date(visiting.year, sm, sd); date(visiting.year, em, ed)
        except ValueError:
            return unknown
        start, end, current = (sm, sd), (em, ed), (visiting.month, visiting.day)
        if wrap and start <= end:
            return unknown
        rule = parse_hours(body, visiting.isoformat())
        if not rule.understood and not rule.outside_weekday:
            return unknown
        applies = start <= current <= end if start <= end else current >= start or current <= end
        if applies:
            active.append(rule)
    if len(active) != 1:
        return unknown
    rule = active[0]
    return HoursRule(windows=rule.windows, closed=rule.closed, understood=rule.understood,
                     outside_weekday=rule.outside_weekday, last_admission=rule.last_admission,
                     reason='登记季节营业时段与停止入馆时间，非官方实时核实')


def _explicit_closure(text,day,calendar_known):
    """Resolve only standalone weekly closure clauses; never seasonal opening times."""
    if re.search(r'临时|活动|预约|特殊|另行',text):return None
    holiday='节假日' in text
    if holiday:
        from .holiday_calendar import outside_published_breaks
        if not calendar_known or outside_published_breaks(day) is not True:return None
    clauses=re.split(r'[;；,，\n]+',text)
    found=False
    for clause in clauses:
        clause=clause.strip()
        scopes=list(SCOPE.finditer(clause))
        days=set()
        for scope in scopes:
            start=_weekday(scope[1]);end=_weekday(scope[2]) if scope[2] else start
            days.update((start+i)%7 for i in range((end-start)%7+1))
        if day.weekday() not in days:continue
        if RANGE.search(clause):return None  # contradictory specific opening rule
        match=SCOPE.match(clause)
        if not match:continue
        body=clause[match.end():].strip()
        body=re.sub(r'[（(](?:国家)?(?:法定)?节假日除外[）)]$','',body).strip()
        body=re.sub(r'^(?:全天|例行)','',body)
        if body in CLOSED:found=True
    if found:
        return HoursRule(closed=True,understood=True,reason='登记周闭馆规则；已排除当年公布的放假窗口，临时公告仍需核实' if holiday else '登记周闭馆规则，非官方实时核实')
    return None


def _weekday(value):
    return WEEK.index("日" if value == "天" else value)


def parse_hours(value: str | None, visit_date: str, *, calendar_known=False) -> HoursRule:
    if not value or not value.strip():
        return HoursRule()
    text = value.strip()
    # Provider commas may separate weekly rules as well as clock ranges. Only
    # a new explicit weekday scope starts another clause; split-hour ranges stay
    # together. A Monday-only exception must not erase a known Tuesday window.
    text = re.sub(r'[,，]\s*(?=(?:每)?(?:周|星期)[一二三四五六日天])', '；', text)
    # Do not silently strip exceptions, last admission, appointment or negation.
    visiting=date.fromisoformat(visit_date)
    text = _normalize_month_scopes(text, visiting)
    closure=_explicit_closure(text,visiting,calendar_known)
    if closure:return closure
    seasonal = _seasonal_rule(text, visiting, calendar_known)
    if seasonal is not None:
        return seasonal
    weekday = visiting.weekday()
    windows, closed, applicable, scoped_open = [], False, False, False
    weekly_open=False
    cutoffs=[]
    for clause in re.split(r"[;；\n]+", text):
        clause = clause.strip()
        if not clause:
            continue
        days = set()
        for match in SCOPE.finditer(clause):
            start = _weekday(match[1]); end = _weekday(match[2]) if match[2] else start
            days.update((start + offset) % 7 for offset in range((end - start) % 7 + 1))
        body = SCOPE.sub("", clause).strip(" 、,，：:")
        body = re.sub(r"^(?:每日|每天)\s*", "", body)
        holiday_closure = re.fullmatch(r'(?:全天|例行)?(闭馆|闭园|休馆|不开放|休息|关闭)[（(](?:国家)?(?:法定)?节假日除外[）)]', body)
        if days and holiday_closure:
            if weekday not in days:
                continue
            from .holiday_calendar import outside_published_breaks
            if not calendar_known or outside_published_breaks(visiting) is not True:
                return HoursRule(reason='当天涉及节假日闭馆例外，需核实官方公告')
            body = holiday_closure[1]
        admission = ADMISSION.search(body)
        cutoff = None
        if admission:
            token = body[admission.start():].strip()
            if token.count('(') != token.count(')') or token.count('（') != token.count('）'):
                return HoursRule(reason='停止入馆时间括号不完整，需核实')
            h, m = map(int, admission.groups())
            if h > 23 or m > 59:
                return HoursRule(reason='停止入馆时间格式无法确认')
            cutoff = h * 60 + m
            body = body[:admission.start()].strip()
        applies = not days or weekday in days
        if body in CLOSED or body.removeprefix("全天") in CLOSED:
            if applies:
                closed = True; applicable = True
            continue
        if body in {"全天开放", "24小时", "24小时开放", "00:00-24:00"}:
            parsed = [(0, 1440)]
        else:
            matches = list(RANGE.finditer(body))
            residue = RANGE.sub("", body).strip(" 、,，/")
            if not matches or residue:
                return HoursRule(reason="营业时间包含未支持的文字或例外规则，需人工确认")
            parsed = []
            for match in matches:
                a, b, c, d = map(int, match.groups())
                if a > 23 or b > 59 or c > 24 or d > 59 or (c == 24 and d):
                    return HoursRule(reason="营业时间格式无法确认")
                start, end = a * 60 + b, c * 60 + d
                if end <= start:
                    return HoursRule(reason="营业时间跨夜或区间不明确，需人工确认")
                parsed.append((start, end))
        if applies:
            if cutoff is not None:
                if len(parsed) != 1 or not parsed[0][0] < cutoff <= parsed[0][1]:
                    return HoursRule(reason='停止入馆时间与营业范围不一致')
                cutoffs.append(cutoff)
            applicable = True; windows.extend(parsed)
            scoped_open = scoped_open or bool(days)
        weekly_open=weekly_open or bool(days)
    if not applicable:
        return HoursRule(reason="登记周营业范围未覆盖当天，不能自动安排" if weekly_open else "登记营业时段未明确覆盖当天",outside_weekday=weekly_open)
    if closed and scoped_open:
        return HoursRule(reason="登记营业时间存在相互冲突的规则")
    return HoursRule(() if closed else tuple(sorted(set(windows))), closed, True, "登记营业时间，非官方实时核实", last_admission=min(cutoffs) if cutoffs else None)


def parse_place_hours(place, visit_date):
    """National holiday exceptions are scoped to identified Beijing museums only.

    The calendar only resolves an explicit provider closure on an ordinary day;
    it never infers holiday opening or substitutes official operating hours.
    """
    def field(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    identities = {
        'B000A8UIN8': {'故宫博物院', '故宫'},
        'B000A83U0P': {'中国国家博物馆', '国家博物馆'},
        'B000A7BC2S': {'中国美术馆'},
    }
    location = field(place, 'location')
    known = False
    try:
        if location is None:
            lon, lat = map(float, field(place, 'coordinates', (None, None)))
        else:
            lon, lat = float(field(location, 'longitude')), float(field(location, 'latitude'))
        identity = field(place, 'poi_id') or field(place, 'id')
        bounds = (116.403, 39.921, 116.412, 39.930) if identity == 'B000A7BC2S' else (116.38, 39.89, 116.42, 39.94)
        left, bottom, right, top = bounds
        known = (field(place, 'name') in identities.get(identity, set())
                 and left <= lon <= right and bottom <= lat <= top)
    except (TypeError, ValueError):
        pass
    return parse_hours(field(place, 'open_hours'), visit_date, calendar_known=known)


def hours_issues(day):
    """Read-only checks also work for old saved schedules; never guess identity by name."""
    issues, unknown = [], 0
    places = [(p, {p.item_id, f"attraction-{i}"}) for i, p in enumerate(day.attractions)]
    places += [(p, {p.item_id, f"meal-{i}", p.type}) for i, p in enumerate(day.meals)]
    items = {item.id: item for item in day.schedule.items} if day.schedule else {}
    for place, ids in places:
        name = getattr(place, "restaurant", None) or place.name
        rule = parse_place_hours(place, day.date)
        if rule.outside_weekday:
            issues.append({'code':'opening_weekday_uncovered','severity':'warning','day_index':day.day_index,
                           'message':f'{name}的登记周营业日未覆盖当天，请先核实或调整日期/地点'})
        if not rule.understood:
            unknown += 1; continue
        message = None
        if rule.closed:
            message = f"{name}登记信息显示当天不开放，请核对官方公告并调整"
        else:
            item = next((items[identity] for identity in ids if identity and identity in items), None)
            if item and item.start_time and item.end_time:
                start = sum(int(v) * m for v, m in zip(item.start_time.split(":"), [60, 1]))
                end = sum(int(v) * m for v, m in zip(item.end_time.split(":"), [60, 1]))
                if not any(a <= start and start <= end <= b for a, b in rule.windows):
                    message = f"{name}计划 {item.start_time}—{item.end_time}，不在登记营业时段内，请调整"
                elif rule.last_admission is not None and start >= rule.last_admission:
                    message = f'{name}计划入馆时间已到或晚于登记停止入馆时间，请调整'
        if message:
            issues.append({"code": "closed_day" if rule.closed else "opening_hours_conflict", "severity": "warning", "message": message, "day_index": day.day_index})
    if unknown:
        issues.append({"code": "opening_hours_unknown", "severity": "info", "message": f"第{day.day_index + 1}天有 {unknown} 处地点营业时段待确认，不能仅凭交通和总时长判断可执行", "day_index": day.day_index})
    return issues


def excludes_date(place,visit_date):
    rule=parse_place_hours(place,visit_date)
    return rule.closed or rule.outside_weekday
