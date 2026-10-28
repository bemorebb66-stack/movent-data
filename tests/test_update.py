import datetime as dt
import importlib.util
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import update

class UpdateTests(unittest.TestCase):
    def test_asian_session_uses_local_date(self):
        cfg={'currency':'JPY','timezone':'Asia/Tokyo','finalHour':18}
        result={'meta':{'currency':'JPY'},'timestamp':[int(dt.datetime(2026,10,27,0,tzinfo=dt.timezone.utc).timestamp())], 'indicators':{'quote':[{'close':[100],'volume':[10]}]}}
        self.assertEqual(list(update.parse_bars(result,cfg,dt.datetime(2026,10,28,tzinfo=dt.timezone.utc))),['2026-10-27'])
    def test_intraday_and_future_bars_excluded(self):
        cfg={'currency':'KRW','timezone':'Asia/Seoul','finalHour':18}
        result={'meta':{'currency':'KRW'},'timestamp':[int(dt.datetime(2026,10,28,0,tzinfo=dt.timezone.utc).timestamp())], 'indicators':{'quote':[{'close':[100],'volume':[10]}]}}
        self.assertEqual(update.parse_bars(result,cfg,dt.datetime(2026,10,28,1,tzinfo=dt.timezone.utc)),{})
    def test_currency_mismatch_rejected(self):
        with self.assertRaises(ValueError):update.parse_bars({'meta':{'currency':'USD'}},{'currency':'JPY'},dt.datetime.now(dt.timezone.utc))
    def test_missing_current_volume_never_becomes_zero_score(self):
        dates=[(dt.date(2026,1,1)+dt.timedelta(days=i)).isoformat() for i in range(100)]
        bars={d:{'close':100,'volume':1000000} for d in dates};bars[dates[-1]]['volume']=None
        self.assertIsNone(update.stock_row(['7203.T','Toyota','Auto','Auto'],bars,dates,'JP',update.CONFIG['JP']))
    def test_failed_atomic_serialization_preserves_previous_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.json';path.write_text('{"accepted":true}')
            with self.assertRaises(ValueError):update.atomic_write(path,{'score':float('nan')})
            self.assertEqual(path.read_text(),'{"accepted":true}')
    def test_no_date_regression(self):
        with self.assertRaises(ValueError):update.validate_country({'asOf':'2026-10-26','stocks':[{}]*5},{'asOf':'2026-10-27','stocks':[{}]*5})

if __name__=='__main__':unittest.main()
