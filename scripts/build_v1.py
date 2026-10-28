"""Generate v1 candidate and time-split comparison from normalized BVT data."""
import argparse,json,math,statistics as st
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from model_v1 import VERSION,WINDOW,score,sequence,context,peer_context
from build_movent_data import slugify,calculate,load_snapshots

def load(root):
    paths=sorted(root.glob("????-??-??.json"));dates=[p.stem for p in paths]
    snapshots=[json.loads(p.read_text(encoding="utf-8"))["tickers"] for p in paths]
    tickers=sorted(set().union(*(s.keys() for s in snapshots)))
    values={};estimated={}
    for t in tickers:
        values[t]=[];estimated[t]=[]
        for day in snapshots:
            r=day.get(t,{});v=r.get("dollar_volume")
            valid=isinstance(v,(int,float)) and math.isfinite(v) and v>0 and r.get("asset_type") in (None,"COMMON_STOCK")
            values[t].append(v if valid else None)
            estimated[t].append(r.get("observation_grade")=="BVT_COMPACT_HISTORY_INTERVAL_MIDPOINT")
    return dates,snapshots,values,estimated

def aggregate(stocks,date,name,slug):
    n=len(stocks);active=sum(s["anomalous"] for s in stocks)
    avg=lambda key:round(st.fmean(s[key] for s in stocks),1)
    delta=lambda h:round(st.fmean(s[f"change{h}d"] for s in stocks),1) if all(s[f"change{h}d"] is not None for s in stocks) else None
    return {"date":date,"name":name,"slug":slug,"movent":avg("movent"),"activity":avg("movent"),
            "persistence":avg("persistence"),"breadth":round(100*active/n,1),"count":active,"total":n,
            "change1d":delta(1),"change5d":delta(5),"change20d":delta(20),"dollarVolume":sum(s["dollarVolume"] for s in stocks)}

