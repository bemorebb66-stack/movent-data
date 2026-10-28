"""Publish compact metadata and independent country files for bounded Worker memory."""
import math

def number(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
def rounded(v):return math.floor(v*10+.5)/10
def sequential_sum(values):
    total=0.0
    for value in values:total+=value
    return total
def aggregate(rows,total):
    eligible=[s for s in rows if number(s.get('movent'))]
    def mean(values):return rounded(sequential_sum(values)/len(values)) if values else None
    def comparison(key):
        paired=[s for s in eligible if s.get('quality')!='sensitive' and number(s.get(key))]
        complete=bool(paired) and len(paired)==len(eligible)
        usable=complete or len(paired)>=5 and len(paired)>=.8*len(eligible)
        return {'value':mean([s[key] for s in paired]) if usable else None,'count':len(paired),'total':len(eligible),'coverage':rounded(100*len(paired)/len(eligible)) if eligible else None,'status':'complete' if complete else 'partial' if usable else 'insufficient','excludedForQuality':sum(s.get('quality')=='sensitive' for s in eligible)}
    return {'movent':mean([s['movent'] for s in eligible]),'change1d':mean([s['change1d'] for s in eligible]) if eligible and all(number(s.get('change1d')) for s in eligible) else None,'change5d':mean([s['change5d'] for s in eligible]) if eligible and all(number(s.get('change5d')) for s in eligible) else None,'comparisons':{key:comparison(key) for key in ['change1d','change5d']},'breadth':rounded(100*sum(bool(s['anomalous']) for s in eligible)/len(eligible)) if eligible else None,'eligibleCount':len(eligible),'sourceCount':total,'coverage':rounded(100*len(eligible)/total) if total else None}
def phase(s):
    if s['anomalous']:return '반복 발생' if s['days5']>=3 and s['observedDays5']==5 else '이례적 활동'
    return '활동 진정' if s['days5']>=2 else '일반 활동'
def occurrence_changes(country):
    import datetime as dt
    schedule=country.get('schedule',{});previous=None
    day=dt.date.fromisoformat(country['asOf'])
    for _ in range(14):
        day-=dt.timedelta(days=1);key=day.isoformat()
        if not schedule.get('validFrom','9999')<=key<=schedule.get('validThrough','0000'):break
        if day.weekday()<5 and key not in schedule.get('holidays',[]):previous=key;break
    counts={k:0 for k in ['new','continuing','ended','none','insufficient']}
    for stock in country['stocks']:
        prior=next((p for p in stock['history'] if p['date']==previous),None)
        if not prior or type(prior.get('anomalous')) is not bool or prior.get('estimated') or stock.get('quality')=='sensitive':counts['insufficient']+=1;continue
        key=('continuing' if prior['anomalous'] else 'new') if stock['anomalous'] else ('ended' if prior['anomalous'] else 'none')
        counts[key]+=1
    return {**counts,'asOf':country['asOf'],'previousDate':previous,'total':len(country['stocks'])}

def publish(feed,root,write):
    manifest={'version':'movent-manifest/1','generatedAt':feed['generatedAt'],'countries':{},'search':[]}
    for code,country in feed['countries'].items():
        rows=country['stocks'];by_ticker={s['ticker']:s for s in rows}
        markets=[]
        for group in country.get('markets',[]):
            members=set(group['constituents']);markets.append({**aggregate([s for s in rows if s['ticker'] in members],group['sourceTotal']),'name':group['name'],'slug':group['slug'],'href':'/markets/'+group['slug']})
        sectors=[]
        for group in country.get('sectors',[]):
            members=[s for s in rows if s['sectorSlug']==group['slug']];total=sum(t['sector']==group['name'] for t in country['tracked'])
            if members:sectors.append({**aggregate(members,total),'name':group['name'],'slug':group['slug'],'href':'/sectors/'+group['slug'] if code=='US' else '#country-stocks'})
        def leading(groups):return max((g for g in groups if g['movent'] is not None),key=lambda g:g['movent'],default=None)
        path='data/countries/'+code+'.json'
        manifest['countries'][code]={k:country[k] for k in ['asOf','releaseStatus','schedule','currency','sampleKind','coverageNote','indices']}
        if country.get('lastSuccessAt'):manifest['countries'][code]['lastSuccessAt']=country['lastSuccessAt']
        manifest['countries'][code].update(path=path,revision=country.get('sourceRevision',feed['generatedAt']),stocks=len(rows),tracked=len(country['tracked']),changes=occurrence_changes(country),overview={**aggregate(rows,len(country['tracked'])),'leadingMarket':leading(markets),'leadingSector':leading([g for g in sectors if g['name']!='업종 미분류'])})
        write(root/path,{'version':'movent-country/1','countryCode':code,'revision':country.get('sourceRevision',feed['generatedAt']),'data':country})
        for tracked in country['tracked']:
            s=by_ticker.get(tracked['ticker'])
            manifest['search'].append({'ticker':tracked['ticker'],'company':s['company'] if s else tracked['company'],'companyEn':s.get('companyEn',tracked['company']) if s else tracked.get('companyEn',tracked['company']),'countryCode':code,'movent':s['movent'] if s else None,'change5d':s.get('change5d') if s else None,'percentile':None,'phase':phase(s) if s else None,'available':bool(s),'status':'eligible' if s else tracked['status'],'reason':None if s else tracked.get('reason')})
    write(root/'data/manifest.json',manifest)
    return manifest
