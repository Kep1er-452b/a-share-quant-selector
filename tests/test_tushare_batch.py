from collections import Counter
from datetime import date

import pandas as pd
import pytest

from utils.data_provider import BaseDataProvider
from utils.tushare_fetcher import TushareFetcher, TushareProviderError
from utils.tushare_batch import BatchSliceStore, BatchSliceError, fetch_date_slice, qfq_stock_frame


def response(dataset, day, symbols=('000001.SZ', '000002.SZ')):
    rows = []
    for symbol in symbols:
        row = {'ts_code': symbol, 'trade_date': day}
        row.update({'open': 10.135, 'high': 11., 'low': 9., 'close': 10.125, 'pre_close': 10., 'vol': 100., 'amount': 20.}
                   if dataset == 'daily' else {'turnover_rate': 1., 'total_mv': 100000.}
                   if dataset == 'daily_basic' else {'adj_factor': 2.})
        rows.append(row)
    return pd.DataFrame(rows)


def fetcher(tmp_path, symbols=('000001.SZ', '000002.SZ')):
    obj = TushareFetcher.__new__(TushareFetcher)
    BaseDataProvider.__init__(obj, tmp_path)
    obj.batch_daily_enabled = True
    obj.stock_meta_file = tmp_path / 'tushare_stock_map.json'
    obj.batch_store = BatchSliceStore(tmp_path / 'batch')
    days = [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)]
    obj.get_trade_dates_between = lambda start, end: [day for day in days if pd.Timestamp(start).date() <= day <= pd.Timestamp(end).date()]
    obj.get_latest_trade_date = lambda: days[-1]
    obj.calls = Counter()
    def call(dataset, **kwargs):
        obj.calls[dataset] += 1
        return response(dataset, kwargs['trade_date'], symbols)
    obj._call_batch_endpoint = call
    return obj


def prepare(obj, symbols=('000001', '000002')):
    obj.prepare_incremental_updates([{'code': code} for code in symbols],
                                   {code: {'latest_date': '2026-09-28'} for code in symbols},
                                   date(2026, 9, 30), [date(2026, 9, 29), date(2026, 9, 30)])


def test_batch_requests_scale_by_dates_not_stock_count(tmp_path):
    symbols = tuple(f'{value:06d}.SZ' for value in range(1, 101))
    obj = fetcher(tmp_path, symbols)
    prepare(obj, tuple(value.split('.')[0] for value in symbols))
    assert obj.calls == {'daily': 3, 'daily_basic': 3, 'adj_factor': 3}
    assert len(obj._batch_frames) == 100
    df = obj.fetch_stock_update('000001', 2)
    assert df.iloc[0]['market_cap'] == 1_000_000_000
    assert df.iloc[0]['amount'] == 20_000
    assert df.iloc[0]['close'] == float('%.2f' % 10.125)
    assert df.date.is_monotonic_decreasing


def test_qfq_matches_installed_sdk_including_rounding():
    import tushare as ts
    raw = pd.concat([response('daily', day, ('000001.SZ',)) for day in ('20260930', '20260929')], ignore_index=True)
    factors = pd.concat([response('adj_factor', day, ('000001.SZ',)) for day in ('20260930', '20260929')], ignore_index=True)
    factors.loc[factors.trade_date == '20260929', 'adj_factor'] = 1.3
    class Api:
        def daily(self, **kwargs):
            return raw.copy()
        def adj_factor(self, **kwargs):
            return factors.copy()
    expected = ts.pro_bar(ts_code='000001.SZ', api=Api(), adj='qfq', adjfactor=True, start_date='20260929', end_date='20260930')
    actual = qfq_stock_frame(raw, factors)
    for field in ('open', 'high', 'low', 'close', 'pre_close', 'adj_factor'):
        assert actual[field].tolist() == expected[field].tolist()


def test_pagination_cap_and_ignored_offset_are_not_completed():
    def good(dataset, **kw):
        symbols = ('000001.SZ', '000002.SZ') if kw['offset'] == 0 else ('000003.SZ',)
        return response(dataset, kw['trade_date'], symbols)
    assert len(fetch_date_slice(good, 'daily', '20260930', page_size=2)) == 3
    with pytest.raises(BatchSliceError, match='no progress'):
        fetch_date_slice(lambda dataset, **kw: response(dataset, kw['trade_date']), 'daily', '20260930', page_size=2)
    with pytest.raises(BatchSliceError, match='limit reached'):
        fetch_date_slice(good, 'daily', '20260930', page_size=2, max_pages=1)


