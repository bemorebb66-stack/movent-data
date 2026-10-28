"""Refresh public index/listing universes; keep each source's last verified list.

Only company/security facts are retained. Cash, futures, funds and SPACs are
excluded. ETF holdings are explicitly a proxy, never official index membership.
"""
import concurrent.futures as cf
import datetime as dt
import html
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
UNKNOWN='업종 미분류'
SECTORS={
 '金融':'금융','金融地产':'금융','金融地產':'금융','Financials':'금융','Banks':'금융','Banking':'금융','Insurance':'금융','Other Financial Services':'금융','Securities':'금융',
 '信息技术':'정보기술','信息技術':'정보기술','Information Technology':'정보기술','Electric Machinery':'정보기술','Precision Instruments':'정보기술','Electric Appliances':'정보기술',
 '电信业务':'통신서비스','電信業務':'통신서비스','Communication Services':'통신서비스','Information and Communications':'통신서비스','Information & Communication':'통신서비스',
 '工业':'산업재','工業':'산업재','Industrials':'산업재','Machinery':'산업재','Construction':'산업재','Shipbuilding':'산업재','Transportation Equipment':'산업재','Railway & Bus':'산업재','Land Transport':'산업재','Marine Transport':'산업재','Air Transport':'산업재','Warehousing':'산업재','Trading Companies':'산업재',
 '医药卫生':'헬스케어','醫藥衛生':'헬스케어','Healthcare':'헬스케어','Health Care':'헬스케어','Pharmaceuticals':'헬스케어',
 '可选消费':'경기소비재','可選消費':'경기소비재','Consumer Discretionary':'경기소비재','Automobiles & Auto parts':'경기소비재','Retail':'경기소비재','Retail Trade':'경기소비재','Services':'경기소비재','Other Manufacturing':'경기소비재',
 '主要消费':'필수소비재','主要消費':'필수소비재','Consumer Staples':'필수소비재','Foods':'필수소비재','Fishery':'필수소비재',
 '原材料':'소재','Materials':'소재','Chemicals':'소재','Steel':'소재','Nonferrous Metals':'소재','Textiles & Apparel':'소재','Pulp & Paper':'소재','Glass & Ceramics':'소재','Rubber':'소재',
 '能源':'에너지','Energy':'에너지','Mining':'에너지','Petroleum':'에너지','公用事业':'유틸리티','公用事業':'유틸리티','Utilities':'유틸리티','Electric Power':'유틸리티','Gas':'유틸리티','房地产':'부동산','房地產':'부동산','Real Estate':'부동산',
}
JP_SECTORS={'水産・農林業':'필수소비재','鉱業':'에너지','建設業':'산업재','食料品':'필수소비재','繊維製品':'소재','パルプ・紙':'소재','化学':'소재','医薬品':'헬스케어','石油・石炭製品':'에너지','ゴム製品':'소재','ガラス・土石製品':'소재','鉄鋼':'소재','非鉄金属':'소재','金属製品':'소재','機械':'산업재','電気機器':'정보기술','輸送用機器':'경기소비재','精密機器':'정보기술','その他製品':'경기소비재','電気・ガス業':'유틸리티','陸運業':'산업재','海運業':'산업재','空運業':'산업재','倉庫・運輸関連業':'산업재','情報・通信業':'통신서비스','卸売業':'산업재','小売業':'경기소비재','銀行業':'금융','証券、商品先物取引業':'금융','保険業':'금융','その他金融業':'금융','不動産業':'부동산','サービス業':'경기소비재'}
TW_INDUSTRIES={'01':('소재','시멘트'),'02':('필수소비재','식품'),'03':('소재','플라스틱'),'04':('소재','섬유'),'05':('산업재','전기·기계'),'06':('정보기술','전선·케이블'),'08':('소재','유리·세라믹'),'09':('소재','제지'),'10':('소재','철강'),'11':('소재','고무'),'12':('경기소비재','자동차'),'14':('산업재','건설'),'15':('산업재','운송'),'16':('경기소비재','관광'),'17':('금융','금융·보험'),'18':('경기소비재','무역·유통'),'20':('기타','기타'),'21':('소재','화학'),'22':('헬스케어','바이오·의료'),'23':('에너지','석유·가스'),'24':('정보기술','반도체'),'25':('정보기술','컴퓨터·주변기기'),'26':('정보기술','광전자'),'27':('통신서비스','통신·네트워크'),'28':('정보기술','전자부품'),'29':('정보기술','전자유통'),'30':('정보기술','정보서비스'),'31':('정보기술','기타 전자'),'35':('에너지','친환경 에너지'),'36':('통신서비스','디지털 콘텐츠'),'37':('경기소비재','스포츠·레저'),'38':('산업재','생활 서비스'),'91':('기타','외국예탁증서')}

