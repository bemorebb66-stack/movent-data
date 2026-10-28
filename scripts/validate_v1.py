import math

def validate_v1(data):
    if data.get("version")!="movent-data/1.0" or data.get("methodVersion")!="v1.0":raise ValueError("unknown method")
    if data.get("model",{}).get("window")!=60:raise ValueError("unknown baseline")
    if data.get("releaseStatus")!="final" or data.get("sessionValidation")!="pass":raise ValueError("candidate not final")
    if len(data["sessionCalendar"])<61:raise ValueError("insufficient calendar")
    stocks=data.get("stocks",[])
    if not stocks:raise ValueError("empty stocks")
    by_ticker={s["ticker"]:s for s in stocks}
    if len(by_ticker)!=len(stocks):raise ValueError("duplicate stock")
    for kind in ("stocks","industries","sectors","markets"):
        if not data.get(kind) or data["counts"].get(kind)!=len(data[kind]):raise ValueError("invalid coverage")
        ids=[r["ticker" if kind=="stocks" else "slug"] for r in data[kind]]
        if len(ids)!=len(set(ids)):raise ValueError("duplicate record")
        for row in data[kind]:
            if row["date"]!=data["asOf"]:raise ValueError("mixed dates")
            for key in ("movent","activity","persistence","breadth"):
                v=row.get(key)
                if not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100:raise ValueError("invalid score")
    for s in stocks:
        for kind, key, route in (("sectors", "sectorSlug", "sectorRoute"), ("industries", "industrySlug", "industryRoute")):
            exists=any(g["slug"]==s[key] for g in data[kind])
            if bool(s[route])!=exists:raise ValueError("group route mismatch")
        if s["activity"]!=s["movent"] or abs(s["movent"]-(50+50*math.tanh(s["robustZ"]/3)))>.06:raise ValueError("score formula mismatch")
        if not 0<=s["days5"]<=s["observedDays5"]<=5 or s["streak"]<0:raise ValueError("invalid repeat counts")
        if not 0<=s["baselineEstimated"]<=60 or s["ratio20"]<=0 or s["relativeActivity"]<=0:raise ValueError("invalid baseline")
        history=s["history"];dates=[p["date"] for p in history]
        if dates!=sorted(set(dates)) or dates[-1]!=data["asOf"] or history[-1]["estimated"]:raise ValueError("invalid history")
        if history[-1]["movent"]!=s["movent"]:raise ValueError("history current score mismatch")
        if any(d not in data["sessionCalendar"] for d in dates):raise ValueError("invalid history session")
        for point in history:
            if not math.isfinite(point["movent"]) or not 0<=point["movent"]<=100:raise ValueError("invalid history score")
        key="industrySlug" if s["breadthSource"]=="industry" else "sectorSlug"
        peers=[p for p in stocks if p[key]==s[key] and p["ticker"]!=s["ticker"]]
        count=sum(p["anomalous"] for p in peers)
        if s["breadthTotal"]!=len(peers) or s["breadthCount"]!=count:raise ValueError("peer counts mismatch")
        expected=round(100*count/len(peers),1) if peers else 0
        if s["breadth"]!=expected or s["breadthAvailable"]!=(len(peers)>=5):raise ValueError("peer breadth mismatch")
    for kind,key in (("industries","industrySlug"),("sectors","sectorSlug"),("markets",None)):
        for group in data[kind]:
            members=[by_ticker[t] for t in group["constituents"]] if key is None else [s for s in stocks if s[key]==group["slug"]]
            if group["total"]!=len(members) or group["count"]!=sum(s["anomalous"] for s in members):raise ValueError("group coverage mismatch")
            if abs(group["movent"]-sum(s["movent"] for s in members)/len(members))>.06:raise ValueError("group score mismatch")
            if group["breadth"]!=round(100*group["count"]/group["total"],1):raise ValueError("group breadth mismatch")
    if len(data["markets"])!=4:raise ValueError("market coverage")
