import sys,unittest,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from news_research import *
class NewsTests(unittest.TestCase):
 def country(self):return {'asOf':'2026-10-30','schedule':{'timezone':'Asia/Seoul','validFrom':'2026-01-01','validThrough':'2026-12-31','holidays':[]},'stocks':[]}
 def test_close_and_weekend(self):
  e={'country':'KR','publishedAt':'2026-10-30T06:30:00Z'}
  self.assertEqual(reaction_session(e,self.country())[0],'2026-11-02')
 def test_missing_timezone_is_not_inferred(self):self.assertIsNone(timestamp('2026-10-30T15:00:00'))
 def test_official_time_precedes_news(self):
  e={'country':'KR','publishedAt':'2026-10-30T09:00:00Z','officialAt':'2026-10-30T02:00:00Z'}
  self.assertEqual(reaction_session(e,self.country()),('2026-10-30','장중'))
 def test_duplicate_canonical_and_revisions(self):
  a={'id':'one','title':'삼성전자 실적','url':'https://example.com/a','publisher':'test','country':'KR','publishedAt':None,'modifiedAt':None}
  x=ingest([], [a.copy()],{},'2026-10-30T00:00:00Z');b={**a,'title':'수정된 제목'}
  y=ingest(x,[b],{},'2026-10-31T00:00:00Z')
  self.assertEqual(y[0]['firstSeenAt'],x[0]['firstSeenAt']);self.assertEqual(len(y[0]['revisions']),1)
 def test_ambiguous_company(self):
  stocks=[{'ticker':'A','company':'동일기업'},{'ticker':'B','company':'동일기업'}]
  self.assertEqual(entities('동일기업 실적 발표','KR',stocks),['A','B'])
  self.assertEqual(entities('초동일기업 실적 발표','KR',stocks),[])
 def test_no_reaction_not_dropped(self):
  c=self.country();e={'country':'KR','publishedAt':'2026-10-30T00:00:00Z'}
  s={'ticker':'A','industry':'one','history':[{'date':'2026-10-29','price':100,'movent':50,'anomalous':False},{'date':'2026-10-30','price':100,'movent':50,'anomalous':False}]};c['stocks']=[s]
  a=event_analysis(e,c,s);self.assertEqual(a['followUp'],'거래 활동 발생 없음');self.assertIsNone(a['windows'][-1].get('returnPct'))
 def test_future_event(self):
  c=self.country();s={'ticker':'A','history':[]};c['stocks']=[s]
  self.assertEqual(event_analysis({'country':'KR','publishedAt':'2026-10-30T08:00:00Z'},c,s)['status'],'반응 확인 전')
 def test_unsafe_url(self):self.assertIsNone(canonical('javascript:alert(1)'))
 def test_feed_times(self):
  a=parse_feed('<rss><channel><item><title>실적</title><link>https://example.com/?utm_source=x</link><pubDate>Fri, 30 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>',{'publisher':'test','country':'KR','id':'t'})[0]
  self.assertEqual(a['url'],'https://example.com/');self.assertIsNone(a['modifiedAt'])
if __name__=='__main__':unittest.main()
