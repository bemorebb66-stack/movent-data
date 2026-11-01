import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import collection_schedule as c
import update
class CollectionTests(unittest.TestCase):
    def test_primary_splits_asia_and_us(self):
        self.assertEqual(c.selected_countries('schedule','0 22 * * *'),['US'])
        self.assertEqual(set(c.selected_countries('schedule','0 8 * * *')),{'KR','JP','CN','HK','TW'})
    def test_cutoffs_allow_kst_schedule_without_accepting_intraday_bars(self):
        self.assertEqual(update.CONFIG['KR']['finalHour'],17)
        self.assertEqual(update.CONFIG['HK']['finalHour'],16)
        self.assertEqual(update.CONFIG['US']['finalHour'],17)
    def test_retry_only_selects_missing_country(self):
        feed={'countries':{code:{'asOf':'2026-10-30'} for code in c.CONFIG}}
        feed['countries']['KR']['asOf']='2026-10-29'
        def read(path,*args,**kwargs):return __import__('json').dumps(feed if path.name=='global.json' else {'errors':{}})
        with patch.object(Path,'read_text',read),patch.object(c,'completed_session_dates',return_value=['2026-10-30']):
            self.assertEqual(c.selected_countries('schedule','30 8 * * *',now=dt.datetime(2026,10,30,8,30,tzinfo=dt.timezone.utc)),['KR'])
