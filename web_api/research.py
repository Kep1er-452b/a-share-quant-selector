"""Protected, bounded research APIs; long work is delegated to admitted jobs."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from research.replay import ReplayService
from research.simulation import SimulationService
from research.service import SnapshotReader, text


def create_research_blueprint(service_factory, *, start_job, params_file, manager_factory, watchlist_factory):
    bp=Blueprint('research',__name__,url_prefix='/api/research')

    @bp.before_request
    def bounded_body():
        if request.content_length and request.content_length>10*1024*1024:
            return jsonify(success=False,error='研究请求最大 10 MiB'),413

    @bp.errorhandler(ValueError)
    @bp.errorhandler(TypeError)
    def invalid(exc):
        return jsonify(success=False,error=str(exc)),400

    @bp.errorhandler(KeyError)
    def missing(exc):
        return jsonify(success=False,error='研究记录不存在'),404

    def body():
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict):
            raise ValueError('请求体必须是 JSON 对象')
        return payload

    def response(data):
        return jsonify(success=True,data=data)

    def summary(kind,item):
        if kind in ('run','snapshot','account','pool'):
            keys={'id','revision','name','status','updated_at','snapshot_id','strategies','provider','market','price_view','historical_universe','session_id','initial_cash','cash','rule_version','archived','data_scope'}
            result={key:value for key,value in item.items() if key in keys}
            if kind=='snapshot':
                result['instrument_count']=len(item['instruments'])
                result['symbols']=list(item['instruments'])[:100]
            if kind=='run':
                result['as_of']=item.get('context',{}).get('as_of')
                result['selected_count']=sum(len(items) for items in item.get('results',{}).values())
            if kind=='pool':
                result['member_count']=len(item['members'])
            return result
        return item

    @bp.get('/<kind>')
    def list_objects(kind):
        kinds={'formulas':'formula','runs':'run','snapshots':'snapshot','pools':'pool','markers':'marker','sessions':'session','checkpoints':'checkpoint','accounts':'account'}
        if kind not in kinds:
            raise ValueError('unsupported research collection')
        page=service_factory().store.list(kinds[kind],limit=request.args.get('limit',100),offset=request.args.get('offset',0))
        page['items']=[summary(kinds[kind],item) for item in page['items']]
        return response(page)

    @bp.get('/<kind>/<object_id>')
    def get_object(kind,object_id):
        kinds={'formulas':'formula','runs':'run','snapshots':'snapshot','pools':'pool','sessions':'session','checkpoints':'checkpoint','accounts':'account'}
        if kind not in kinds:
            raise ValueError('unsupported research collection')
        service=service_factory()
        if kind=='accounts':
            return response(SimulationService(service.store).report(object_id))
        item=service.store.get(kinds[kind],object_id,request.args.get('revision'))
        if kind=='runs':
            item.pop('executor_sources',None)
        return response(item)

    @bp.post('/formulas')
    @bp.post('/formulas/<formula_id>')
    def save_formula(formula_id=None):
        return response(service_factory().save_formula(body(),formula_id))

    @bp.post('/formulas/<formula_id>/archive')
    def archive_formula(formula_id):
        store=service_factory().store
        formula=store.get('formula',formula_id)
        return response(store.put('formula',{**formula,'archived':True},object_id=formula_id))

    @bp.post('/formulas/<formula_id>/test')
    def test_formula(formula_id):
        payload=body()
        service=service_factory()
        snapshot_id=payload['snapshot_id']
        snapshot=service.store.get('snapshot',snapshot_id)
        spec=service.resolve_formula(formula_id,payload.get('revision'),payload.get('parameters'),snapshot['market'])
        from utils.market_watchlist import canonical_equity_symbol
        symbol=canonical_equity_symbol(snapshot['market'],payload.get('symbol'))
        if len(snapshot['instruments'])>100 and not symbol:
            raise ValueError('单股测试必须指定证券')
        def work(cancel,progress):
            reader=SnapshotReader(service.store,snapshot_id)
            frame=service.store.read_frame(snapshot['instruments'][symbol]['hash'])
            class OneReader:
                def read_analysis_frame(self,_symbol):
                    return frame.copy()
            captured=service.capture(OneReader(),[(symbol,snapshot['instruments'][symbol]['name'])],provider=snapshot['provider'],market=snapshot['market'],price_view=snapshot['price_view'],cancel=cancel)
            return service.execute(captured['id'],['FormulaStrategy'],params_file(),runtime_params={'FormulaStrategy':spec},backend='sequential',cancel=cancel,progress=progress)
        return start_job('单股公式测试',work)

    @bp.post('/capture')
    def capture():
        payload=body()
        symbols=payload.get('symbols')
        if symbols is not None and (not isinstance(symbols,list) or not 1<=len(symbols)<=100):
            raise ValueError('选择 1 至 100 只股票，或省略 symbols 冻结当前仓库')
        def work(cancel,progress):
            service=service_factory()
            manager=manager_factory()
            from utils.stock_exporter import load_stock_names
            names=load_stock_names(manager.data_dir)
            codes=symbols or manager.list_all_stocks()
            from utils.market_watchlist import canonical_equity_symbol
            codes=[canonical_equity_symbol('a_share',symbol).split('.')[0] for symbol in codes]
            if not codes:
                raise ValueError('当前行情仓库为空')
            candidates=[(code,names.get(code,code)) for code in codes]
            provider=manager.data_dir.name if manager.data_dir.parent.name=='providers' else 'local'
            return service.capture(manager,candidates,provider=provider,cancel=cancel)
        return start_job('冻结当前行情输入',work)

    @bp.post('/archives')
    def archive():
        payload=body()
        return start_job('导入历史原始行情档案',lambda cancel,progress: ReplayService(service_factory()).import_archive(payload,cancel=cancel))

    @bp.post('/runs')
    def run():
        payload=body()
        service=service_factory()
        session=None
        if payload.get('session_id'):
            session=service.store.get('session',payload['session_id'])
            snapshot_id=session['snapshot_id']
        else:
            snapshot_id=payload['snapshot_id']
        snapshot=service.store.get('snapshot',snapshot_id)
        strategies=payload.get('strategies') or ['FormulaStrategy']
        if not isinstance(strategies,list) or not 1<=len(strategies)<=20 or any(not isinstance(value,str) or len(value)>80 for value in strategies):
            raise ValueError('invalid research strategies')
        runtime={}
        if 'FormulaStrategy' in strategies:
            runtime={'FormulaStrategy':service.resolve_formula(payload['formula_id'],payload.get('formula_revision'),payload.get('parameters'),snapshot['market'])}
        score=payload.get('score')
        backend=payload.get('backend','thread')
        if backend not in ('sequential','thread','process') or score not in (None,'activity_v1'):
            raise ValueError('invalid execution settings')
        def work(cancel, progress):
            if cancel():
                raise InterruptedError('研究任务已取消')
            if session:
                spec = runtime.get('FormulaStrategy', {})
                ReplayService(service).configure(session['id'], {
                    'strategies': strategies, 'formula_id': spec.get('formula_id'),
                    'formula_revision': spec.get('formula_revision'), 'formula_name': spec.get('label'),
                    'parameters': spec.get('parameters', {}), 'backend': backend, 'score': score
                }, expected_revision=session['revision'])
            return service.execute(snapshot_id,strategies,params_file(),runtime_params=runtime,
                as_of=session['as_of'] if session else None,phase=session['phase'] if session else 'close',
                price_only=session['mode']=='price_only' if session else False,session_id=session['id'] if session else None,
                score=score,backend=backend,cancel=cancel,progress=progress)
        return start_job('研究选股运行', work)

    @bp.post('/pools')
    @bp.post('/pools/<pool_id>')
    def pool(pool_id=None):
        return response(service_factory().save_pool(body(),pool_id))

    @bp.post('/pools/initialize')
    def initialize_pools():
        service=service_factory()
        for pool_id,name in (('initial','初筛'),('study','研究中'),('verified','已验证'),('deferred','暂缓')):
            try:
                service.store.get('pool',pool_id)
            except KeyError:
                service.store.put('pool',{'name':name,'members':{},'session_id':None,'archived':False},object_id=pool_id)
        return response(service.migrate_watchlist(watchlist_factory()))

    @bp.post('/pools/<pool_id>/members')
    def member(pool_id):
        payload=body()
        return response(service_factory().member(pool_id,payload.get('market','a_share'),payload['symbol'],remove=bool(payload.get('remove')),metadata=payload))

    @bp.post('/markers')
    def marker():
        payload=body()
        return response(service_factory().marker(payload.get('market','a_share'),payload['symbol'],payload))

    @bp.get('/chart')
    def chart():
        flag=request.args.get('indicators','1')
        if flag not in ('0','1'):
            raise ValueError('invalid indicator option')
        return response(service_factory().chart(request.args['snapshot_id'],request.args['symbol'],
            as_of=request.args.get('as_of'),phase=request.args.get('phase','close'),start=request.args.get('start'),end=request.args.get('end'),run_id=request.args.get('run_id'),include_indicators=flag=='1',account_id=request.args.get('account_id')))

    @bp.post('/sessions')
    def session():
        payload=body()
        return response(ReplayService(service_factory()).create(payload['snapshot_id'],payload['as_of'],payload.get('phase','close'),payload.get('mode','price_only')))

    @bp.post('/sessions/<session_id>/advance')
    def advance(session_id):
        return response(ReplayService(service_factory()).advance(session_id))

    @bp.post('/sessions/<session_id>/checkpoint')
    def checkpoint(session_id):
        return response(ReplayService(service_factory()).checkpoint(session_id))

    @bp.post('/checkpoints/<checkpoint_id>/restore')
    def restore(checkpoint_id):
        return response(ReplayService(service_factory()).restore(checkpoint_id))

    @bp.post('/accounts')
    def account():
        payload=body()
        return response(SimulationService(service_factory().store).create(payload['session_id'],payload['cash'],payload.get('assumptions')))

    @bp.post('/accounts/<account_id>/orders')
    def order(account_id):
        payload=body()
        return response(SimulationService(service_factory().store).submit(account_id,payload['symbol'],payload['side'],payload['quantity'],request_id=payload['request_id'],run_id=payload.get('run_id')))

    @bp.post('/accounts/<account_id>/orders/<order_id>/cancel')
    def cancel_order(account_id,order_id):
        return response(SimulationService(service_factory().store).cancel(account_id,order_id))

    @bp.post('/accounts/<account_id>/settle')
    def settle(account_id):
        return response(SimulationService(service_factory().store).settle_open(account_id))

    @bp.post('/accounts/<account_id>/mark')
    def mark(account_id):
        return response(SimulationService(service_factory().store).mark_close(account_id))

    return bp
