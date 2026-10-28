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
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
import re
import gzip
import threading
from model_v1 import sequence, context, MIN_LIQUIDITY

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config.json').read_text())['markets']
_rate_lock=threading.Lock()
_next_request=0.0

def limit_requests():
    global _next_request
    with _rate_lock:
        now=time.monotonic();slot=max(now,_next_request);_next_request=slot+.25
    if slot>now:time.sleep(slot-now)

def read_json(url):
    last = None
    for attempt in range(3):
        try:
            limit_requests()
            with urlopen(Request(url, headers={'User-Agent':'Mozilla/5.0 MOVENT/1.0'}), timeout=18) as r:
                return json.load(r)
        except Exception as e:
            last = e
            if isinstance(e,HTTPError) and e.code in (400,404,410):raise
            if attempt < 2: time.sleep(2 * (attempt + 1))
    raise last

def chart(symbol):
    if symbol == '^TOPX': return topix_chart()
    path=ROOT/'work'/'chart-cache'/(quote(symbol,safe='')+'.json.gz')
    try:
        if time.time()-path.stat().st_mtime<1800:
            with gzip.open(path,'rt',encoding='utf-8') as f:return json.load(f)
    except (OSError,ValueError,EOFError):pass
    data = read_json('https://query1.finance.yahoo.com/v8/finance/chart/' + quote(symbol, safe='') + '?range=6mo&interval=1d')
    result = data.get('chart', {}).get('result')
    if not result: raise ValueError('No chart: ' + symbol)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    with gzip.open(temporary,'wt',encoding='utf-8') as f:json.dump(result[0],f,separators=(',',':'))
    os.replace(temporary,path)
    return result[0]

def topix_chart():
    class TableParser(HTMLParser):
        def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None
        def handle_starttag(self,tag,attrs):
            if tag=='tr':self.row=[]
            if tag in ('td','th') and self.row is not None:self.cell=''
        def handle_data(self,text):
            if self.cell is not None:self.cell+=text
        def handle_endtag(self,tag):
            if tag in ('td','th') and self.cell is not None:self.row.append(self.cell.strip());self.cell=None
            if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None
    url='https://finance.yahoo.co.jp/quote/998405.T/history'
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=30) as response:html=response.read().decode('utf-8')
    table=TableParser();table.feed(html)
    rows={}
    for row in table.rows:
        if len(row)>=5 and re.fullmatch(r'\d{4}/\d{1,2}/\d{1,2}',row[0]):
            date=dt.datetime.strptime(row[0],'%Y/%m/%d').replace(hour=9,tzinfo=ZoneInfo('Asia/Tokyo'))
            price=float(row[4].replace(',',''))
            if math.isfinite(price) and price>0:rows[int(date.timestamp())]=price
    dates=sorted(rows)
    if len(dates)<2:raise ValueError('TOPIX history unavailable')
    return {'meta':{'currency':'JPY','instrumentType':'INDEX','source':'Yahoo Finance Japan','sourceUrl':url},'timestamp':dates,'indicators':{'quote':[{'close':[rows[d] for d in dates],'volume':[None]*len(dates)}]}}