def test_missing_stock_date_uses_authoritative_stock_path(tmp_path):
    obj = fetcher(tmp_path)
    original = obj._call_batch_endpoint
    obj._call_batch_endpoint = lambda dataset, **kw: original(dataset, **kw).iloc[:1] if dataset == 'daily' and kw['trade_date'] == '20260929' else original(dataset, **kw)
    prepare(obj)
    assert '000001' in obj._batch_frames
    assert '000002' not in obj._batch_frames
    assert obj._batch_diagnostics['stock_fallbacks']['000002'] == 'missing_dates_or_suspension'


def test_failed_slice_survives_restart_and_is_retried(tmp_path):
    obj = fetcher(tmp_path)
    original = obj._call_batch_endpoint
    obj._call_batch_endpoint = lambda dataset, **kw: pd.DataFrame() if dataset == 'adj_factor' and kw['trade_date'] == '20260929' else original(dataset, **kw)
    with pytest.raises(TushareProviderError):
        prepare(obj)
    assert not obj._batch_frames
    assert obj.batch_store.state()['slices']['adj_factor/20260929']['status'] == 'failed'
    state = obj.batch_store.state()
    failed = state['plans'][state['active_plan_id']]
    assert failed['status'] == 'failed'
    assert failed['stock_ranges']['000001'] == '20260928'
    assert len(failed['planned_slices']) == 9
    second = fetcher(tmp_path)
    prepare(second)
    assert second.batch_store.state()['slices']['adj_factor/20260929']['status'] == 'validated'
    assert second._batch_diagnostics['cache_hits'] == 3
    state = second.batch_store.state()
    assert state['plans'][state['active_plan_id']]['status'] == 'prepared'
    assert len(state['plans'][state['active_plan_id']]['slices']) == 9


def test_checksum_and_io_failures_are_not_empty_cache(tmp_path):
    store = BatchSliceStore(tmp_path)
    store.save('daily', '20260930', response('daily', '20260930'))
    assert len(store.load('daily', '20260930')) == 2
    path = next((tmp_path / 'slices').glob('*.json'))
    path.write_text('{"data":"broken"}')
    with pytest.raises(BatchSliceError, match='checksum'):
        store.load('daily', '20260930')
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.load('daily', '20260930')


def test_cancel_during_response_preserves_csv_and_cancelled_slice(tmp_path):
    obj = fetcher(tmp_path)
    stopped = [False]
    original = obj._call_batch_endpoint
    def call(dataset, **kwargs):
        result = original(dataset, **kwargs)
        stopped[0] = True
        return result
    obj._call_batch_endpoint = call
    with pytest.raises(InterruptedError):
        obj.prepare_incremental_updates([{'code':'000001'}], {'000001':{'latest_date':'2026-09-28'}}, date(2026,9,30), [date(2026,9,30)], halt_checker=lambda:stopped[0])
    assert obj.batch_store.state()['slices']['daily/20260928']['status'] == 'cancelled'
    state = obj.batch_store.state()
    assert state['plans'][state['active_plan_id']]['status'] == 'cancelled'
    assert obj.csv_manager.list_all_stocks() == []


def test_restart_preserves_original_missing_slices_after_local_dates_advance(tmp_path):
    obj = fetcher(tmp_path)
    original = obj._call_batch_endpoint
    obj._call_batch_endpoint = lambda dataset, **kw: pd.DataFrame() if dataset=='adj_factor' and kw['trade_date']=='20260928' else original(dataset, **kw)
    with pytest.raises(TushareProviderError):
        prepare(obj)
    old_id=obj.batch_store.state()['active_plan_id']
    second=fetcher(tmp_path)
    second.prepare_incremental_updates([{'code':'000001'},{'code':'000002'}],
        {code:{'latest_date':'2026-09-30'} for code in ('000001','000002')},
        date(2026,9,30), [date(2026,9,30)])
    state=second.batch_store.state()
    assert old_id in second._batch_diagnostics['resumed_plans']
    assert '20260928' in second._batch_diagnostics['dates']
    assert state['slices']['adj_factor/20260928']['status']=='validated'
    assert state['plans'][old_id]['status']=='resumed'
