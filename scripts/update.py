"""Collect completed local sessions, validate, then publish one atomic feed.

Foreign country activity is a named tracking sample, not a whole-market index.
Actual exchange index levels are independent vendor quotes. No credentials needed.
"""
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
from pathlib import Path
import statistics as st
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from model_v1 import sequence, context

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config.json').read_text())['markets']

def read_json(url):
    last = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent':'Mozilla/5.0 MOVENT/1.0'}), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last = e
            if attempt < 2: time.sleep(2 * (attempt + 1))
    raise last

def chart(symbol):
    data = read_json('https://query1.finance.yahoo.com/v8/finance/chart/' + quote(symbol, safe='') + '?range=1y&interval=1d')
    result = data.get('chart', {}).get('result')
    if not result: raise ValueError('No chart: ' + symbol)
    return result[0]

def parse_bars(result, cfg, now):
    if result.get('meta', {}).get('currency') != cfg['currency']:
        raise ValueError('Unexpected currency')
    zone = ZoneInfo(cfg['timezone'])
    local_now = now.astimezone(zone)
    quotes = result['indicators']['quote'][0]
    bars = {}
    for i, timestamp in enumerate(result.get('timestamp', [])):
        day = dt.datetime.fromtimestamp(timestamp, zone).date()
        if day > local_now.date() or day == local_now.date() and local_now.hour < cfg['finalHour']: continue
        close = quotes.get('close', [])[i]
        volume = quotes.get('volume', [])[i]
        if not isinstance(close, (float,int)) or not math.isfinite(close) or close <= 0: continue
        bars[day.isoformat()] = {'close':close, 'volume':volume if isinstance(volume,(float,int)) and math.isfinite(volume) and volume>0 else None}
    return bars

def schedule(cfg, now):
    import exchange_calendars as xc
    start = dt.date(now.year, 1, 1)
    end = dt.date(now.year, 12, 31)
    cal = xc.get_calendar(cfg['calendar'], start=start.isoformat(), end=end.isoformat())
    sessions = {d.date().isoformat() for d in cal.sessions}
    holidays=[]
    day=start
    while day <= end:
        if day.weekday()<5 and day.isoformat() not in sessions: holidays.append(day.isoformat())
        day+=dt.timedelta(days=1)
    return {'timezone':cfg['timezone'], 'finalHour':cfg['finalHour'], 'validFrom':start.isoformat(), 'validThrough':end.isoformat(), 'holidays':holidays}

def stock_row(meta, bars, dates, code, cfg):
    ticker, company, sector, industry = meta
    values = [bars[d]['close']*bars[d]['volume'] if d in bars and bars[d]['volume'] else None for d in dates]
    rows = sequence(values, [False]*len(values))
    row=rows[-1]
    if not row: return None
    count, observed, streak = context(rows, len(rows)-1)
    def change(n):
        earlier=rows[-1-n] if len(rows)>n else None
        return round(row['score']-earlier['score'],1) if earlier else None
    venue={'KR':'XKRX','JP':'XTKS','TW':'XTAI','HK':'XHKG','CN':'XSHG' if ticker.endswith('.SS') else 'XSHE'}[code]
    # Stable category slugs are local to the country and never point to US categories.
    sector_slug=code.lower()+'-s-'+sector
    industry_slug=code.lower()+'-i-'+industry
    return {'date':dates[-1],'ticker':ticker,'company':company,'companyEn':ticker,'sector':sector,'sectorSlug':sector_slug,'industry':industry,'industrySlug':industry_slug,
      'price':bars[dates[-1]]['close'],'priceChange':round(100*(bars[dates[-1]]['close']/bars[dates[-2]]['close']-1),2) if dates[-2] in bars else None,
      'dollarVolume':values[-1],'relativeActivity':round(row['ratio'],3),'ratio20':round(values[-1]/st.median(values[-21:-1]),3),
      'movent':round(row['score'],1),'activity':round(row['score'],1),'robustZ':round(row['z'],4),'baseline':round(row['baseline'],2),'anomalous':row['anomalous'],
      'persistence':round(100*count/observed,1) if observed else 0,'days5':count,'observedDays5':observed,'streak':streak,'days20':sum(bool(r and r['confirmed']) for r in rows[-20:]),
      'history':[{'date':dates[i],'movent':round(r['score'],1),'price':bars[dates[i]]['close'],'estimated':False} for i,r in enumerate(rows) if r][-60:],
      'change1d':change(1),'change5d':change(5),'change20d':change(20),'breadthCount':0,'breadthTotal':0,'breadth':0,'breadthAvailable':False,'breadthSource':'industry',
      'breadthChange5d':None,'baselineEstimated':0,'quality':'observed','sensitivity':0,'industryAvailable':False,'industryRoute':None,'sectorRoute':None,
      'countryCode':code,'currency':cfg['currency'],'exchangeCode':venue,'localCode':ticker.split('.')[0],'instrumentId':f'{code}:{venue}:{ticker}',
      'marketCap':None,'marketCapBucket':'unknown','indexMemberships':[]}