def parse_bars(result, cfg, now, require_currency=True):
    if require_currency and result.get('meta', {}).get('currency') != cfg['currency']:
        raise ValueError('Unexpected currency')
    zone = ZoneInfo(cfg['timezone'])
    local_now = now.astimezone(zone)
    quotes = result['indicators']['quote'][0]
    bars = {}
    for i, timestamp in enumerate(result.get('timestamp', [])):
        day = dt.datetime.fromtimestamp(timestamp, zone).date()
        if day > local_now.date() or day == local_now.date() and local_now.hour < cfg['finalHour']: continue
        close = quotes.get('close', [])[i]
        volumes=quotes.get('volume') or []
        volume = volumes[i] if i<len(volumes) else None
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
    ticker, company, sector, industry = meta[:4]
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
      'dollarVolume':values[-1],'relativeActivity':round(row['ratio'],3),'ratio20':round(values[-1]/st.median(values[-21:-1]),3),'medianTurnover20':st.median(values[-21:-1]),'medianTurnover20Basis':'observed-median',
      'movent':round(row['score'],1),'activity':round(row['score'],1),'robustZ':round(row['z'],4),'baseline':round(row['baseline'],2),'anomalous':row['anomalous'],
      'persistence':round(100*count/observed,1) if observed else 0,'days5':count,'observedDays5':observed,'streak':streak,'days20':sum(bool(r and r['confirmed']) for r in rows[-20:]),
      'history':[{'date':dates[i],'movent':round(r['score'],1),'price':bars[dates[i]]['close'],'estimated':False,'anomalous':r['confirmed']} for i,r in enumerate(rows) if r][-60:],
      'change1d':change(1),'change5d':change(5),'change20d':change(20),'breadthCount':0,'breadthTotal':0,'breadth':0,'breadthAvailable':False,'breadthSource':'industry',
      'breadthChange5d':None,'baselineEstimated':0,'quality':'observed','sensitivity':0,'industryAvailable':False,'industryRoute':None,'sectorRoute':None,
      'countryCode':code,'currency':cfg['currency'],'exchangeCode':venue,'localCode':ticker.split('.')[0],'instrumentId':f'{code}:{venue}:{ticker}',
      'marketCap':None,'marketCapBucket':'unknown','indexMemberships':[],'logoDomain':meta[4] if len(meta)>4 else None}

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

def unavailable_reason(bars,dates,currency):
    current=bars.get(dates[-1])
    if not current or not current.get('volume'):
        return '기준 거래일의 확정 종가 또는 유효 거래량이 없습니다.'
    baseline=[bars[d]['close']*bars[d]['volume'] for d in dates[-61:-1] if d in bars and bars[d].get('volume')]
    if len(baseline)<60:
        return f'직전 연속 60거래일의 유효 거래대금 관측이 부족합니다 ({len(baseline)}/60일).'
    if st.median(baseline)<MIN_LIQUIDITY:
        return f'직전 60거래일 중앙 거래대금이 최소 조건 {MIN_LIQUIDITY:,.0f} {currency} 미만입니다.'
    return '가격·거래량 관측값이 계산 검증을 통과하지 못했습니다.'

def foreign_country(code, cfg, fetched, now, previous):
    if len(cfg['stocks'])<cfg.get('minimumTracked',0):raise ValueError('Tracking universe below validated minimum; preserve last good country')
    # The broad market index supplies an independent local session grid.
    reference=parse_bars(fetched[cfg['indices'][0][2]], cfg, now)
    dates=sorted(reference)
    if len(dates)<81:raise ValueError('Insufficient reference sessions')
    stocks=[];tracked=[]
    for meta in cfg['stocks']:
        ticker=meta[0];row=None;reason='시세 공급원에서 가격·거래량 이력을 가져오지 못했습니다. 다음 수집 때 다시 확인합니다.'
        if ticker in fetched:
            try:
                bars=parse_bars(fetched[ticker],cfg,now)
                reason=unavailable_reason(bars,dates,cfg['currency'])
                row=stock_row(meta,bars,dates,code,cfg)
            except Exception:reason='현지 통화·거래일 또는 가격·거래량 검증을 통과하지 못했습니다.'
        tracked.append({'ticker':ticker,'company':meta[1],'companyEn':ticker,'sector':meta[2],'industry':meta[3],'status':'eligible' if row else 'data_unavailable','reason':None if row else reason})
        if row:stocks.append(row)
    if len(stocks)<5:raise ValueError('Fewer than five validated observations')
    industries={};sectors={}
    for s in stocks:
        industries.setdefault(s['industry'],[]).append(s)
        sectors.setdefault(s['sector'],[]).append(s)
    for stock in stocks:
        peers=industries.get(stock['industry'],[]) if stock['industry']!='업종 미분류' else []
        if len(peers)<6:
            peers=sectors.get(stock['sector'],[]) if stock['sector']!='업종 미분류' else []
            stock['breadthSource']='sector_fallback'
        peers=[s for s in peers if s['ticker']!=stock['ticker']]
        stock.update(breadthTotal=len(peers),breadthCount=sum(s['anomalous'] for s in peers),breadthAvailable=len(peers)>=5)
        stock['breadth']=round(100*stock['breadthCount']/len(peers),1) if peers else 0
    groups=lambda key:list({s[key]:{'slug':s[key+'Slug'],'name':s[key]} for s in stocks}.values())
    data={'asOf':dates[-1],'releaseStatus':'final','schedule':schedule(cfg,now),'currency':cfg['currency'],'sampleKind':cfg.get('sampleKind','representative-sample'),'methodVersion':'v1.0','stocks':stocks,'tracked':tracked,
          'markets':market_groups(code,cfg,stocks), 'sectors':groups('sector'),'industries':groups('industry'),'indices':index_quotes(cfg,fetched,now),
          'coverageNote':'공개 지수 구성목록과 거래소 상장기업을 기반으로 확장한 추적 유니버스입니다. 지수별 구성 확인일과 출처를 표시하며 ETF 보유종목은 추종 표본으로 구분합니다. 모든 상장종목 또는 모든 지수 구성종목의 수집을 보장하지 않습니다.'}
    return validate_country(data,previous)

