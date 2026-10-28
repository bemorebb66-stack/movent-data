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

class ResearchMetadataTests(unittest.TestCase):
    def test_exact_median_and_historical_flags(self):
        import statistics
        dates=[(dt.date(2026,1,1)+dt.timedelta(days=i)).isoformat() for i in range(100)]
        bars={d:{'close':100,'volume':1000000+i*10000} for i,d in enumerate(dates)}
        row=update.stock_row(['7203.T','Toyota','Auto','Auto'],bars,dates,'JP',update.CONFIG['JP'])
        self.assertEqual(row['medianTurnover20'],statistics.median([bars[d]['close']*bars[d]['volume'] for d in dates[-21:-1]]))
        self.assertEqual(row['medianTurnover20Basis'],'observed-median')
        self.assertTrue(all(isinstance(p['anomalous'],bool) for p in row['history']))
        self.assertEqual(row['history'][-1]['anomalous'],row['anomalous'])
    def test_cached_taiwan_list_can_survive_provider_failure(self):
        import json
        from expand_universe import recover_verified_sources
        members=[[str(1000+i)+'.TW','Test','Test','Test',None] for i in range(800)]
        baseline={'universeSources':{'taiwan':{'countryCode':'TW','slug':'tw-taiex','name':'TAIEX','sourceUrl':'https://openapi.twse.com.tw/v1/opendata/t187ap03_L','membershipAsOf':'2026-10-27','scope':'board-sample','sourceTotal':800,'members':members}}}
        config={}
        self.assertEqual(recover_verified_sources(config,lambda url:json.dumps(baseline).encode()),{})
        self.assertEqual(len(config['universeSources']['taiwan']['members']),800)
        self.assertEqual(recover_verified_sources(config,lambda url: (_ for _ in ()).throw(RuntimeError('offline'))),{})
    def test_taiwan_coverage_floor_fails_before_network(self):
        with self.assertRaises(ValueError):update.foreign_country('TW',{'stocks':[],'minimumTracked':800},{},dt.datetime.now(dt.timezone.utc),None)
    def test_new_occurrence_needs_exact_previous_session(self):
        from publish_views import occurrence_changes
        country={'asOf':'2026-10-28','schedule':{'validFrom':'2026-01-01','validThrough':'2026-12-31','holidays':[]},'stocks':[{'anomalous':True,'quality':'observed','history':[{'date':'2026-10-27','anomalous':False}]},{'anomalous':True,'quality':'observed','history':[{'date':'2026-10-26','anomalous':False}]}]}
        result=occurrence_changes(country)
        self.assertEqual(result['new'],1)
        self.assertEqual(result['insufficient'],1)
        country['stocks'][0]['history'][0]['estimated']=True
        self.assertEqual(occurrence_changes(country)['new'],0)
    def test_preserved_country_revision_is_not_new_collection(self):
        from publish_views import publish
        country={'asOf':'2026-10-27','releaseStatus':'final','schedule':{},'currency':'TWD','sampleKind':'test','coverageNote':'test','indices':[],'stocks':[],'tracked':[],'sourceRevision':'2026-10-27T10:00:00Z','lastSuccessAt':'2026-10-27T10:00:00Z'}
        saved={}
        manifest=publish({'generatedAt':'2026-10-28T10:00:00Z','countries':{'TW':country}},Path('/test'),lambda path,data:saved.update({str(path):data}))
        self.assertEqual(manifest['countries']['TW']['revision'],country['sourceRevision'])
        self.assertEqual(manifest['countries']['TW']['lastSuccessAt'],country['lastSuccessAt'])
        self.assertEqual(saved['/test/data/countries/TW.json']['revision'],country['sourceRevision'])

if __name__=='__main__':unittest.main()
