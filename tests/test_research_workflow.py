import json
from pathlib import Path

import pandas as pd
import pytest

from research.store import ResearchStore
from research.service import ResearchService, SnapshotReader, expand_formula
from research.replay import ReplayService
from research.simulation import SimulationService
from utils.csv_manager import CSVManager
from utils.market_watchlist import MarketWatchlistStore

PARAMS=Path(__file__).resolve().parents[1]/'config/strategy_params.yaml'


def bars(rows=80):
    dates=pd.bdate_range('2026-05-01',periods=rows)
    return [{'date':date.strftime('%Y-%m-%d'),'open':10.,'high':11.,'low':9.,'close':10.,'volume':100000.,
             'amount':1000000.,'adj_factor':1.,'up_limit':11.,'down_limit':9.,'market_cap':1e9,'turnover':1.} for date in dates]


def archive_payload(data=None):
    return {'market':'a_share','price_view':'raw_with_factors','source':'offline acceptance fixture',
            'historical_universe':True,'daily_metrics_point_in_time':True,
            'instruments':[{'symbol':'000001.SZ','name':'Test','list_date':'2020-01-01',
                            'name_history':[{'from':'2020-01-01','name':'Test'}], 'bars':data or bars()}]}


@pytest.fixture
def service(tmp_path):
    return ResearchService(ResearchStore(tmp_path/'research'))


def test_formula_immutable_versions_and_parameter_limits(service):
    first=service.save_formula({'name':'Above MA','source':'C > MA(C, N)','parameters':{'N':{'default':20,'min':2,'max':60}}})
    second=service.save_formula({'name':'Above MA','source':'C >= MA(C, N)','parameters':{'N':{'default':10,'min':2,'max':60}}},first['id'])
    assert first['revision']==1 and second['revision']==2
    assert service.resolve_formula(first['id'],1)['formula']=='C > MA(C, (20))'
    assert service.resolve_formula(first['id'],2,{'N':5})['formula']=='C >= MA(C, (5))'
    with pytest.raises(ValueError):
        service.resolve_formula(first['id'],1,{'N':10000})
    with pytest.raises(ValueError):
        expand_formula('C > 0',{'C':{'default':2}})
    with pytest.raises(ValueError):
        expand_formula('__import__("os")')


def test_actual_snapshot_survives_warehouse_changes_and_preserves_float(service,tmp_path):
    manager=CSVManager(tmp_path/'providers'/'tushare')
    frame=pd.DataFrame(bars())
    frame.loc[0,'open']=9.123456789012345
    manager.write_stock('000001',frame)
    original=manager.read_stock_for_analysis('000001').reset_index(drop=True)
    snapshot=service.capture(manager,[('000001','Test')],provider='tushare')
    stored=SnapshotReader(service.store,snapshot['id']).read_analysis_frame('000001')
    assert stored.open.tolist()==original.open.tolist()
    changed=original.copy(); changed.close=100
    manager.write_stock('000001',changed)
    assert SnapshotReader(service.store,snapshot['id']).read_analysis_frame('000001').close.tolist()==original.close.tolist()