def market_groups(code,cfg,stocks):
    definitions=json.loads((ROOT/'config.json').read_text()).get('marketGroups',{})
    groups=[]
    for slug,group in definitions.items():
        if group['countryCode']!=code:continue
        member_set=set(group.get('members',[]))
        tracked=[s[0] for s in cfg['stocks'] if s[0] in member_set or group.get('suffix') and s[0].endswith(group['suffix'])]
        constituents=[s['ticker'] for s in stocks if s['ticker'] in tracked]
        if not constituents:continue
        for stock in stocks:
            if stock['ticker'] in member_set and slug not in stock['indexMemberships']:stock['indexMemberships'].append(slug)
        groups.append({'slug':slug,'name':group['name'],'sourceTotal':group.get('sourceTotal',len(tracked)),'constituents':constituents,'scope':group['scope'],'sourceUrl':group['sourceUrl'],'membershipAsOf':group.get('membershipAsOf')})
    return groups

def previous_exchange_session(day,cfg,now):
    calendar=schedule(cfg,now);day=dt.date.fromisoformat(day)-dt.timedelta(days=1)
    for _ in range(15):
        if day.weekday()<5 and day.isoformat() not in calendar['holidays']:return day.isoformat()
        day-=dt.timedelta(days=1)
    raise ValueError('No verified previous exchange session')