def validate_country(data, old=None):
    stocks=data['stocks']
    if len(stocks)<5: raise ValueError('Fewer than five eligible stocks')
    if old and data['asOf']<old['asOf']: raise ValueError('Refusing date regression')
    if old and len(stocks)<.85*len(old['stocks']): raise ValueError('Coverage dropped more than 15%')
    if len({s['ticker'] for s in stocks})!=len(stocks):raise ValueError('Duplicate ticker')
    for s in stocks:
        if s['date']!=data['asOf'] or not math.isfinite(s['movent']) or not 0<=s['movent']<=100: raise ValueError('Invalid stock')
        if not s['history'] or s['history'][-1]['date']!=data['asOf'] or s['history'][-1]['movent']!=s['movent']: raise ValueError('History mismatch')
        if not 0<=s['days5']<=s['observedDays5']<=5:raise ValueError('Invalid persistence')
    return data

def foreign_country(code, cfg, fetched, now, previous):
    # The broad market index supplies an independent local session grid.
    reference=parse_bars(fetched[cfg['indices'][0][2]], cfg, now)
    dates=sorted(reference)
    if len(dates)<81:raise ValueError('Insufficient reference sessions')
    stocks=[];tracked=[]
    for meta in cfg['stocks']:
        ticker=meta[0];row=None
        try:
            row=stock_row(meta,parse_bars(fetched[ticker],cfg,now),dates,code,cfg)
        except Exception:pass
        tracked.append({'ticker':ticker,'company':meta[1],'companyEn':ticker,'sector':meta[2],'industry':meta[3],'status':'eligible' if row else 'data_unavailable','reason':None if row else '연속 60거래일 관측·유동성 조건 부족 또는 수집 실패'})
        if row:stocks.append(row)
    if len(stocks)<max(5,math.ceil(.75*len(cfg['stocks']))):raise ValueError('Less than 75% of configured sample')
    for stock in stocks:
        peers=[s for s in stocks if s['industry']==stock['industry'] and s['ticker']!=stock['ticker']]
        stock.update(breadthTotal=len(peers),breadthCount=sum(s['anomalous'] for s in peers),breadthAvailable=len(peers)>=5)
        stock['breadth']=round(100*stock['breadthCount']/len(peers),1) if peers else 0
    groups=lambda key:[{'slug':s[key+'Slug'],'name':s[key]} for i,s in enumerate(stocks) if s[key] not in {p[key] for p in stocks[:i]}]
    data={'asOf':dates[-1],'releaseStatus':'final','schedule':schedule(cfg,now),'currency':cfg['currency'],'sampleKind':'representative-sample','methodVersion':'v1.0','stocks':stocks,'tracked':tracked,
          'markets':[], 'sectors':groups('sector'),'industries':groups('industry'),'indices':index_quotes(cfg,fetched,now),
          'coverageNote':'선정한 대표 종목의 추적 표본입니다. 국가 전체나 지수 전체 구성종목을 포괄하지 않습니다. 공식 지수 시세와 MOVENT 표본 점수는 별개입니다.'}
    return validate_country(data,previous)