@pytest.mark.parametrize('backend',['sequential','thread','process'])
def test_shared_worker_frozen_execution_equivalence(service,backend):
    snapshot=ReplayService(service).import_archive(archive_payload())
    run=service.execute(snapshot['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'C > 0','label':'All'}},backend=backend)
    assert run['status']=='completed'
    assert [item['symbol'] for item in run['results']['FormulaStrategy']]==['000001.SZ']
    assert service.store.get('last_run','a_share_imported')['run_id']==run['id']
    assert run['executor_sources'] and run['context']['strategy_parameters_yaml']
    assert 'utils/quant_core.py' in run['executor_sources']
    assert 'research/service.py' in run['executor_sources']
    assert run['environment']['packages']['pandas']


def test_result_pointer_atomic_cancel_and_failed_publish(service,monkeypatch):
    snapshot=ReplayService(service).import_archive(archive_payload())
    first=service.begin_run(snapshot['id'],['FormulaStrategy'])
    service.finish_run(first['id'],{})
    second=service.begin_run(snapshot['id'],['FormulaStrategy'])
    original=service.store.put
    def fault(kind,*args,**kw):
        if kind=='last_run':
            raise OSError('disk failure')
        return original(kind,*args,**kw)
    monkeypatch.setattr(service.store,'put',fault)
    with pytest.raises(OSError):
        service.finish_run(second['id'],{'FormulaStrategy':[]})
    assert service.store.get('run',second['id'])['status']=='running'
    assert service.store.get('last_run','a_share_imported')['run_id']==first['id']
    monkeypatch.setattr(service.store,'put',original)
    with pytest.raises(InterruptedError):
        service.execute(snapshot['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'C>0'}},cancel=lambda:True)
    assert service.store.list('run')['items'][0]['status']=='cancelled'
    assert service.store.get('last_run','a_share_imported')['run_id']==first['id']


def test_multi_pools_cooldown_and_migration_preserve_original(service,tmp_path):
    path=tmp_path/'watchlist.json'
    original={'items':{'000001':{'name':'Test','note':'old note'}}}
    path.write_text(json.dumps(original))
    watchlist=MarketWatchlistStore(path)
    migrated=service.migrate_watchlist(watchlist)
    assert json.loads(path.read_text())==original
    assert service.migrate_watchlist(watchlist)['id']==migrated['id']
    one=service.save_pool({'name':'One'}); two=service.save_pool({'name':'Two'})
    service.member(one['id'],'a_share','000001')
    service.member(two['id'],'a_share','000001',metadata={'note':'two'})
    service.member(one['id'],'a_share','000001',remove=True)
    assert service.store.get('pool',one['id'])['members']=={}
    assert service.store.get('pool',two['id'])['members']['a_share:000001.SZ']['note']=='two'
    marker=service.marker('a_share','000001',{'cooldown_until':'2026-10-12','reason':'wait'})
    assert marker['symbol']=='000001.SZ'
    assert len(service.store.get('pool',two['id'])['members'])==1


def test_replay_future_append_and_open_phase_do_not_change_signals(service):
    data=bars(); as_of=data[65]['date']
    snapshot=ReplayService(service).import_archive(archive_payload(data))
    before=SnapshotReader(service.store,snapshot['id'],as_of=as_of).read_analysis_frame('000001')
    changed=bars()
    for row in changed[66:]:
        for field in ('open','high','low','close'):
            row[field]*=100
        row['adj_factor']=10
    other=ReplayService(service).import_archive(archive_payload(changed))
    after=SnapshotReader(service.store,other['id'],as_of=as_of).read_analysis_frame('000001')
    pd.testing.assert_frame_equal(before,after)
    opening=SnapshotReader(service.store,snapshot['id'],as_of=as_of,phase='open').read_analysis_frame('000001')
    assert opening.date.max()<pd.Timestamp(as_of)
    outputs=[]
    for current in (snapshot,other):
        run=service.execute(current['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'C > 0'}},as_of=as_of,backend='sequential',price_only=True)
        outputs.append(run['results'])
    assert outputs[0]==outputs[1]
    with pytest.raises(ValueError,match='unsupported historical'):
        service.execute(snapshot['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'CAP > 0'}},as_of=as_of,price_only=True)


def test_point_in_time_listing_names_and_daily_field_gates(service):
    payload=archive_payload()
    payload['instruments'][0]['name_history']=[{'from':'2020-01-01','to':bars()[70]['date'],'name':'Test'}, {'from':bars()[70]['date'],'name':'ST Test'}]
    snapshot=ReplayService(service).import_archive(payload)
    before=service.execute(snapshot['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'C>0'}},as_of=bars()[65]['date'],backend='sequential')
    after=service.execute(snapshot['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'C>0'}},as_of=bars()[75]['date'],backend='sequential')
    assert len(before['results']['FormulaStrategy'])==1
    assert after['results']['FormulaStrategy']==[]
    payload['daily_metrics_point_in_time']=False
    unsupported=ReplayService(service).import_archive(payload)
    with pytest.raises(ValueError,match='market cap'):
        service.execute(unsupported['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'CAP>0'}},as_of=bars()[65]['date'])
    with pytest.raises(ValueError,match='turnover'):
        service.execute(unsupported['id'],['FormulaStrategy'],str(PARAMS),runtime_params={'FormulaStrategy':{'formula':'TURNOVER>0'}},as_of=bars()[65]['date'])


def setup_account(service, *, capacity=.01, data=None, cash=100000):
    replay=ReplayService(service)
    snapshot=replay.import_archive(archive_payload(data))
    session=replay.create(snapshot['id'],bars()[65]['date'])
    sim=SimulationService(service.store)
    account=sim.create(session['id'],cash,{'slippage_bps':0,'previous_volume_capacity':capacity})
    return replay,session,sim,account


def test_paper_next_open_t1_fee_cash_and_idempotence(service):
    replay,session,sim,account=setup_account(service)
    ordered=sim.submit(account['id'],'000001','buy',100,request_id='buy1')
    assert ordered==sim.submit(account['id'],'000001','buy',100,request_id='buy1')
    with pytest.raises(ValueError,match='reused'):
        sim.submit(account['id'],'000001','buy',200,request_id='buy1')
    with pytest.raises(ValueError,match='only at replay open'):
        sim.settle_open(account['id'])
    replay.advance(session['id'])
    filled=sim.settle_open(account['id'])
    assert filled['cash']==98994.99
    assert filled['positions']['000001.SZ']['quantity']==100
    assert filled['orders'][0]['fills'][0]['date']==bars()[66]['date']
    assert sim.settle_open(account['id'])==filled
    with pytest.raises(ValueError,match=r'T\+1'):
        sim.submit(account['id'],'000001','sell',100,request_id='same_day')
    replay.advance(session['id']); replay.advance(session['id'])
    sim.submit(account['id'],'000001','sell',100,request_id='sell1')
    assert sim.settle_open(account['id'])['orders'][1]['status']=='pending'
    replay.advance(session['id']); replay.advance(session['id'])
    sold=sim.settle_open(account['id'])
    assert sold['positions']['000001.SZ']['quantity']==0
    assert sold['cash']==99989.48
    report=sim.report(account['id'])
    assert report['fees']=={'commission':10.,'transfer':.02,'stamp':.5}
    replay.advance(session['id']); sim.mark_close(account['id'])
    assert sim.report(account['id'])['equity']==99989.48


def test_capacity_partial_fill_cancel_and_limit_block(service):
    replay,session,sim,account=setup_account(service,capacity=.001)
    sim.submit(account['id'],'000001','buy',300,request_id='partial')
    replay.advance(session['id'])
    result=sim.settle_open(account['id'])
    assert result['orders'][0]['status']=='partial' and result['orders'][0]['remaining']==200
    cancelled=sim.cancel(account['id'],result['orders'][0]['id'])
    assert cancelled['orders'][0]['reserved']==0
    assert sim.available_cash(cancelled)==cancelled['cash']
    data=bars(); data[66]['open']=11.; data[66]['high']=11.
    replay,session,sim,account=setup_account(service,data=data)
    sim.submit(account['id'],'000001','buy',100,request_id='limit')
    replay.advance(session['id'])
    blocked=sim.settle_open(account['id'])
    assert blocked['cash']==100000 and blocked['orders'][0]['waiting_reason']=='opening_price_at_limit'


def test_checkpoint_restores_account_and_session_pool_as_branch(service):
    replay,session,sim,account=setup_account(service)
    execution={'formula_id':'fixture', 'formula_revision':1, 'parameters':{'N':20}}
    replay.configure(session['id'],execution,expected_revision=session['revision'])
    pool=service.save_pool({'name':'session pool','session_id':session['id']})
    service.member(pool['id'],'a_share','000001')
    checkpoint=replay.checkpoint(session['id'])
    sim.submit(account['id'],'000001','buy',100,request_id='later')
    replay.advance(session['id']); sim.settle_open(account['id'])
    service.member(pool['id'],'a_share','000001',remove=True)
    branch=replay.restore(checkpoint['id'])
    assert branch['as_of']==bars()[65]['date'] and branch['id']!=session['id']
    assert branch['execution']==execution
    accounts=[item for item in service.store.list('account')['items'] if item['session_id']==branch['id']]
    pools=[item for item in service.store.list('pool')['items'] if item['session_id']==branch['id']]
    assert accounts[0]['cash']==100000 and accounts[0]['orders']==[]
    assert len(pools[0]['members'])==1
    assert service.store.get('account',account['id'])['cash']==98994.99


def test_session_configuration_rejects_stale_revision(service):
    replay,session,sim,account=setup_account(service)
    replay.advance(session['id'])
    with pytest.raises(ValueError,match='会话已变化'):
        replay.configure(session['id'],{},expected_revision=session['revision'])


def test_order_cannot_use_same_day_future_close_signal(service):
    replay,session,sim,account=setup_account(service)
    opening=replay.advance(session['id'])
    run=service.begin_run(session['snapshot_id'],['FormulaStrategy'],{'as_of':opening['as_of'],'phase':'close'})
    service.finish_run(run['id'],{})
    with pytest.raises(ValueError,match='future date'):
        sim.submit(account['id'],'000001','buy',100,request_id='future_close',run_id=run['id'])


def test_dividend_bonus_receivables_and_unexplained_action_guard(service):
    data=bars()
    for row in data[68:]:
        row.update(open=5.,close=5.,high=5.5,low=4.5,up_limit=5.5,down_limit=4.5,adj_factor=2.)
    payload=archive_payload(data)
    payload['instruments'][0]['corporate_actions']=[{'record_date':data[67]['date'],'ex_date':data[68]['date'],
        'pay_date':data[69]['date'],'delivery_date':data[69]['date'],'bonus_ratio':1.,'cash_per_share_net':.1}]
    replay=ReplayService(service); snapshot=replay.import_archive(payload)
    session=replay.create(snapshot['id'],data[65]['date']); sim=SimulationService(service.store)
    account=sim.create(session['id'],100000,{'slippage_bps':0})
    sim.submit(account['id'],'000001','buy',100,request_id='action')
    replay.advance(session['id']); sim.settle_open(account['id'])
    for _ in range(4): replay.advance(session['id'])
    assert sim.report(account['id'])['equity'] is None
    ex=sim.settle_open(account['id'])
    assert ex['receivables'][0]['cash']==10 and ex['receivables'][0]['shares']==100
    assert sim.report(account['id'])['equity']==100004.99
    replay.advance(session['id']); replay.advance(session['id'])
    assert sim.report(account['id'])['equity'] is None
    delivered=sim.settle_open(account['id'])
    assert delivered['positions']['000001.SZ']['quantity']==200
    assert delivered['cash']==99004.99
    sim._audit(delivered)


def test_warmup_chart_range_and_run_identity(service):
    data=bars()
    for index,row in enumerate(data):
        row['close']=10+index*.01; row['high']=max(row['close'],11.)
    snapshot=ReplayService(service).import_archive(archive_payload(data))
    result=service.chart(snapshot['id'],'000001',start=data[60]['date'],end=data[65]['date'])
    assert result['range']['bars']==6
    expected=sum(10+index*.01 for index in range(41,61))/20
    assert result['indicators'][1]['values'][0]==pytest.approx(expected)
    assert result['candles'][0]['date']==data[60]['date']


def test_missing_action_blocks_nav_and_cash_never_negative_on_price_gap(service):
    data=bars()
    for row in data[68:]:
        row.update(open=5.,close=5.,high=5.5,low=4.5,up_limit=5.5,down_limit=4.5,adj_factor=2.)
    replay,session,sim,account=setup_account(service,data=data)
    sim.submit(account['id'],'000001','buy',100,request_id='missing_action')
    replay.advance(session['id']); sim.settle_open(account['id'])
    for _ in range(5): replay.advance(session['id'])
    assert sim.report(account['id'])['equity'] is None
    with pytest.raises(ValueError,match='actions required'):
        sim.mark_close(account['id'])
    rising=bars(); rising[66].update(open=20.,close=20.,high=21.,low=19.,up_limit=22.,down_limit=18.)
    replay,session,sim,account=setup_account(service,data=rising,cash=3100)
    sim.submit(account['id'],'000001','buy',300,request_id='cash_gap')
    replay.advance(session['id']); filled=sim.settle_open(account['id'])
    assert filled['cash']>=0 and sim.available_cash(filled)>=0
    sim._audit(filled)


def test_ledger_corruption_and_archive_future_metadata_rejected(service):
    replay,session,sim,account=setup_account(service)
    broken={**account,'cash':account['cash']+1}
    service.store.put('account',broken,object_id=account['id'])
    with pytest.raises(RuntimeError,match='cash ledger'):
        sim.report(account['id'])
    payload=archive_payload()
    payload['instruments'][0]['name_history'][0]['known_from']='2027-01-01'
    with pytest.raises(ValueError,match='future-known'):
        replay.import_archive(payload)


def test_decimal_slippage_half_cent_rounding(service):
    replay,session,sim,account=setup_account(service)
    account=sim.create(session['id'],100000,{'slippage_bps':5})
    sim.submit(account['id'],'000001','buy',100,request_id='rounding')
    replay.advance(session['id']); filled=sim.settle_open(account['id'])
    assert filled['orders'][0]['fills'][0]['price']==10.01
    assert filled['cash']==98993.99
    sim._audit(filled)
