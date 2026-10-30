"""Versioned RSS metadata and point-in-time event observations; no article bodies.
Run after market refresh or standalone. Publisher times are never inferred from fetch time.
"""
import datetime as dt
import hashlib
from difflib import SequenceMatcher
import json
import re
import statistics as st
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
VERSION = 'news-research/1'
SOURCES = [
 {'id':'samsung-official','publisher':'삼성전자 뉴스룸','country':'KR','url':'https://news.samsung.com/kr/feed','officialTicker':'005930.KS'},
 {'id':'hankyung-finance','publisher':'한국경제','country':'KR','url':'https://www.hankyung.com/feed/finance'},
 {'id':'hankyung-economy','publisher':'한국경제','country':'KR','url':'https://www.hankyung.com/feed/economy'},
 {'id':'marketwatch','publisher':'MarketWatch','country':'US','url':'https://feeds.content.dowjones.io/public/rss/mw_topstories'},
]
CATEGORIES = {'실적':r'실적|영업이익|매출|earnings|quarterly results', '수주':r'수주|공급계약|contract award', '투자':r'설비투자|공장.*증설|capital expenditure|investment', '인수합병':r'인수|합병|acquisition|merger', '증자':r'유상증자|신주발행|share offering', '규제':r'규제|과징금|제재|regulat|antitrust', '배당·자사주':r'배당|자사주|dividend|buyback'}
def stamp(): return dt.datetime.now(UTC).isoformat()
def digest(text): return hashlib.sha256(text.encode()).hexdigest()[:24]
def canonical(url):
 p=urlsplit(url)
 if p.scheme not in ('http','https') or not p.netloc:return None
 return urlunsplit((p.scheme,p.netloc.lower(),p.path,urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]),''))
def timestamp(value):
 if not value:return None
 try:
  try:d=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
  except ValueError:d=parsedate_to_datetime(value)
  return d.astimezone(UTC).isoformat() if d.tzinfo else None
 except (ValueError,TypeError,OverflowError):return None

def parse_feed(xml, source):
 root=ET.fromstring(xml);out=[]
 for item in list(root.findall('.//item'))+list(root.findall('{http://www.w3.org/2005/Atom}entry')):
  def field(*names):
   for child in item:
    if child.tag.split('}')[-1] in names:
     return child.attrib.get('href') or ''.join(child.itertext()).strip()
   return None
  title=field('title');url=canonical(field('link') or '')
  if not title or not url:continue
  out.append({'id':digest(url),'title':title,'url':url,'publisher':source['publisher'],'country':source['country'],'feedId':source['id'],'publishedAt':timestamp(field('pubDate','published')),'modifiedAt':timestamp(field('updated','modified')),'officialAt':None,'officialSourceUrl':None,'sourceKind':'news'})
  if source.get('officialTicker'):
   out[-1].update(sourceKind='official',officialAt=out[-1]['publishedAt'],officialSourceUrl=url,officialTicker=source['officialTicker'])
 return out

def entities(title, country, stocks):
 matches=[]
 for s in stocks:
  if s.get('countryCode',country)!=country:continue
  aliases={s.get('company',''),s.get('companyEn','')}
  if any(len(a.strip())>=3 and a!=s['ticker'] and a.casefold() in title.casefold() and re.search(r'(?<![\w가-힣])'+re.escape(a)+r'(?![\w가-힣])',title,re.I) for a in aliases):matches.append(s['ticker'])
 return sorted(set(matches))
def classify(title):return [name for name,pattern in CATEGORIES.items() if re.search(pattern,title,re.I)] or ['기타']
def cluster_key(article):
 # Conservative: same date, company set, normalized headline and numbers. No same-day blanket merging.
 title=re.sub(r'\[[^]]*\]|\([^)]*\)','',article['title']).casefold()
 return digest('|'.join([article['country'],str(article.get('publishedAt',''))[:10],','.join(article['tickers']),re.sub(r'[^\w가-힣]','',title)]))
