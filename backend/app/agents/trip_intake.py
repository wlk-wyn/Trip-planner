"""Turn a short travel conversation into the fields required by the planner."""

import json
import re
from datetime import date, timedelta

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..models.schemas import ChatTurn, TripIntakeData
from .langgraph_planner import get_langgraph_planner
from ..services.budget_intake import budget_context


TRANSPORTATION = {"公共交通", "自驾", "步行", "混合"}
ACCOMMODATION = {"经济型酒店", "舒适型酒店", "豪华酒店", "民宿"}


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _days(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        days = int(value)
        return days if 1 <= days <= 30 else None
    except (TypeError, ValueError):
        return None


def _explicit_duration(user_texts):
    """Bounded literal trip lengths outrank model arithmetic and stale form dates.

    Only clear day-count phrases are parsed here, not arbitrary date language.
    An explicit return date is retained so contradictory inputs can be clarified.
    """
    from ..services.budget_intake import _integer
    number = r'(\d{1,2}|[一二三四五六七八九十两]{1,3})'
    pattern = rf'(?:游玩|玩|游览|待|旅行|行程|安排)\s*(?:上|个)?\s*{number}\s*天|{number}\s*天(?:游|行程|旅行|的行程)'
    date_token = r'(?:\d{4}[-/年])?\d{1,2}[-/月]\d{1,2}(?:日|号)?'
    explicit_end = rf'(?:返程日期|返程|回程|返回|结束|到|至|—|~|～)\s*[:：]?\s*{date_token}|{date_token}\s*(?:返程|回程|返回|结束|回家)'
    for message in reversed(user_texts):
        for text in message.split('\n已填写条件（本条新描述中的更改优先）：', 1):
            matches = list(re.finditer(pattern, text))
            if matches:
                length = _integer(next(g for g in matches[-1].groups() if g))
                if length and 1 <= length <= 30:
                    return length, bool(re.search(explicit_end, text))
            if re.search(explicit_end, text):
                return None, True
    return None, False


def normalize_intake(raw: dict, today: date, user_texts=None) -> TripIntakeData:
    """Validate model output and ask only for planning-critical missing fields."""
    city = str(raw.get("city") or "").strip()[:80] or None
    start = _date(raw.get("start_date"))
    end = _date(raw.get("end_date"))
    days = _days(raw.get("travel_days"))
    stated_days, explicit_end = _explicit_duration(user_texts or [])
    date_conflict = bool(start and end and stated_days and explicit_end and (end-start).days+1 != stated_days)
    if start and stated_days and not explicit_end:
        days = stated_days
        end = start + timedelta(days=days - 1)
    if start and end:
        days = (end - start).days + 1
    elif start and days:
        end = start + timedelta(days=days - 1)
    elif end and days:
        start = end - timedelta(days=days - 1)

    transportation = raw.get("transportation")
    accommodation = raw.get("accommodation")
    preferences = raw.get("preferences")
    if not isinstance(preferences, list):
        preferences = []
    preferences = [str(item).strip()[:30] for item in preferences if isinstance(item, str) and item.strip()][:12]
    notes = str(raw.get("free_text_input") or "").strip()[:1500]
    costs, missing_costs = budget_context(raw, user_texts or [], days)

    reply = ""
    if not city:
        reply = "这次想去哪个城市？告诉我目的地就能继续。"
    elif start and end and end < start:
        start = end = None
        days = None
        reply = "结束日期早于出发日期，能再确认一下出行日期吗？"
    elif start and end and (end - start).days >= 30:
        start = end = None
        days = None
        reply = "目前一次最多规划 30 天。请告诉我一个不超过 30 天的日期范围。"
    elif start and end and start < today:
        start = end = None
        reply = "出发日期已经过去了，请告诉我新的出发日期。"
    elif not start:
        reply = "想什么时候出发？可以说具体日期（如 10 月 3 日），或“下周六”。"
    elif not end:
        reply = "收到出发日期了。这趟打算玩几天？"
    elif date_conflict:
        reply = '出发和返程日期与所说的旅行天数不一致，请确认按日期范围还是按天数安排。'
    elif missing_costs:
        reply = '为了按预算规划，请确认' + '、'.join(missing_costs) + '。也可以先规划，预算稍后再定。'

    if not reply and costs['budget_mode']=='deferred':
        ready_reply = f'先规划{city}的{days}天行程，预算暂不作达标判断，费用仍会标明已知和待核实。'
    elif not reply and costs['destination_budget'] is not None:
        ready_reply = f"按{costs['party_size']}人、目的地内全体预算{costs['destination_budget']:g}元规划{city}{days}天（不含往返大交通）；参考费用仍需核实。"
    else:
        ready_reply = f"已了解你的想法，正在规划 {city} 的 {days} 天行程。"

    return TripIntakeData(
        ready=not reply,
        reply=reply or ready_reply,
        city=city,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        travel_days=days if isinstance(days, int) and 1 <= days <= 30 else None,
        transportation=transportation if transportation in TRANSPORTATION else None,
        accommodation=accommodation if accommodation in ACCOMMODATION else None,
        preferences=preferences,
        free_text_input=notes,
        **costs,
    )


async def interpret_trip_intake(history: list[ChatTurn]) -> TripIntakeData:
    """Extract the latest intent from all turns, including corrections."""
    today = date.today()
    system = SystemMessage(content=f"""你是旅行规划 Agent 的需求理解助手。
从对话中提取用户最终确认的意图；若后文更改了前文，以最后的要求为准。助手先前的追问不是用户确认的事实。city 只能是旅行目的地，不能把“从上海出发”之类的出发地当作目的地。
只输出 JSON 对象，字段：city, start_date, end_date, travel_days, transportation, accommodation, preferences, free_text_input, party_size, room_count, budget_amount, budget_basis, budget_coverage, cost_evidence。
未知字段填 null；preferences 用字符串数组；free_text_input 简洁保留预算、同行人、节奏、兴趣和特殊要求，不要重复城市、日期、天数，也不要添加用户没说的偏好。
日期必须为 YYYY-MM-DD。明确的相对日期（明天、下周六）可依今天计算；只有月日时可按最近的未来日期理解；“国庆”“春节”“暑假”“下个月”等无法确定具体出发日时填 null，绝不凭空指定日期。
只给出发日期和“玩几天”时，end_date填null，由程序按包含出发当天计算返程日，不要自己加天数。用户明确给出返程日期时才填写end_date。
transportation 仅指目的地内交通，可选公共交通、自驾、步行、混合；accommodation 可选经济型酒店、舒适型酒店、豪华酒店、民宿。未提及就填 null。
party_size为用户明确的同行总人数，room_count为明确的每晚房间数，均为整数；不能从学生党、天数或双人房推断人数，也不能默认1人1间。
budget_amount为用户表达的整趟旅行预算金额（人民币数值），不填单餐餐费、每晚房价或单项票价；budget_basis仅可为party（全体合计）、person（每人）或null；budget_coverage仅可为destination（明确目的地内、不含往返大交通）、whole_trip（明确含往返）、null。未明确范围时必须null，不能把“预算1500元”默认为目的地内全体预算。人均转总额由程序计算，不要自己乘。
cost_evidence为对象，party_size、room_count、budget_amount、budget_basis、budget_coverage各自对应一段用户原话的逐字引用。只引用用户消息，可引用已填写条件；助手问题中的示例不是用户确认。后文的更改或暂缓优先，已填写条件不得覆盖本条用户的新描述。未找到用户原话就填null。
不要回答用户，不要输出 Markdown 或代码围栏。确保字符串中的引号正确转义，输出必须是可被标准 JSON 解析器读取的单个对象。
今天是 {today.isoformat()}。""")
    messages = [system]
    for turn in history[-12:]:
        content = turn.content.strip()[:1500]
        if content:
            messages.append(HumanMessage(content=content) if turn.role == "user" else AIMessage(content=content))
    llm = get_langgraph_planner().fast_llm
    for attempt in range(2):
        response = await llm.ainvoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        start = content.find("{")
        try:
            if start < 0:
                raise ValueError("需求理解未返回有效数据")
            raw, _ = json.JSONDecoder().raw_decode(content[start:])
            if not isinstance(raw, dict):
                raise ValueError("需求理解返回的数据格式无效")
            return normalize_intake(raw, today, [t.content.strip()[:1500] for t in history[-12:] if t.role=='user'])
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            if attempt:
                raise ValueError("需求理解两次返回了无效 JSON") from exc
            messages.extend([
                AIMessage(content=content[:4000]),
                HumanMessage(content=f"上一条不是有效的单个 JSON 对象（{exc}）。请修正格式，只返回完整 JSON 对象，不要解释。"),
            ])
    raise ValueError("需求理解未返回有效数据")
