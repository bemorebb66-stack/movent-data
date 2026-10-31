import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import update
CAL={'validFrom':'2026-01-01','validThrough':'2026-12-31','holidays':[],'timezone':'Asia/Seoul','finalHour':18}
class ReferenceDateTests(unittest.TestCase):
 def test_closed_stock_day_survives_missing_index_close(self):
  dates=[]
  day=dt.date(2026,5,1)
  while day<=dt.date(2026,10,30):
   if day.weekday()<5:dates.append(day.isoformat())
   day+=dt.timedelta(days=1)
  cfg={**update.CONFIG['KR'],'stocks':[[f'TEST{i}.KS','Test','산업재','제조'] for i in range(6)],'minimumTracked':0}
  def chart(missing=False):return {'meta':{'currency':'KRW'},'timestamp':[int(dt.datetime.fromisoformat(d+'T00:00:00+00:00').timestamp()) for d in dates],'indicators':{'quote':[{'close':[100.]*(len(dates)-1)+[None if missing else 110.],'volume':[1000000]*len(dates)}]}}
  fetched={cfg['indices'][0][2]:chart(True),**{s[0]:chart() for s in cfg['stocks']}}
  with patch.object(update,'schedule',return_value=CAL):
   data=update.foreign_country('KR',cfg,fetched,dt.datetime(2026,10,31,tzinfo=dt.timezone.utc),None)
  self.assertEqual(data['asOf'],'2026-10-30')
  self.assertTrue(all(s['date']=='2026-10-30' for s in data['stocks']))
 def test_incomplete_day_weekend_and_holiday_excluded(self):
  with patch.object(update,'schedule',return_value={**CAL,'holidays':['2026-10-29']}):
   dates=update.completed_session_dates(update.CONFIG['KR'],dt.datetime(2026,10,30,1,tzinfo=dt.timezone.utc),'2026-10-28')
  self.assertEqual(dates,['2026-10-28'])
