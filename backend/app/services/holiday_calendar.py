"""Published break windows, used only to avoid applying holiday exceptions blindly.

Inside a break is NOT proof a venue opens. Uncovered years remain unknown.
"""
from datetime import date

SOURCE_URL='https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html'
DOCUMENT='国办发明电〔2025〕7号'
BREAKS={2026:(('01-01','01-03'),('02-15','02-23'),('04-04','04-06'),('05-01','05-05'),
              ('06-19','06-21'),('09-25','09-27'),('10-01','10-07'))}


def outside_published_breaks(day:date):
    if day.year not in BREAKS:return None
    return not any(f'{day.year}-{start}'<=day.isoformat()<=f'{day.year}-{end}' for start,end in BREAKS[day.year])