def ingest(old, incoming, countries, observed):
 articles={a['id']:a for a in old}
 for a in incoming:
  old_a=articles.get(a['id']);a['tickers']=[a['officialTicker']] if a.get('officialTicker') else entities(a['title'],a['country'],countries.get(a['country'],{}).get('stocks',[]))
  a['entityStatus']='verified' if a.get('officialTicker') else 'candidate' if len(a['tickers'])==1 else 'review';a['categories']=classify(a['title'])
  a['firstSeenAt']=old_a['firstSeenAt'] if old_a else observed;a['lastSeenAt']=observed
  a['revisions']=(old_a.get('revisions',[]) if old_a else [])
  if old_a and any(old_a.get(k)!=a.get(k) for k in ['title','publishedAt','modifiedAt']):a['revisions']=a['revisions']+[ {k:old_a.get(k) for k in ['title','publishedAt','modifiedAt','lastSeenAt']} ]
  a['clusterId']=cluster_key(a);articles[a['id']]=a
 return list(articles.values())
def reaction_session(event, country):
 """First session exposed to announcement. Close equality belongs to next session."""
 t=timestamp(event.get('officialAt') or event.get('publishedAt'))
 if not t:return None,'발표 시각 미확인'
 schedule=country['schedule'];local=dt.datetime.fromisoformat(t).astimezone(ZoneInfo(schedule['timezone']));day=local.date()
 close={'KR':(15,30),'US':(16,0),'JP':(15,30),'CN':(15,0),'HK':(16,0),'TW':(13,30)}[event['country']]
 if (local.hour,local.minute)>=close:day+=dt.timedelta(days=1);phase='장 마감 후'
 else:phase='장 시작 전' if local.hour<9 or event['country']=='US' and (local.hour,local.minute)<(9,30) else '장중'
 for _ in range(15):
  key=day.isoformat()
  if not schedule['validFrom']<=key<=schedule['validThrough']:return None,'거래소 달력 부족'
  if day.weekday()<5 and key not in schedule.get('holidays',[]):return key,phase
  day+=dt.timedelta(days=1)
 return None,'거래소 달력 부족'
def event_analysis(event, country, stock):
 session,phase=reaction_session(event,country)
 history=sorted(stock.get('history',[]),key=lambda p:p['date']);by={p['date']:p for p in history}
 dates=sorted({p['date'] for s in country['stocks'] for p in s.get('history',[])})
 if not session or session not in dates:return {'status':'반응 확인 전' if session and session>country['asOf'] else '자료 부족','session':session,'phase':phase,'windows':[]}
 index=dates.index(session);base=by.get(dates[index-1]) if index else None
 windows=[]
 def ret(s,end):
  points={p['date']:p for p in s.get('history',[])};b=points.get(dates[index-1]) if index else None;p=points.get(end)
  return (p['price']/b['price']-1)*100 if b and p and b.get('price') and p.get('price') and not b.get('estimated') and not p.get('estimated') else None
 peers=[s for s in country['stocks'] if s['ticker']!=stock['ticker'] and s.get('industry')==stock.get('industry') and stock.get('industry') not in (None,'업종 미분류','기타')]
 market=[s for s in country['stocks'] if s['ticker']!=stock['ticker']]
 for offset in [-5,-4,-3,-2,-1,0,2,4]:
  pos=index+offset
  if pos<0 or pos>=len(dates):windows.append({'label':f'후 {offset+1}일' if offset>=0 else f'전 {-offset}일','status':'자료 부족' if pos<0 else '반응 확인 전'});continue
  day=dates[pos];p=by.get(day);r=ret(stock,day)
  comparable=[ret(s,day) for s in peers];comparable=[v for v in comparable if v is not None]
  m=[ret(s,day) for s in market];m=[v for v in m if v is not None]
  windows.append({'label':f'후 {offset+1}일' if offset>=0 else f'전 {-offset}일','date':day,'status':'관측' if p and not p.get('estimated') else '자료 부족','movent':p.get('movent') if p and not p.get('estimated') else None,'ratio20':p.get('ratio20') if p else None,'returnPct':r,'peerCount':len(comparable),'marketCount':len(m),'peerExcessPct':r-st.mean(comparable) if r is not None and len(comparable)>=5 else None,'marketExcessPct':r-st.mean(m) if r is not None and len(m)>=5 else None})
 observed=[by[d] for d in dates[index:index+5] if d in by and not by[d].get('estimated')]
 flags=[p.get('anomalous') for p in observed]
 pre=[by[d] for d in dates[max(0,index-5):index] if d in by and not by[d].get('estimated')]
 follow='자료 부족'
 if observed and all(type(x) is bool for x in flags):
  follow='반복 발생' if sum(flags)>=2 else '일시적 발생' if any(flags) else '거래 활동 발생 없음'
  if len(flags)>=2 and any(flags[:-1]) and flags[-1] is False:follow='발생 종료'
 return {'status':'관측','session':session,'phase':phase,'timeBasis':'공식 발표' if event.get('officialAt') else '기사 게시 · 공식 시각 미확인','windows':windows,'followUp':follow,'preEventActivity':any(p.get('anomalous') is True for p in pre) if len(pre)==5 else None,'peerBasis':'본인 제외 동일 원본 업종 · 5개 이상','peerCount':len(peers),'causality':'동시 관측이며 인과관계는 확인되지 않았습니다.'}