def build(root,source):
    dates,snapshots,values,estimated=load(root)
    series={t:sequence(v,estimated[t]) for t,v in values.items()}
    current={t:r[-1] for t,r in series.items() if r[-1] and not r[-1]["estimated"]}
    industry=defaultdict(list);sector=defaultdict(list)
    for t in current:
        meta=snapshots[-1][t];industry[meta.get("industry") or "Unknown"].append(t);sector[meta.get("sector") or "Unknown"].append(t)
    stocks=[]
    for t,r in current.items():
        meta=snapshots[-1][t];ind=meta.get("industry") or "Unknown";sec=meta.get("sector") or "Unknown"
        rows=series[t];n,observed,streak=context(rows,len(dates)-1)
        history=[{"date":dates[i],"movent":round(row["score"],1),"price":snapshots[i].get(t,{}).get("close_price"),"estimated":row["estimated"],**({"anomalous":row["confirmed"]} if not row["estimated"] else {})} for i,row in enumerate(rows) if row][-60:]
        def change(h):
            earlier=rows[-1-h] if len(rows)>h else None
            return round(r["score"]-earlier["score"],1) if earlier and not earlier["estimated"] else None
        peers=industry[ind];breadth_source="industry"
        if len(peers)<6:peers=sector[sec];breadth_source="sector_fallback"
        ctx=peer_context(t,peers,current)
        previous_index=len(dates)-6
        previous={p:series[p][previous_index] for p in peers if series[p][previous_index] and not series[p][previous_index]["estimated"]}
        # Same current peer universe for the comparison; require every peer at t-5.
        pc=peer_context(t,peers,previous)
        breadth_change=round(ctx["breadth"]-pc["breadth"],1) if ctx["breadthAvailable"] and pc["breadthTotal"]==ctx["breadthTotal"] else None
        prior=values[t][-61:-1];flags=estimated[t][-61:-1]
        sensitivities=[]
        for sign in (-1,1):
            candidate=score(values[t][-1],[max(1,v+sign*500000) if flag else v for v,flag in zip(prior,flags)])
            if candidate:sensitivities.append(candidate["score"])
            else:sensitivities.extend([0,100])
        sensitivity=round(max([abs(s-r["score"]) for s in sensitivities],default=0),1)
        quality="sensitive" if sensitivity>5 else "estimated_baseline" if r["baselineEstimated"] else "observed"
        stocks.append({"date":dates[-1],"ticker":t,"company":meta.get("name") or t,"sector":sec,"sectorSlug":slugify(sec),"industry":ind,"industrySlug":slugify(ind),
          "price":meta.get("close_price"),"priceChange":meta.get("daily_return"),"dollarVolume":values[t][-1],"relativeActivity":round(r["ratio"],3),"ratio20":round(values[t][-1]/st.median(values[t][-21:-1]),3),"medianTurnover20":st.median(values[t][-21:-1]),"medianTurnover20Basis":"input-median",
          "movent":round(r["score"],1),"activity":round(r["score"],1),"robustZ":round(r["z"],4),"baseline":round(r["baseline"],2),
          "anomalous":r["anomalous"],"persistence":round(100*n/observed,1) if observed else 0,"days5":n,"observedDays5":observed,"streak":streak,
          "days20":sum(bool(row and row["confirmed"]) for row in rows[-20:]),"history":history,"change1d":change(1),"change5d":change(5),"change20d":change(20),
          **ctx,"breadthSource":breadth_source,"breadthChange5d":breadth_change,"baselineEstimated":r["baselineEstimated"],"quality":quality,"sensitivity":sensitivity,
          "industryAvailable":len(industry[ind])>=5,"industryRoute":f"/industries/{slugify(ind)}" if len(industry[ind])>=5 else None,"sectorRoute":f"/sectors/{slugify(sec)}" if len(sector[sec])>=5 else None})
    groups={}
    for key in ("industry","sector"):
        grouped=defaultdict(list)
        for s in stocks:grouped[s[key]].append(s)
        groups[key]=[{**aggregate(m,dates[-1],name,slugify(name)),**({"sector":m[0]["sector"]} if key=="industry" else {})} for name,m in grouped.items() if len(m)>=5]
    latest=json.loads(source.read_text(encoding="utf-8"));membership={s["t"]:set(s.get("uni",[])) for s in latest["stocks"]}
    # Preserve the existing, explicitly current-membership proxy for Dow.
    from augment_market_data import DOW_30,MARKETS
    for t in DOW_30:membership.setdefault(t,set()).add("Dow Jones")
    markets=[]
    for slug,(name,tag) in MARKETS.items():
        members=[s for s in stocks if tag in membership.get(s["ticker"],set())]
        if not members:continue
        history=[]
        for i in range(max(WINDOW,len(dates)-60),len(dates)):
            rs=[series[s["ticker"]][i] for s in members];valid=[r for r in rs if r and not r["estimated"]]
            if len(valid)>=5:history.append({"date":dates[i],"movent":round(st.fmean(r["score"] for r in valid),1),"count":len(valid)})
        sectors=[aggregate([s for s in members if s["sector"]==sec],dates[-1],sec,slugify(sec)) for sec in sorted({s["sector"] for s in members})]
        market=aggregate(members,dates[-1],name,slug)
        # Point-to-point deltas must correspond to the plotted current-membership series.
        points={p["date"]:p["movent"] for p in history if p["count"]==len(members)}
        for h in (1,5,20):market[f"change{h}d"]=round(market["movent"]-points[dates[-1-h]],1) if dates[-1-h] in points else None
        markets.append({**market,"sourceTotal":sum(tag in x for x in membership.values()),"constituents":[s["ticker"] for s in members],"sectorSlugs":[s["slug"] for s in sectors],"sectors":sectors,"history":history})
    universe=[];included={s["ticker"] for s in stocks}
    for meta in latest["stocks"]:
        t=meta["t"];status="eligible" if t in included else "data_unavailable" if t not in snapshots[-1] or estimated.get(t,[True])[-1] else "insufficient_history"
        if status!="eligible" and values.get(t) and all(v for v in values[t][-61:-1]) and st.median(values[t][-61:-1])<1000000:status="low_liquidity"
        universe.append({"ticker":t,"company":meta.get("n") or t,"sector":meta.get("sec") or "Unknown","industry":meta.get("ind") or "Unknown","status":status,"reason":None if status=="eligible" else "현재 원본 관측과 연속 60거래일 기준 구간 및 유동성 조건이 필요합니다."})
    payload={"version":"movent-data/1.0","methodVersion":VERSION,"asOf":dates[-1],"generatedAt":datetime.now(timezone.utc).isoformat(),"releaseStatus":"final","sessionValidation":"pass",
      "universeSnapshotId":f"us-equities-{dates[-1]}","sessionCalendar":dates,"calendarSource":"https://www.nyse.com/trade/hours-calendars",
      "sourceGrade":latest.get("data_grade","unknown"),"membershipPolicy":"current-membership-proxy","marketMethod":"equal-weight-constituent-anomaly",
      "model":{"window":60,"scaleFloor":.15,"zThreshold":2,"ratioThreshold":1.5,"mapping":"50 + 50*tanh(z/3)","status":"research-preview","historyPolicy":"estimated history labelled; only observed current sessions published"},
      "counts":{"stocks":len(stocks),"industries":len(groups["industry"]),"sectors":len(groups["sector"]),"markets":len(markets)},"stocks":stocks,"industries":groups["industry"],"sectors":groups["sector"],"markets":markets}
    return payload,{"asOf":dates[-1],"tracked":len(universe),"stocks":universe},(dates,values,estimated,series)

