"""Resolve primary schedules and retry only markets without the required session."""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo
from update import ROOT, CONFIG, completed_session_dates

def selected_countries(event, cron, country='all', now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    codes=list(CONFIG) if country=='all' else [country]
    if event!='schedule':return codes
    asia=cron in ('0 8 * * *','30 8 * * *','0 9 * * *')
    codes=[c for c in CONFIG if (c!='US')==asia]
    if cron in ('0 8 * * *','0 22 * * *'):return codes
    # Retry runs leave countries with the required validated session untouched.
    feed=json.loads((ROOT/'data/global.json').read_text())
    status_path=ROOT/'data/status.json'
    errors=json.loads(status_path.read_text()).get('errors',{}) if status_path.exists() else {}
    needed=[]
    for code in codes:
        cfg=CONFIG[code]
        local=now.astimezone(ZoneInfo(cfg['timezone'])).date()
        expected=completed_session_dates(cfg,now,(local-dt.timedelta(days=14)).isoformat())[-1]
        if code in errors or feed.get('countries',{}).get(code,{}).get('asOf','')<expected:needed.append(code)
    return needed

if __name__=='__main__':
    selected=selected_countries(os.environ.get('MOVENT_EVENT',''),os.environ.get('MOVENT_CRON',''),os.environ.get('MOVENT_COUNTRY','all'))
    # Discard transient quote caches before a retry of incomplete source data.
    if os.environ.get('MOVENT_EVENT')=='schedule' and os.environ.get('MOVENT_CRON') not in ('0 8 * * *','0 22 * * *'):
        import shutil
        shutil.rmtree(ROOT/'work/chart-cache',ignore_errors=True)
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('countries='+(','.join(selected) if selected else 'none')+'\n')
    print('Selected markets:',selected)
