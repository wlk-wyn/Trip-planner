"""Reviewed official admission summaries. No runtime scraping or model extraction.

Match venue identity before selecting a guide; expiry hides actionable old rules.
Sources were read on 2026-10-22. These are not live notices or booking receipts.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta

from .visitor_access import CHINA, REFERENCE_DAYS, matching_policy
from .park_scopes import matching_park, park_message

REVIEWED = date(2026, 10, 22)
GUIDES = {
    'palace': {
        'source_name': '故宫博物院官网',
        'sources': [{'title': '故宫博物院参观须知', 'url': 'https://www.dpm.org.cn/singles_detail/259831.html'}],
        'booking_url': 'https://ticket.dpm.org.cn/',
        'booking_label': '去官方预约',
        'booking_channel': '可在“故宫博物院”官方小程序完成实名预约。',
        'notice': '馆方未授权第三方代售门票或代理展览预约；讲解商品不能替代官方入院预约。',
        'summary': [
            {'label': '预约要求', 'text': '所有观众均须实名预约，包括免票及陪护人员；不出售当日票。'},
            {'label': '放票规则', 'text': '门票及展览于参观前7天20:00开始预约。'},
            {'label': '入场准备', 'text': '携带预约证件原件，从午门入院；神武门、东华门可离院。'},
        ],
        'details': [
            {'label': '参观时段', 'text': '上午票最迟12:00检票，下午票最早11:00检票；仍须按预约时段到场。'},
            {'label': '退改规则', 'text': '票种、日期、时段和参观人不可改签；未使用门票可在参观前一天24:00前经原渠道退票。'},
            {'label': '闭馆安排', 'text': '通常周一闭馆，法定节假日例外及临时调整以官方公告为准。'},
        ],
    },
    'national': {
        'source_name': '中国国家博物馆官方预约系统',
        'sources': [{'title': '个人预约须知', 'url': 'https://pcticket.chnmuseum.cn/'}],
        'booking_url': 'https://pcticket.chnmuseum.cn/',
        'booking_label': '去官方免费预约',
        'booking_channel': '官方预约页提供微信小程序二维码，个人观众扫码完成预约。',
        'notice': '免费参观也需要成功预约，第三方讲解不等于入馆资格。',
        'summary': [
            {'label': '预约要求', 'text': '先以有效身份证件完成实名认证，再免费预约。'},
            {'label': '放票规则', 'text': '可预约7日内参观，每天17:00放票；须选择入馆时段。'},
            {'label': '入场准备', 'text': '携带本人预约证件原件及预约成功信息，按所约时段从北门核验。'},
        ],
        'details': [
            {'label': '预约数量', 'text': '每个账号每周最多预约1次，每次最多5人；退票仍计入预约次数。'},
            {'label': '参观时间', 'text': '通常9:00—17:00，16:00停止入馆；6月1日至10月31日延至17:30，16:30停止入馆。'},
            {'label': '闭馆安排', 'text': '周一通常闭馆，法定节假日除外；请遵循所约时段及临时公告。'},
        ],
    },
    'temple_heaven': {
        'source_name': '天坛公园官网',
        'sources': [
            {'title': '导览服务与线上购票', 'url': 'https://www.tiantanpark.cn/guide_service.html'},
            {'title': '官方购票说明', 'url': 'https://www.tiantanpark.cn/en/index/detail/1312.html'},
        ],
        'booking_url': 'https://www.tiantanpark.cn/guide_service.html',
        'booking_label': '去官方购票',
        'booking_channel': '官方购票页提供微信扫码入口。',
        'notice': '公园入园与园内景点是不同范围，请核对票种。',
        'summary': [
            {'label': '票种范围', 'text': '请核对是否包含祈年殿、回音壁等园内景点，不能把公园入园等同于全部景点可进入。'},
            {'label': '景点闭园', 'text': '祈年殿等景点逢周一关闭，法定节假日期间例外；公园与景点的开放时间不同。'},
        ],
        'details': [],
    },
    'summer_palace': {
        'source_name': '颐和园官网',
        'sources': [{'title': '导览、票务与开放时间', 'url': 'https://www.summerpalace.net.cn/'}],
        'booking_url': None,
        'booking_label': None,
        'booking_channel': '已核对票种及开放范围；本条资料尚未核实可直接使用的官方线上购票入口。',
        'notice': '园中园单独收费，公园门票、联票的范围不同。',
        'summary': [
            {'label': '票种范围', 'text': '联票包含公园门票和园中园门票；门票、联票及园中园票均设优惠半价票。'},
            {'label': '景点闭园', 'text': '佛香阁、德和园、颐和园博物馆、苏州街通常周一关闭，重点节假日例外。'},
        ],
        'details': [],
    },
}


def admission_guide(city, place, visit_date, *, now=None):
    policy = matching_policy(city, place) or matching_park(city, place)
    if not policy or policy['key'] not in GUIDES:
        return None
    now = now or datetime.now(CHINA)
    if now.tzinfo is None:
        raise ValueError('Reference time must have a timezone')
    visiting = date.fromisoformat(visit_date) if isinstance(visit_date, str) else visit_date
    fresh = 0 <= (now.astimezone(CHINA).date() - REVIEWED).days <= REFERENCE_DAYS
    result = deepcopy(GUIDES[policy['key']])
    result.update(status='reviewed' if fresh else 'expired', reviewed_on=REVIEWED.isoformat(),
                  valid_until=(REVIEWED + timedelta(days=REFERENCE_DAYS)).isoformat(),
                  visit_date=visiting.isoformat(), booking_status='unverified', booking_opens_at=None)
    if not fresh:
        result.update(summary=[], details=[], notice='官方资料已超过复核期限，请先核对当前官方规则。')
        return result
    if 'inner_names' in policy:
        result['details'].append({'label': '所选季节', 'text': park_message(policy, visiting.isoformat())})
    if policy['key'] == 'palace' and visiting.weekday() != 0:
        opening_day = visiting - timedelta(days=7)
        result['booking_opens_at'] = f'{opening_day}T20:00:00+08:00'
    return result