def evaluate(root,inputs):
    dates,values,estimated,series=inputs
    short={t:sequence(v,estimated[t],20) for t,v in values.items()}
    legacy=defaultdict(dict)
    for r in calculate(load_snapshots(root)):legacy[r["date"]][r["ticker"]]=r["movent"]
    days=[]
    for i in range(64,len(dates)-3):
        pool=[]
        for t,rows in series.items():
            r=rows[i];s=short[t][i]
            if not r or r["estimated"] or not s or t not in legacy[dates[i]]:continue
            # Evaluation target: exact DV in the NEXT three sessions vs today's fixed 20D median.
            future=values[t][i+1:i+4]
            if any(v is None for v in future) or any(estimated[t][i+1:i+4]):continue
            prior=values[t][i-20:i]
            baseline=st.median(prior)
            target=sum(v/baseline>=1.5 for v in future)>=2
            pool.append((t,{"ratio20":values[t][i]/baseline,"legacy":legacy[dates[i]][t],"robust20":s["score"],"robust60":r["score"]},target))
        if len(pool)<100:continue
        metrics={}
        for model in ("ratio20","legacy","robust20","robust60"):
            top=sorted(pool,key=lambda r:(-r[1][model],r[0]))[:50]
            metrics[model]=sum(r[2] for r in top)/len(top)
        days.append({"date":dates[i],"pool":len(pool),"baseRate":st.fmean(r[2] for r in pool),"precision50":metrics})
    split=max(1,int(len(days)*.6));cutoff=days[split-1]["date"] if days else None
    cutoff_index=dates.index(cutoff) if cutoff else 0
    train=days[:split];holdout=[d for d in days[split:] if dates.index(d["date"])>cutoff_index+3]
    def summarize(rows):return {"days":len(rows),"from":rows[0]["date"] if rows else None,"to":rows[-1]["date"] if rows else None,"baseRate":round(st.fmean(r["baseRate"] for r in rows),4) if rows else None,"precision50":{m:round(st.fmean(r["precision50"][m] for r in rows),4) if rows else None for m in ("ratio20","legacy","robust20","robust60")}}
    return {"methodVersion":VERSION,"target":"Next 3 exact sessions: at least 2 with DV >= 1.5x signal-day prior-20-session median","topK":50,"development":summarize(train),"holdout":summarize(holdout),"embargoSessions":3,"daily":days,
      "limitations":["120-session source window; current membership and retrospectively generated snapshots","Estimated baselines remain; ranking inputs are not fully point-in-time vendor data","Forward windows overlap; daily observations are not independent","This evaluates activity recurrence, not return prediction or profitability","60-day window and all thresholds are design choices, not fitted to this holdout","120-day candidate cannot be evaluated with this source length"]}

def main():
    p=argparse.ArgumentParser();p.add_argument("--snapshots",type=Path,required=True);p.add_argument("--source",type=Path,required=True);p.add_argument("--out",type=Path,required=True);p.add_argument("--research",action="store_true");a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);payload,universe,inputs=build(a.snapshots,a.source)
    for name,data in (("movent.json",payload),("universe.json",universe)):(a.out/name).write_text(json.dumps(data,ensure_ascii=False,separators=(",",":"),allow_nan=False),encoding="utf-8")
    if a.research:
        report=evaluate(a.snapshots,inputs);(a.out/"validation.json").write_text(json.dumps(report,indent=2),encoding="utf-8");print(json.dumps({"development":report["development"],"holdout":report["holdout"]}))
    print(json.dumps({"asOf":payload["asOf"],"counts":payload["counts"],"estimatedBaselines":sum(s["baselineEstimated"]>0 for s in payload["stocks"]),"sensitive":sum(s["quality"]=="sensitive" for s in payload["stocks"])}))

if __name__=="__main__":main()