def index_quotes(cfg, fetched, now):
    results=[]
    for slug,name,symbol in cfg['indices']:
        try:
            result=fetched[symbol];meta=result.get('meta',{})
            bars=parse_bars(result,cfg,now,require_currency=False);dates=sorted(bars)
            date_basis='completed-daily-bar'
            if not dates:
                # Some index vendors expose a live quote only. Their explicit prior
                # close is a completed-session observation, not today's intraday price.
                timestamps=result.get('timestamp',[])
                if len(timestamps)!=1 or meta.get('instrumentType')!='INDEX':raise ValueError('No completed index observation')
                local=dt.datetime.fromtimestamp(timestamps[0],ZoneInfo(cfg['timezone']))
                if local.date()!=now.astimezone(ZoneInfo(cfg['timezone'])).date():raise ValueError('Unverified live quote date')
                prior=meta.get('chartPreviousClose');live=meta.get('regularMarketPrice');ret=meta.get('regularMarketChangePercent')
                if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (prior,live,ret)) or prior<=0 or abs(100*(live/prior-1)-ret)>.05:raise ValueError('Prior close validation failed')
                dates=[previous_exchange_session(local.date().isoformat(),cfg,now)]
                bars={dates[0]:{'close':prior,'volume':None}}
                date_basis='vendor-previous-close-and-exchange-calendar'
            last=bars[dates[-1]]['close']
            prev=bars[dates[-2]]['close'] if len(dates)>1 else None
            change=round(100*(last/prev-1),2) if isinstance(prev,(int,float)) and math.isfinite(prev) and prev>0 else None
            meta=fetched[symbol].get('meta',{})
            results.append({'slug':slug,'name':name,'symbol':'998405.T' if symbol=='^TOPX' else symbol,'date':dates[-1],'price':round(last,2),'changePct':change,'status':'ready','source':meta.get('source','Yahoo Finance'),'sourceUrl':meta.get('sourceUrl'),'dateBasis':date_basis})
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
    raw_by_ticker={s['t']:s for s in raw['stocks']}
    def cap_info(ticker):
        value=raw_by_ticker.get(ticker,{}).get('mc')
        valid=isinstance(value,(int,float)) and math.isfinite(value) and value>0
        return {'marketCap':value if valid else None,'marketCapBucket':'large' if valid and value>=10_000_000_000 else 'mid' if valid and value>=2_000_000_000 else 'small' if valid else 'unknown'}
    for s in stocks:s.update(cap_info(s['ticker']))
    for s in universe['stocks']:s.update(cap_info(s['ticker']))
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
    global CONFIG
    now=dt.datetime.now(dt.timezone.utc)
    from expand_universe import expand
    CONFIG=expand(now)['markets']
    path=ROOT/'data/global.json'
    previous=json.loads(path.read_text()) if path.exists() else {'countries':{}}
    symbols={s[0] for cfg in CONFIG.values() for s in cfg['stocks']} | {i[2] for cfg in CONFIG.values() for i in cfg['indices']}
    fetched={};failures={}
    def get(symbol):
        try:return symbol,chart(symbol),None
        except Exception as e:return symbol,None,str(e)
    definitions=json.loads((ROOT/'config.json').read_text())
    priority={i[2] for cfg in CONFIG.values() for i in cfg['indices']}
    priority.update(t for group in definitions.get('marketGroups',{}).values() if group.get('scope')!='board-sample' for t in group.get('members',[]))
    ordered=sorted(symbols,key=lambda symbol:(symbol not in priority,symbol))
    # Concurrency absorbs latency while the shared limiter caps starts at 4/s.
    with cf.ThreadPoolExecutor(max_workers=48) as pool:
        for count,(symbol,data,error) in enumerate(pool.map(get,ordered),1):
            if data:fetched[symbol]=data
            else:failures[symbol]=error
            if count%200==0:print('Collected',count,'/',len(symbols),'symbols;',len(failures),'unavailable',flush=True)
    countries=dict(previous['countries']);errors={}
    for data in countries.values():data.setdefault('sourceRevision',previous.get('generatedAt',now.isoformat()))
    for code,cfg in CONFIG.items():
        try:
            old=countries.get(code)
            countries[code]=us_country(cfg,fetched,now,old) if code=='US' else foreign_country(code,cfg,fetched,now,old)
            countries[code]['lastSuccessAt']=dt.datetime.now(dt.timezone.utc).isoformat()
            countries[code]['sourceRevision']=countries[code]['lastSuccessAt']
            if old:
                prior={q['slug']:q for q in old.get('indices',[])}
                countries[code]['indices']=[prior[q['slug']] if q['price'] is None and prior.get(q['slug'],{}).get('price') is not None else q for q in countries[code]['indices']]
            print(code,countries[code]['asOf'],len(countries[code]['stocks']),flush=True)
        except Exception as e:
            errors[code]=str(e)
            print(code,'PRESERVED:',e,flush=True)
    completed=dt.datetime.now(dt.timezone.utc).isoformat()
    status={'startedAt':now.isoformat(),'checkedAt':completed,'universeRefresh':definitions.get('universeRefresh',{}),'errors':errors,'failedSymbols':failures,'countries':{code:{'asOf':data['asOf'],'stocks':len(data['stocks'])} for code,data in countries.items()}}
    atomic_write(ROOT/'data/status.json',status)
    if not countries:raise RuntimeError('No valid country. Previous data preserved.')
    # A complete initial release is required; later failures preserve each country's last good data.
    if not previous['countries'] and set(countries)!=set(CONFIG):raise RuntimeError('Initial release incomplete: '+str(errors))
    current={'version':'movent-global/1','generatedAt':completed,'source':'Yahoo Finance EOD / BVT','countries':countries}
    atomic_write(path,current)
    from publish_views import publish
    publish(current,ROOT,atomic_write)
    if errors:print('Partial refresh: see data/status.json',flush=True)

if __name__=='__main__':main()
