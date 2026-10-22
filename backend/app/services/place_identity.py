"""Lightweight POI identity helpers; safe for map adapters without Agent imports."""
import re


def normalized(name):
    return re.sub(r"[\s（）()·—-]", "", name).casefold()


def parent_id(value, own_id=None):
    if not isinstance(value, str) or value in {'', '0'} or value == own_id:
        return None
    return value if re.fullmatch(r'[A-Za-z0-9]{1,80}', value) else None
