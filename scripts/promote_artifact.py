"""Validate a candidate MOVENT artifact, then atomically promote it.

The candidate must carry the authoritative exchange-session list produced by
the upstream NYSE/Nasdaq calendar step. Failure leaves the accepted artifact untouched.
"""
import argparse,json,os,tempfile,math
from datetime import date,timedelta
from pathlib import Path
from build_movent_data import REGRESSIONS

REQUIRED_REGRESSIONS={("2026-07-22","SMCI"),("2026-08-04","PLTR"),("2026-06-26","AUPH")}
def validate(data):
    sessions=data.get("sessionCalendar")
    if not isinstance(sessions,list) or len(sessions)<21: raise ValueError("authoritative sessionCalendar is missing")
    if sessions!=sorted(set(sessions)) or sessions[-1]!=data.get("asOf"): raise ValueError("invalid session ordering or asOf")
    calendar=json.loads((Path(__file__).resolve().parents[1]/"data/exchange-calendar.json").read_text())
    if sessions[0]<calendar["validFrom"] or sessions[-1]>calendar["validThrough"]: raise ValueError("calendar range expired")
    cursor=date.fromisoformat(sessions[0]); end=date.fromisoformat(sessions[-1]); expected=[]
    while cursor<=end:
        if cursor.weekday()<5 and cursor.isoformat() not in calendar["holidays"]: expected.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    if sessions!=expected: raise ValueError("exchange session gap")
    if data.get("methodVersion")=="v1.0":
        from validate_v1 import validate_v1
        validate_v1(data)
        return
    required=sessions[-21:]
    for stock in data.get("stocks",[]):
        observed=[p["date"] for p in stock.get("history",[]) if p["date"] in required]
        if observed!=required: raise ValueError(f"session gap: {stock['ticker']}")
    regressions={(x["date"],x["ticker"]) for x in data.get("regressions",[])}
    if regressions!=REQUIRED_REGRESSIONS: raise ValueError("regression set mismatch")
    if len(data["regressions"])!=len(REGRESSIONS): raise ValueError("duplicate regressions")
    for row in data["regressions"]:
        for field,value in zip(("activity","persistence","breadth","movent"),REGRESSIONS[(row["date"],row["ticker"])]):
            actual=row.get(field)
            if not isinstance(actual,(int,float)) or not math.isfinite(actual) or abs(actual-value)>1e-9: raise ValueError("regression value mismatch")
    if not data.get("stocks") or not data.get("industries") or not data.get("sectors"): raise ValueError("coverage is empty")
    for kind in ("stocks","industries","sectors"):
        if data["counts"].get(kind)!=len(data[kind]): raise ValueError("coverage count mismatch")
        ids=[row["ticker" if kind=="stocks" else "slug"] for row in data[kind]]
        if len(ids)!=len(set(ids)): raise ValueError("duplicate records")
        for row in data[kind]:
            if row["date"]!=data["asOf"]: raise ValueError("mixed dates")
            for field in ("movent","activity","persistence","breadth"):
                value=row.get(field)
                if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100: raise ValueError("invalid score")
    if len(data.get("markets",[]))!=4 or any(not m.get("total") for m in data["markets"]): raise ValueError("market coverage is empty")
def main():
    p=argparse.ArgumentParser();p.add_argument("candidate",type=Path);p.add_argument("production",type=Path);a=p.parse_args()
    data=json.loads(a.candidate.read_text(encoding="utf-8"));validate(data)
    a.production.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=a.production.parent,prefix=".movent-",suffix=".json")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,separators=(",",":"));f.flush();os.fsync(f.fileno())
        os.replace(tmp,a.production)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print(json.dumps({"promoted":str(a.production),"asOf":data["asOf"],"stocks":len(data["stocks"])}))
if __name__=="__main__":main()
