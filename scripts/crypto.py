"""Upbit KRW BTC/ETH: completed UTC 8-hour bars, no partial-bar scoring."""
import datetime as dt
import json
import math
import statistics
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
ASSETS = {'BTC': '비트코인', 'ETH': '이더리움'}

def stamp(t):
    return t.astimezone(UTC).isoformat().replace('+00:00', 'Z')

def parse(t):
    return dt.datetime.fromisoformat(t.replace('Z', '+00:00')).replace(tzinfo=UTC)

def request(path):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request('https://api.upbit.com/v1/' + path, headers={'User-Agent': 'MOVENT/1.0'}), timeout=25) as r:
                return json.load(r)
        except Exception:
            if attempt == 2: raise
            time.sleep(2 ** attempt)

def bars8(candles, now):
    parts = {}
    for c in candles:
        t = parse(c['candle_date_time_utc'])
        if t.minute or t.second or t.hour % 4: raise ValueError('Unaligned source candle')
        if t + dt.timedelta(hours=4) > now: continue
        values = [c[k] for k in ('opening_price', 'high_price', 'low_price', 'trade_price', 'candle_acc_trade_price', 'candle_acc_trade_volume')]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values): raise ValueError('Invalid candle')
        if c['low_price'] > min(c['opening_price'], c['trade_price']) or c['high_price'] < max(c['opening_price'], c['trade_price']): raise ValueError('Invalid OHLC')
        if t in parts: raise ValueError('Duplicate candle')
        parts[t] = c
    result = []
    for t, a in sorted(parts.items()):
        if t.hour % 8 or t + dt.timedelta(hours=8) > now: continue
        b = parts.get(t + dt.timedelta(hours=4))
        if b is None: continue
        result.append({'startAt': stamp(t), 'endAt': stamp(t + dt.timedelta(hours=8)),
                       'open': a['opening_price'], 'high': max(a['high_price'], b['high_price']),
                       'low': min(a['low_price'], b['low_price']), 'close': b['trade_price'],
                       'turnover': a['candle_acc_trade_price'] + b['candle_acc_trade_price'],
                       'volume': a['candle_acc_trade_volume'] + b['candle_acc_trade_volume']})
    return result

def scored(bars):
    result = []
    for i in range(60, len(bars)):
        window = bars[i-60:i+1]
        if any(parse(b['startAt']) != parse(a['endAt']) for a, b in zip(window, window[1:])): continue
        baseline = [math.log(x['turnover']) for x in window[:-1]]
        median = statistics.median(baseline)
        scale = max(.15, 1.4826 * statistics.median(abs(x-median) for x in baseline))
        z = (math.log(window[-1]['turnover'])-median)/scale
        ratio60 = window[-1]['turnover']/statistics.median(x['turnover'] for x in window[:-1])
        result.append({**window[-1], 'movent': round(50+50*math.tanh(z/3), 1),
                       'ratio20': round(window[-1]['turnover']/statistics.median(x['turnover'] for x in window[-21:-1]), 3),
                       'changePct': round((window[-1]['close']/window[-1]['open']-1)*100, 3),
                       'anomaly': z >= 2 and ratio60 >= 1.5})
    return result

def collect_asset(symbol, name, now):
    market = 'KRW-'+symbol
    candles = request('candles/minutes/240?market='+market+'&count=200')
    if not isinstance(candles, list) or any(c.get('market') != market for c in candles): raise ValueError('Wrong market')
    bars = bars8(candles, now)
    history = scored(bars)
    expected = now.replace(hour=(now.hour//8)*8, minute=0, second=0, microsecond=0)
    if not history or parse(history[-1]['endAt']) != expected: raise ValueError('Latest completed 8-hour bar unavailable')
    ticker = request('ticker?markets='+market)[0]
    if ticker['market'] != market or not math.isfinite(ticker['trade_price']) or ticker['trade_price'] <= 0: raise ValueError('Invalid quote')
    quote_at = dt.datetime.fromtimestamp(ticker['timestamp']/1000, UTC)
    if not -60 <= (now-quote_at).total_seconds() <= 900: raise ValueError('Stale quote')
    return {'symbol': symbol, 'name': name, 'market': market, 'currency': 'KRW', 'collectedAt': stamp(now),
            'price': ticker['trade_price'], 'priceAt': stamp(quote_at), 'history': history}, bars

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')
    tmp.replace(path)

def main():
    now = dt.datetime.now(UTC)
    directory = ROOT/'data/crypto'
    errors, assets, allbars = {}, [], {}
    for symbol, name in ASSETS.items():
        try:
            for attempt in range(3):
                try:
                    asset, bars = collect_asset(symbol, name, now)
                    break
                except Exception:
                    if attempt == 2:raise
                    time.sleep(30)
            assets.append(asset)
            allbars[symbol] = bars
        except Exception as e:
            errors[symbol] = str(e)[:240]
    if not errors:
        feed = {'version': 'crypto/1', 'generatedAt': stamp(now), 'source': 'Upbit KRW', 'intervalHours': 8, 'assets': assets}
        previous_path = directory/'latest.json'
        previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
        if previous and previous['generatedAt'] > feed['generatedAt']: raise ValueError('Clock regression')
        # Keep all observed closed OHLCV and first-collected event snapshots.
        for symbol, bars in allbars.items():
            path = directory/'bars'/f'{symbol}.json'
            old = json.loads(path.read_text()) if path.exists() else []
            merged = {b['startAt']: b for b in old}
            merged.update({b['startAt']: b for b in bars})
            write(path, sorted(merged.values(), key=lambda b: b['startAt']))
        write(directory/'observations'/now.strftime('%Y-%m-%dT%H%M%SZ.json'), feed)
        write(previous_path, feed)
    write(directory/'status.json', {'checkedAt': stamp(now), 'ok': not errors, 'errors': errors})
    print(json.dumps({'checkedAt': stamp(now), 'assets': [a['symbol'] for a in assets], 'errors': errors}, ensure_ascii=False))
    if errors: raise SystemExit(1)

if __name__ == '__main__': main()