def download(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 MOVENT/1.0'}),timeout=35) as response:return response.read()
        except HTTPError as error:
            if error.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(min(15,max(2,int(error.headers.get('Retry-After','2')))))
def text(value):return html.unescape(re.sub(r'<[^>]+>','',str(value))).strip()
def domain(value):
    value=str(value or '').strip()
    if not value or value.lower()=='nan':return None
    host=urlparse(value if '://' in value else 'https://'+value).hostname
    return host.lower().removeprefix('www.') if host and '.' in host and re.fullmatch(r'[a-zA-Z0-9.-]+',host) else None
def day(value):
    value=re.sub(r'[^0-9]','',str(value))
    if len(value)==7:value=str(int(value[:3])+1911)+value[3:]
    return dt.datetime.strptime(value[:8],'%Y%m%d').date().isoformat()
def source(code,slug,name,url,date,rows,minimum,scope='index-constituents'):
    unique={r[0]:r for r in rows}
    if len(unique)<minimum:raise ValueError(f'{slug}: only {len(unique)} securities')
    if dt.date.fromisoformat(date)>dt.date.today()+dt.timedelta(days=1):raise ValueError('Future source date')
    return {'countryCode':code,'slug':slug,'name':name,'sourceUrl':url,'membershipAsOf':date,'scope':scope,'members':list(unique.values()),'sourceTotal':len(unique)}

def nikkei():
    url='https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225';s=download(url).decode('utf-8')
    updated=re.search(r'Update[^A-Za-z]+([A-Za-z]{3}/\d{1,2}/\d{4})',text(s))
    if not updated:raise ValueError('Nikkei snapshot date unavailable')
    date=dt.datetime.strptime(updated[1],'%b/%d/%Y').date().isoformat();rows=[]
    for part in re.split(r'<h3[^>]*>',s)[1:]:
        industry=text(part.split('</h3>')[0])
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>',part,re.S):
            cells=[text(c) for c in re.findall(r'<td[^>]*>(.*?)</td>',tr,re.S)]
            if len(cells)>=2 and re.fullmatch(r'[0-9A-Z]{4}',cells[0]):rows.append([cells[0]+'.T',cells[1],SECTORS.get(industry,UNKNOWN),industry,None])
    if len(rows)!=225:raise ValueError('Nikkei must have 225 constituents')
    return source('JP','jp-nikkei225','Nikkei 225',url,date,rows,225)
def topix():
    url='https://www.jpx.co.jp/automation/markets/indices/topix/files/topixweight_j.csv'
    frame=pd.read_csv(io.BytesIO(download(url)),encoding='cp932',dtype=str);rows=[];date=None
    for r in frame.to_dict('records'):
        code=r.get('コード','')
        if not isinstance(code,str) or not re.fullmatch(r'[0-9A-Z]{4}',code):continue
        date=day(r['日付']);industry=r['業種'];rows.append([code+'.T',r['銘柄名'],JP_SECTORS.get(industry,UNKNOWN),industry,None])
    return source('JP','jp-topix','TOPIX',url,date,rows,1000)
def csi300():
    url='https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls'
    frame=pd.read_excel(io.BytesIO(download(url)),dtype=str);rows=[];date=None
    for values in frame.itertuples(index=False,name=None):
        date=day(values[0]);code=str(values[4]).zfill(6);exchange=values[7]
        suffix='.SS' if '上海' in exchange else '.SZ' if '深圳' in exchange else None
        if suffix:rows.append([code+suffix,values[5],UNKNOWN,UNKNOWN,None])
    return source('CN','cn-csi300','CSI 300',url,date,rows,290)
def cni(index,slug,name,minimum):
    url='https://www.cnindex.com.cn/sample-detail/download?indexcode='+index
    frame=pd.read_excel(io.BytesIO(download(url)),dtype=str);rows=[];date=None
    for r in frame.to_dict('records'):
        date=day(r['日期']);industry=r['所属行业'];rows.append([str(r['样本代码']).zfill(6)+'.SZ',r['样本简称'],SECTORS.get(industry,UNKNOWN),industry,None])
    return source('CN',slug,name,url,date,rows,minimum)
def hsi(series,slug,name,minimum):
    url='https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/product-data/pub/constituent/metadata/v2?data=rt&seriesCode='+series+'&language=en-hk'
    payload=json.loads(download(url));data=payload.get('data',{});rows=[];date=None
    if payload.get('code')!='0':raise ValueError('HSI source unavailable')
    for index,items in data.get('viewConsDetail',{}).items():
        for r in items:
            raw=str(r.get('stockCode',''))
            if not raw.isdigit() or int(raw)>9999 or r.get('isDummy')=='Y':continue
            sector='금융' if index.endswith('.01') else '유틸리티' if index.endswith('.02') else '부동산' if index.endswith('.03') else UNKNOWN
            date=r['dateTime'][:10];rows.append([str(int(raw)).zfill(4)+'.HK',r['stockName'],sector,sector,None])
    return source('HK',slug,name,url,date,rows,minimum)
def kodex(fund,slug,name,suffix,minimum,now):
    local=now.astimezone(ZoneInfo('Asia/Seoul'));date=local.date()-dt.timedelta(days=int(local.hour<18))
    url='https://www.samsungfund.com/api/v1/kodex/product-pdf/'+fund+'.do?gijunYMD='+date.strftime('%Y.%m.%d')
    data=json.loads(download(url))['pdf'];rows=[]
    for r in data['list']:
        code=r.get('itmNo','');name_=r.get('secNm','')
        if re.fullmatch(r'[0-9A-Z]{6}',code) and not re.search(r'스팩|기업인수목적',name_):rows.append([code+suffix,name_,UNKNOWN,UNKNOWN,None])
    return source('KR',slug,name,url,day(data['gijunYMD']),rows,minimum,'etf-proxy')
def korean_sector(industry):
    for pattern,sector in [('의약|의료','헬스케어'),('반도체|전자|컴퓨터|통신장비|전기장비','정보기술'),('은행|보험|금융|증권','금융'),('소프트웨어|정보서비스|방송|통신업|영화|오디오','통신서비스'),('자동차|소매|숙박|음식점|가구|의복|스포츠','경기소비재'),('식품|식료|음료|담배|농업|어업','필수소비재'),('화학|철강|금속|종이|고무|플라스틱|섬유|목재|비금속','소재'),('석유|석탄|광업','에너지'),('전기, 가스|수도|폐기물','유틸리티'),('부동산','부동산'),('건설|운송|기계|도매|창고|설비|사업지원','산업재')]:
        if re.search(pattern,industry):return sector
    return UNKNOWN
def kind(market,suffix):
    url='https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType='+market
    s=download(url).decode('cp949',errors='replace');rows=[]
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>',s,re.S|re.I):
        c=[text(x) for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>',tr,re.S|re.I)]
        if len(c)>=9 and re.fullmatch(r'[0-9A-Z]{6}',c[2]) and not re.search(r'스팩|기업인수목적',c[0]):rows.append([c[2]+suffix,c[0],korean_sector(c[3]),c[3],domain(c[8])])
    return source('KR','kr-kospi' if suffix=='.KS' else 'kr-kosdaq','코스피' if suffix=='.KS' else '코스닥',url,dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat(),rows,600,'board-sample')
def taiwan():
    url='https://openapi.twse.com.tw/v1/opendata/t187ap03_L';data=json.loads(download(url));rows=[];date=None
    for r in data:
        code=r.get('公司代號','')
        if not re.fullmatch(r'\d{4}',code) or r.get('產業別')=='91':continue
        date=day(r['出表日期']);sector,industry=TW_INDUSTRIES.get(r.get('產業別'),(UNKNOWN,UNKNOWN))
        rows.append([code+'.TW',r.get('公司簡稱') or r['公司名稱'],sector,industry,domain(r.get('網址'))])
    return source('TW','tw-taiex','TAIEX',url,date,rows,800,'board-sample')

def expand(now=None):
    now=now or dt.datetime.now(dt.timezone.utc);path=ROOT/'config.json';config=json.loads(path.read_text());saved=config.setdefault('universeSources',{})
    providers={'nikkei':nikkei,'topix':topix,'csi300':csi300,'shenzhen':lambda:cni('399001','cn-shenzhen','선전성분',490),'chinext':lambda:cni('399006','cn-chinext','차이넥스트',95),'hsi':lambda:hsi('hsi','hk-hangseng','Hang Seng',80),'hstech':lambda:hsi('hstech','hk-hstech','Hang Seng Tech',28),'kospi200':lambda:kodex('2ETF01','kr-kospi200','코스피 200 추종 ETF 보유종목','.KS',180,now),'kosdaq150':lambda:kodex('2ETF54','kr-kosdaq150','코스닥 150 추종 ETF 보유종목','.KQ',130,now),'kospi':lambda:kind('stockMkt','.KS'),'kosdaq':lambda:kind('kosdaqMkt','.KQ'),'taiwan':taiwan}
    def refresh(item):
        key,fn=item
        try:return key,fn(),None
        except Exception as error:return key,None,str(error)[:200]
    errors={}
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for key,result,error in pool.map(refresh,providers.items()):
            if result:
                previous=saved.get(key)
                if previous and (result['membershipAsOf']<previous['membershipAsOf'] or result['sourceTotal']<previous['sourceTotal']*.8):errors[key]='Source date or coverage regressed; retained previous list'
                else:saved[key]=result
            else:errors[key]=error
    for code,cfg in config['markets'].items():
        if code=='US':continue
        cfg.setdefault('baseStocks',cfg['stocks']);members={r[0]:list(r)+[None]*max(0,5-len(r)) for r in cfg['baseStocks']}
        for src in saved.values():
            if src['countryCode']!=code:continue
            for r in src['members']:
                old=members.get(r[0])
                if not old:members[r[0]]=list(r)
                else:
                    for i in [2,3,4]:
                        if (not old[i] or old[i]==UNKNOWN) and r[i] and r[i]!=UNKNOWN:old[i]=r[i]
        cfg['stocks']=list(members.values());cfg['sampleKind']='index-and-listed-universe'
    # Prefer source sector classifications across overlapping constituent lists.
    for src in saved.values():
        code=src['countryCode'];group={k:src[k] for k in ['countryCode','name','sourceUrl','membershipAsOf','scope','sourceTotal']};group['members']=[r[0] for r in src['members']]
        config.setdefault('marketGroups',{})[src['slug']]=group
    for code,definition in [('KR',['kr-kospi200','코스피 200','^KS200']),('KR',['kr-kosdaq150','코스닥 150','^KQ150']),('CN',['cn-csi300','CSI 300','000300.SS'])]:
        if not any(i[0]==definition[0] for i in config['markets'][code]['indices']):config['markets'][code]['indices'].append(definition)
    config['universeRefresh']={'checkedAt':now.isoformat(),'errors':errors,'counts':{c:len(v['stocks']) for c,v in config['markets'].items() if c!='US'}}
    path.write_text(json.dumps(config,ensure_ascii=False,separators=(',',':'))+'\n')
    print('Expanded index/listing universes',json.dumps(config['universeRefresh'],ensure_ascii=False),flush=True)
    return config

if __name__=='__main__':expand()