def build_events(articles,countries):
 clusters={}
 for a in articles:
  key=a['clusterId']
  if a['tickers']:
   for prior,group in clusters.items():
    b=group[0]
    if a['tickers']==b['tickers'] and a['categories']==b['categories'] and str(a.get('publishedAt'))[:10]==str(b.get('publishedAt'))[:10] and re.findall(r'\d+',a['title'])==re.findall(r'\d+',b['title']) and SequenceMatcher(None,a['title'],b['title']).ratio()>=.92:
     key=prior;break
  clusters.setdefault(key,[]).append(a)
 events=[]
 for key,group in clusters.items():
  group.sort(key=lambda a:a.get('publishedAt') or a['firstSeenAt']);a=group[0];country=countries.get(a['country']);e={**a,'id':key,'sources':[{k:s.get(k) for k in ['id','title','url','publisher','publishedAt','modifiedAt','firstSeenAt']} for s in group]}
  stock=next((s for s in country.get('stocks',[]) if a['tickers']==[s['ticker']]),None) if country else None
  e['analysis']=event_analysis(e,country,stock) if stock else {'status':'기업 연결 검토','windows':[]}
  # Candidate linkage is explicit; never silently promote a title mention to an official issuer event.
  e['linkageNote']='제목의 정확한 기업명 일치 후보 · 공식 종목 연결 검토 필요' if stock else '동명·비상장·미지원 기업 여부 확인 필요'
  events.append(e)
 return sorted(events,key=lambda e:e.get('publishedAt') or e['firstSeenAt'],reverse=True)

def write(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False));temp.replace(path)
def main():
 observed=stamp();feed=json.loads((ROOT/'data/global.json').read_text());path=ROOT/'data/news/articles.json';old=json.loads(path.read_text()) if path.exists() else [];incoming=[];errors={}
 for source in SOURCES:
  try:
   with urlopen(Request(source['url'],headers={'User-Agent':'MOVENT news research metadata collector (+https://movent.kr)'}),timeout=25) as response:incoming+=parse_feed(response.read(2_000_000),source)
  except Exception as error:errors[source['id']]=str(error)[:200]
 articles=ingest(old,incoming,feed['countries'],observed)
 for review in json.loads((ROOT/'data/news/reviews.json').read_text()):
  for a in articles:
   if a['id']==review['articleId']:
    if review.get('verifiedTickers') is not None:a.update(tickers=review['verifiedTickers'],entityStatus='verified',reviewedAt=observed)
    if timestamp(review.get('officialAt')) and canonical(review.get('officialSourceUrl','')):a.update(officialAt=timestamp(review['officialAt']),officialSourceUrl=canonical(review['officialSourceUrl']))
 events=build_events(articles,feed['countries'])
 write(path,articles)
 write(ROOT/'data/news/observations'/f'{observed[:10]}-{observed[11:19].replace(":","")}.json',{'observedAt':observed,'articles':articles})
 write(ROOT/'data/news/events.json',{'version':VERSION,'generatedAt':observed,'marketRevision':feed['generatedAt'],'sources':SOURCES,'errors':errors,'events':events,'validation':{'status':'표본 축적 중 · 성능 미검증','pointInTimeSince':min((a['firstSeenAt'] for a in articles),default=observed),'cohortMinimum':30,'cohortStatus':'기업 연결 검토 및 기업 규모·발표 규모 확보 후 공개','holdoutStatus':'평가 미실행'}})
 # Preserve exact published market snapshots (including unchanged/no-reaction events).
 for code,country in feed['countries'].items():
  target=ROOT/'data/history'/code/(country['asOf']+'.json')
  if not target.exists():write(target,{'firstArchivedAt':observed,'data':country})
 print(json.dumps({'articles':len(articles),'events':len(events),'errors':errors},ensure_ascii=False))
if __name__=='__main__':main()
