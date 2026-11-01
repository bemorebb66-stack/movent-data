# BTC and ETH observation feed

Source: Upbit KRW-BTC and KRW-ETH public quotation API. No credentials.
Primary collection: 01:00, 09:00, 17:00 Asia/Seoul, following the commonly used UTC 00/08/16 eight-hour funding settlement grid. This is a spot activity feed; funding rates and futures positions are not collected. Exchange/pair-specific funding intervals can differ.

Completed four-hour candles are paired into UTC 00/08/16 eight-hour bars. MOVENT uses the previous 60 contiguous eight-hour bars, never the current bar in its baseline. 20-bar turnover ratio uses the previous 20-bar median. No missing bars are imputed. Store true quote-currency turnover, not close times volume. Keep observed closed OHLCV and timestamped scored snapshots under data/crypto. Initial backfill is first observed at collection time, not point-in-time historical evidence.

Latest feed publication is atomic across BTC and ETH. If either source fails, preserve latest.json and write status.json with errors; the workflow fails and still commits error status. Retry source collection twice with a 30-second delay. GitHub cron is a requested start, not an exact completion guarantee. Website checks for missing completed bars and scheduled collections overdue by 30 minutes, and retains verified data on transport/schema/date-regression errors.

Stock collection: Asia 17:00 KST, US 07:00 KST. Retry only missing/failed markets at +30/+60 minutes. Country cutoffs respect local completed sessions; Hong Kong is not considered complete before 16:10 local because of the closing auction. US time is fixed in KST regardless of DST. Scheduled Asia primary refresh also preserves the existing universe refresh. News schedules remain separate.
