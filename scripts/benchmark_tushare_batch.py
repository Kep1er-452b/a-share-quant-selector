"""Offline old/new Tushare core request and qfq equivalence benchmark.

Run: .venv/bin/python scripts/benchmark_tushare_batch.py --stocks 300
No token, network, provider activation or production data writes.
"""
import argparse
from collections import Counter
from datetime import date
from pathlib import Path
import json
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import tushare as ts
from utils.data_provider import BaseDataProvider
from utils.tushare_batch import BatchSliceStore
from utils.tushare_fetcher import TushareFetcher


def run(count):
    symbols=[f'{value:06d}.SZ' for value in range(1,count+1)]
    dates=[date(2026,9,28),date(2026,9,29),date(2026,9,30)]
    rows={day.strftime('%Y%m%d'):pd.DataFrame([{'ts_code':symbol,'trade_date':day.strftime('%Y%m%d'),
          'open':10.135,'high':11.,'low':9.,'close':10.125,'pre_close':10.,'vol':100.,'amount':20.,
          'turnover_rate':1.,'total_mv':100000.,'adj_factor':2.} for symbol in symbols]) for day in dates}
    combined=pd.concat([rows[day.strftime('%Y%m%d')] for day in reversed(dates)],ignore_index=True)
    by_stock={symbol:frame.reset_index(drop=True) for symbol,frame in combined.groupby('ts_code')}
    old_calls=Counter()
    class Api:
        def daily(self,ts_code,**kw):
            old_calls['daily']+=1
            return by_stock[ts_code][['ts_code','trade_date','open','high','low','close','pre_close','vol','amount']].copy()
        def adj_factor(self,ts_code,**kw):
            old_calls['adj_factor']+=1
            return by_stock[ts_code][['ts_code','trade_date','adj_factor']].copy()
    with tempfile.TemporaryDirectory(prefix='aqs-batch-benchmark-') as directory:
        fetcher=TushareFetcher.__new__(TushareFetcher)
        BaseDataProvider.__init__(fetcher,directory)
        fetcher.stock_meta_file=Path(directory)/'stock_map.json'
        fetcher.batch_store=BatchSliceStore(Path(directory)/'batch')
        fetcher.batch_daily_enabled=True
        fetcher.get_trade_dates_between=lambda start,end:[day for day in dates if pd.Timestamp(start).date()<=day<=pd.Timestamp(end).date()]
        new_calls=Counter()
        from utils.tushare_batch import FIELDS
        def call(dataset,**kw):
            new_calls[dataset]+=1
            return rows[kw['trade_date']][FIELDS[dataset].split(',')].copy()
        fetcher._call_batch_endpoint=call
        started=time.perf_counter()
        old={}
        old_calls['daily_basic']=len(dates)
        for symbol in symbols:
            frame=ts.pro_bar(ts_code=symbol,api=Api(),start_date='20260928',end_date='20260930',adj='qfq',adjfactor=True)
            old[symbol.split('.')[0]]=fetcher._normalize_history_dataframe(frame,by_stock[symbol][['trade_date','turnover_rate','total_mv']])
        old_seconds=time.perf_counter()-started
        started=time.perf_counter()
        items=[{'code':symbol.split('.')[0]} for symbol in symbols]
        fetcher.prepare_incremental_updates(items,{item['code']:{'latest_date':'2026-09-28'} for item in items},dates[-1],dates[1:])
        new_seconds=time.perf_counter()-started
        for code,frame in old.items():
            pd.testing.assert_frame_equal(frame.reset_index(drop=True),fetcher._batch_frames[code].reset_index(drop=True))
        return {'scope':'offline synthetic frozen inputs, core fetch/normalize only; excludes CSV writes, preflight, extension and network latency',
                'stocks':count,'trade_dates':len(dates),'old_endpoint_calls':dict(old_calls),'batch_endpoint_calls':dict(new_calls),
                'old_local_seconds':round(old_seconds,3),'batch_local_seconds':round(new_seconds,3),'qfq_csv_frames_equal':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stocks',type=int,default=300)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.stocks<=1000: parser.error('stocks must be 1..1000')
    result=run(args.stocks)
    content=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(content+'\n')
    print(content)
