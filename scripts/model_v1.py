"""MOVENT v1: causal robust activity score; persistence and peers are context.

No fitted weights. All baselines exclude the observation being scored. Compact
history is explicitly labelled; it is never treated as confirmed daily activity.
"""
import math
import statistics as st

VERSION = "v1.0"
WINDOW = 60
SCALE_FLOOR = .15
Z_THRESHOLD = 2.0
RATIO_THRESHOLD = 1.5
MIN_LIQUIDITY = 1_000_000

def score(current, prior):
    if not prior or not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in [current,*prior]):
        return None
    baseline=st.median(prior)
    if baseline<MIN_LIQUIDITY:return None
    logs=[math.log(v) for v in prior];center=st.median(logs)
    scale=max(SCALE_FLOOR,1.4826*st.median(abs(v-center) for v in logs))
    z=(math.log(current)-center)/scale
    return {"score":50+50*math.tanh(z/3),"z":z,"ratio":current/baseline,"baseline":baseline,
            "anomalous":z>=Z_THRESHOLD and current/baseline>=RATIO_THRESHOLD}

def sequence(values, estimated, window=WINDOW):
    result=[]
    for i,current in enumerate(values):
        row=None
        if i>=window and current is not None and all(v is not None for v in values[i-window:i]):
            row=score(current,values[i-window:i])
            if row:
                row["estimated"]=estimated[i]
                row["baselineEstimated"]=sum(estimated[i-window:i])
                row["confirmed"]=row["anomalous"] and not estimated[i]
        result.append(row)
    return result

def context(rows,index):
    recent=rows[max(0,index-4):index+1]
    observed=sum(r is not None and not r["estimated"] for r in recent)
    count=sum(bool(r and r["confirmed"]) for r in recent)
    streak=0
    for row in reversed(rows[:index+1]):
        if not row or not row["confirmed"]:break
        streak+=1
    return count,observed,streak

def peer_context(ticker,members,rows):
    peers=[t for t in members if t!=ticker and t in rows]
    n=len(peers);active=sum(rows[t]["confirmed"] for t in peers)
    return {"breadthCount":active,"breadthTotal":n,"breadth":round(100*active/n,1) if n else 0,
            "breadthAvailable":n>=5}
