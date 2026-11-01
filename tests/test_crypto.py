import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from crypto import bars8, scored, UTC

def candles(n=124):
    start = dt.datetime(2026, 9, 1, tzinfo=UTC)
    return [{'candle_date_time_utc':(start+dt.timedelta(hours=4*i)).isoformat(), 'opening_price':100, 'high_price':112, 'low_price':90, 'trade_price':110, 'candle_acc_trade_price':1000, 'candle_acc_trade_volume':10} for i in range(n)]
class CryptoTests(unittest.TestCase):
    def test_incomplete_bars_excluded_and_true_turnover_summed(self):
        rows = candles(5)
        bars = bars8(rows, dt.datetime(2026,9,1,18,tzinfo=UTC))
        self.assertEqual(len(bars),2)
        self.assertEqual(bars[-1]['endAt'],'2026-09-01T16:00:00Z')
        self.assertEqual(bars[0]['turnover'],2000)
    def test_missing_half_is_not_filled(self):
        rows=candles(4); del rows[1]
        self.assertEqual(len(bars8(rows,dt.datetime(2026,9,2,tzinfo=UTC))),1)
    def test_current_bar_cannot_enter_its_baseline(self):
        bars=bars8(candles(),dt.datetime(2026,11,1,tzinfo=UTC))
        self.assertEqual(scored(bars)[-1]['movent'],50)
        bars[-1]['turnover']*=10
        result=scored(bars)[-1]
        self.assertGreater(result['movent'],99)
        self.assertEqual(result['ratio20'],10)
        self.assertTrue(result['anomaly'])
    def test_gap_suppresses_score_until_60_contiguous_baselines(self):
        bars=bars8(candles(),dt.datetime(2026,11,1,tzinfo=UTC))
        del bars[10]
        self.assertEqual(scored(bars),[])
    def test_bad_and_duplicate_source_rejected(self):
        rows=candles(2);rows[0]['trade_price']=float('nan')
        with self.assertRaises(ValueError):bars8(rows,dt.datetime(2026,11,1,tzinfo=UTC))
        rows=candles(2)
        with self.assertRaises(ValueError):bars8(rows+[rows[0]],dt.datetime(2026,11,1,tzinfo=UTC))
if __name__=='__main__': unittest.main()