def index_quotes(cfg, fetched, now):
    results=[]
    for slug,name,symbol in cfg['indices']:
        try:
            bars=parse_bars(fetched[symbol],cfg,now);dates=sorted(bars)
            last=bars[dates[-1]]['close'];prev=bars[dates[-2]]['close']
            results.append({'slug':slug,'name':name,'symbol':symbol,'date':dates[-1],'price':round(last,2),'changePct':round(100*(last/prev-1),2),'status':'ready'})
        except Exception:
            results.append({'slug':slug,'name':name,'symbol':symbol,'date':None,'price':None,'changePct':None,'status':'unavailable'})
    return results

def us_country(cfg, fetched, now, previous):
    upstream=ROOT/'work'/'bvt'
    raw=json.loads((upstream/'data.json').read_text())
    if previous and raw['market_date']<previous['asOf']:raise ValueError('BVT date regression')
    normalized=ROOT/'work'/'normalized'
    subprocess.run([sys.executable,str(ROOT/'scripts/normalize_bvt_snapshots.py'),'--latest-data',str(upstream/'data.json'),'--history',str(upstream/'history.json'),'--snapshots',str(upstream/'replay_data/snapshots'),'--out',str(normalized)],check=True)
    from build_v1 import build
    from promote_artifact import validate
    payload,universe,_=build(normalized,upstream/'data.json')
    validate(payload)
    stocks=payload['stocks']
    membership={s['ticker']:[m['slug'] for m in payload['markets'] if s['ticker'] in m['constituents']] for s in stocks}
    for s in stocks:
        s.update(countryCode='US',currency='USD',companyEn=s['company'],indexMemberships=membership[s['ticker']],instrumentId='US:UNKNOWN:'+s['ticker'],exchangeCode=None,marketCap=None,marketCapBucket='unknown')
        # Country overview and stock explorer need a compact, current feed.
        s['history']=s['history'][-20:]
    return validate_country({'asOf':payload['asOf'],'releaseStatus':payload['releaseStatus'],'schedule':schedule(cfg,now),'currency':'USD','sampleKind':'bvt-universe','methodVersion':'v1.0','stocks':stocks,'tracked':universe['stocks'],
          'markets':payload['markets'],'sectors':payload['sectors'],'industries':payload['industries'],'indices':index_quotes(cfg,fetched,now),'coverageNote':'BVT 미국 추적 유니버스. 전체 미국 상장종목을 모두 포괄하지 않습니다.'},previous)

def atomic_write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    handle,tmp=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(handle,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def main():
    now=dt.datetime.now(dt.timezone.utc)
    path=ROOT/'data/global.json'
    previous=json.loads(path.read_text()) if path.exists() else {'countries':{}}
    symbols={s[0] for cfg in CONFIG.values() for s in cfg['stocks']} | {i[2] for cfg in CONFIG.values() for i in cfg['indices']}
    fetched={};failures={}
    def get(symbol):
        try:return symbol,chart(symbol),None
        except Exception as e:return symbol,None,str(e)
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for symbol,data,error in pool.map(get,sorted(symbols)):
            if data:fetched[symbol]=data
            else:failures[symbol]=error
    countries=dict(previous['countries']);errors={}
    for code,cfg in CONFIG.items():
        try:
            old=countries.get(code)
            countries[code]=us_country(cfg,fetched,now,old) if code=='US' else foreign_country(code,cfg,fetched,now,old)
            print(code,countries[code]['asOf'],len(countries[code]['stocks']),flush=True)
        except Exception as e:
            errors[code]=str(e)
            print(code,'PRESERVED:',e,flush=True)
    status={'checkedAt':now.isoformat(),'errors':errors,'failedSymbols':failures,'countries':{code:{'asOf':data['asOf'],'stocks':len(data['stocks'])} for code,data in countries.items()}}
    atomic_write(ROOT/'data/status.json',status)
    if not countries:raise RuntimeError('No valid country. Previous data preserved.')
    # A complete initial release is required; later failures preserve each country's last good data.
    if not previous['countries'] and set(countries)!=set(CONFIG):raise RuntimeError('Initial release incomplete: '+str(errors))
    current={'version':'movent-global/1','generatedAt':now.isoformat(),'source':'Yahoo Finance EOD / BVT','countries':countries}
    atomic_write(path,current)
    if errors:print('Partial refresh: see data/status.json',flush=True)

if __name__=='__main__':main()
