"""Formula, immutable selection evidence, pools and chart read services."""
from __future__ import annotations

import ast
import hashlib
import math
import re
import platform
from importlib.metadata import version, PackageNotFoundError
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import date

import pandas as pd

from research.store import ResearchStore, dumps, identity, now
from utils.formula_engine import compile_formula, _ALLOWED_FUNCTION_NAMES, _COLUMN_ALIASES
from utils.market_watchlist import canonical_equity_symbol, watchlist_identity
from utils.selection_worker import build_worker_context, process_selection_chunk, initialize_selection_worker
from utils.technical import MA, finite_number
from utils.atomic_io import atomic_write_text
from pathlib import Path


def day(value):
    return date.fromisoformat(str(value)).isoformat()


def text(value, maximum=2000):
    value = str(value or '').strip()
    if not value or len(value) > maximum:
        raise ValueError(f'text must contain 1 to {maximum} characters')
    return value


def expand_formula(source, definitions=None, values=None):
    source = text(source)
    definitions, values = definitions or {}, values or {}
    if not isinstance(definitions, dict) or not isinstance(values, dict) or len(definitions) > 10 or set(values) - set(definitions):
        raise ValueError('invalid formula parameters')
    expanded, parameters = source, {}
    for name, spec in definitions.items():
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,15}', name) or name in _COLUMN_ALIASES or name in _ALLOWED_FUNCTION_NAMES:
            raise ValueError('parameter conflicts with a reserved formula name')
        if not isinstance(spec, dict) or set(spec) - {'default', 'min', 'max'}:
            raise ValueError('invalid parameter definition')
        low, high = float(spec.get('min', 1)), float(spec.get('max', 250))
        value = float(values.get(name, spec.get('default', low)))
        if not all(math.isfinite(number) for number in (low, high, value)) or low < 0 or high > 10000 or not low <= value <= high:
            raise ValueError('formula parameter outside its bounds')
        parameters[name] = value
        expanded = re.sub(r'\b' + re.escape(name) + r'\b', f'({value:g})', expanded)
    compiled = compile_formula(expanded)
    if len(list(ast.walk(compiled.tree))) > 300:
        raise ValueError('formula exceeds evaluation complexity budget')
    return expanded, parameters


class SnapshotReader:
    def __init__(self, store, snapshot_id, *, as_of=None, phase='close', price_only=False):
        self.store, self.snapshot = store, store.get('snapshot', snapshot_id)
        self.as_of = day(as_of) if as_of else None
        if phase not in ('open', 'close'):
            raise ValueError('phase must be open or close')
        self.phase, self.price_only = phase, price_only

    def read_analysis_frame(self, symbol):
        symbol = canonical_equity_symbol(self.snapshot['market'], symbol)
        entry = self.snapshot['instruments'].get(symbol)
        if not entry:
            raise KeyError('security outside frozen universe')
        if entry.get('error'):
            raise RuntimeError(entry['error'])
        frame = self.store.read_frame(entry['hash'])
        if self.as_of:
            cutoff = pd.Timestamp(self.as_of)
            frame = frame[frame.date.lt(cutoff) if self.phase == 'open' else frame.date.le(cutoff)].copy()
        frame = frame.sort_values('date', ascending=False).reset_index(drop=True)
        if self.snapshot.get('price_view') == 'raw_with_factors' and not frame.empty:
            if 'adj_factor' not in frame or frame.adj_factor.isna().any() or frame.adj_factor.le(0).any():
                raise ValueError('historical adjustment factors unavailable')
            anchor = float(frame.iloc[0].adj_factor)
            for column in ('open', 'high', 'low', 'close'):
                frame[column] = (frame[column] * frame.adj_factor / anchor).map(lambda value: float('%.2f' % value))
        if self.price_only:
            # Current cap/name snapshots cannot support historical screening.
            frame['market_cap'] = 0.0
        return frame

    def descriptor(self):
        return {'type': 'research_snapshot', 'root': str(self.store.root), 'snapshot_id': self.snapshot['id'],
                'as_of': self.as_of, 'phase': self.phase, 'price_only': self.price_only}


def reader_from_descriptor(spec):
    if not isinstance(spec, dict) or spec.get('type') != 'research_snapshot':
        raise ValueError('unsupported worker reader descriptor')
    return SnapshotReader(ResearchStore(spec['root']), spec['snapshot_id'], as_of=spec.get('as_of'),
                          phase=spec.get('phase', 'close'), price_only=bool(spec.get('price_only')))


class ResearchService:
    def __init__(self, store):
        self.store = store

    def save_formula(self, payload, formula_id=None):
        markets = payload.get('markets', ['a_share'])
        if not isinstance(markets, list) or not markets or set(markets) - {'a_share', 'hong_kong'}:
            raise ValueError('unsupported formula market')
        source = text(payload.get('source'))
        parameters = payload.get('parameters') or {}
        expand_formula(source, parameters)
        item = {'name': text(payload.get('name'), 80), 'description': str(payload.get('description') or '')[:1000],
                'source': source, 'parameters': parameters, 'markets': markets, 'archived': False,
                'code_hash': hashlib.sha256(dumps({'source': source, 'parameters': parameters, 'markets': markets}).encode()).hexdigest()}
        if formula_id:
            self.store.get('formula', formula_id)
        return self.store.put('formula', item, object_id=formula_id)

    def resolve_formula(self, formula_id, revision=None, parameters=None, market='a_share'):
        formula = self.store.get('formula', formula_id, revision)
        if market not in formula['markets'] or formula.get('archived'):
            raise ValueError('formula unavailable for this market')
        source, values = expand_formula(formula['source'], formula['parameters'], parameters)
        return {'formula': source, 'label': formula['name'], 'formula_id': formula['id'],
                'formula_revision': formula['revision'], 'parameters': values, 'code_hash': formula['code_hash']}

    def capture(self, reader, candidates, *, market='a_share', provider='local', price_view='csv_qfq', cancel=None):
        if len(candidates) > 6000:
            raise ValueError('snapshot universe exceeds 6000 securities')
        instruments = {}
        with self.store.connection() as db:
            for code, name in candidates:
                if cancel and cancel():
                    raise InterruptedError('selection snapshot cancelled')
                symbol = canonical_equity_symbol(market, code)
                try:
                    frame = reader.read_analysis_frame(symbol) if hasattr(reader, 'read_analysis_frame') else reader.read_stock_for_analysis(code)
                except Exception as exc:
                    instruments[symbol] = {'name': name, 'error': str(exc)[:500]}
                    continue
                instruments[symbol] = {'name': name, 'hash': self.store.save_frame(frame, db=db), 'rows': len(frame)}
            snapshot = self.store.put('snapshot', {'market': market, 'provider': provider, 'price_view': price_view,
                'instruments': instruments, 'historical_universe': False, 'visibility': 'current_capture'}, db=db)
        return snapshot

    def begin_run(self, snapshot_id, strategies, context=None):
        self.store.get('snapshot', snapshot_id)
        root = Path(__file__).resolve().parent.parent
        paths = list((root / 'strategy').glob('*.py')) + list((root / 'research').glob('*.py'))
        paths += [root / path for path in (
            'utils/technical.py', 'utils/selection_worker.py', 'utils/formula_engine.py',
            'utils/quant_core.py', 'utils/strategy_labels.py', 'utils/market_watchlist.py',
            'market_data/equity_policy.py', 'market_data/equity_symbols.py', 'market_data/hong_kong.py',
            'csrc/quant_core.c', 'csrc/quant_core.h')]
        sources = {str(path.relative_to(root)): path.read_text(encoding='utf-8') for path in paths if path.is_file()}
        missing_sources = [str(path.relative_to(root)) for path in paths if not path.is_file()]
        packages = {}
        for package in ('pandas', 'numpy', 'tushare'):
            try:
                packages[package] = version(package)
            except PackageNotFoundError:
                packages[package] = None
        from utils import quant_core
        core_available = quant_core.available()
        environment = {'python': platform.python_version(), 'packages': packages,
            'quant_core_available': core_available,
            'quant_core_hash': hashlib.sha256(quant_core._library_path().read_bytes()).hexdigest() if core_available else None}
        return self.store.put('run', {'snapshot_id': snapshot_id, 'strategies': strategies, 'context': context or {},
                                      'executor_sources': sources, 'executor_hash': hashlib.sha256(dumps(sources).encode()).hexdigest(),
                                      'executor_source_missing': missing_sources,
                                      'environment': environment,
                                      'status': 'running', 'started_at': now()})

    def finish_run(self, run_id, results, *, errors=0, status=None):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self.store.get('run', run_id, db=db)
            if run['status'] != 'running':
                raise ValueError('run already finalized')
            status = status or ('partial' if errors else 'completed')
            run = self.store.put('run', {**run, 'status': status, 'results': results,
                                         'errors': errors, 'finished_at': now()}, object_id=run_id, db=db)
            if status == 'completed':
                snapshot = self.store.get('snapshot', run['snapshot_id'], db=db)
                key = f"{snapshot['market']}_{snapshot['provider']}"
                self.store.put('last_run', {'run_id': run_id}, object_id=key, db=db)
            return run

    def execute(self, snapshot_id, strategies, params_file, *, runtime_params=None, as_of=None, phase='close',
                price_only=False, backend='thread', score=None, cancel=None, progress=None,
                observer=None, return_data=False, max_workers=4, chunk_size=50, session_id=None, category='all'):
        if backend not in ('sequential', 'thread', 'process') or score not in (None, 'activity_v1'):
            raise ValueError('invalid research execution mode')
        reader = SnapshotReader(self.store, snapshot_id, as_of=as_of, phase=phase, price_only=price_only)
        market = reader.snapshot['market']
        if as_of and not price_only:
            if not reader.snapshot.get('historical_universe'):
                raise ValueError('historical selection requires a historical universe')
            if not reader.snapshot.get('daily_metrics_point_in_time'):
                source = (runtime_params or {}).get('FormulaStrategy',{}).get('formula','')
                names = {node.id.upper() for node in ast.walk(compile_formula(source).tree) if isinstance(node,ast.Name)} if strategies==['FormulaStrategy'] else {'MARKET_CAP'}
                if names & {'MARKET_CAP','CAP','TURNOVER'}:
                    raise ValueError('point-in-time daily market cap/turnover evidence unavailable')
        if price_only:
            if strategies != ['FormulaStrategy']:
                raise ValueError('price replay supports formula strategies only')
            source = (runtime_params or {}).get('FormulaStrategy', {}).get('formula', '')
            compiled = compile_formula(source)
            allowed = {'O','OPEN','H','HIGH','L','LOW','C','CLOSE','V','VOL','VOLUME','AMO','AMOUNT','J','K','D'} | _ALLOWED_FUNCTION_NAMES | {'TRUE','FALSE'}
            if {node.id.upper() for node in ast.walk(compiled.tree) if isinstance(node, ast.Name)} - allowed:
                raise ValueError('formula requires unsupported historical fields')
        params_content = Path(params_file).read_text(encoding='utf-8')
        candidates = [(symbol, '' if price_only else value.get('name', '')) for symbol, value in reader.snapshot['instruments'].items()]
        if as_of and not price_only:
            candidates = []
            for symbol, entry in reader.snapshot['instruments'].items():
                if entry['list_date']>as_of or entry.get('delist_date') and entry['delist_date']<=as_of:
                    continue
                names = [version['name'] for version in entry['name_history'] if version['from']<=as_of and (not version.get('to') or as_of<version['to']) and version.get('known_from',version['from'])<=as_of]
                if len(names)!=1:
                    raise ValueError('historical name/status missing at replay date')
                candidates.append((symbol,names[0]))
        max_workers,chunk_size=int(max_workers),int(chunk_size)
        if not 1<=max_workers<=32 or not 1<=chunk_size<=500:
            raise ValueError('invalid worker budget')
        chunks = [candidates[index:index+chunk_size] for index in range(0, len(candidates), chunk_size)]
        results = {strategy: [] for strategy in strategies}
        indicators = {}
        errors, completed = 0, 0
        run = self.begin_run(snapshot_id, strategies, {'as_of': as_of, 'phase': phase, 'price_only': price_only,
            'runtime_params': runtime_params or {}, 'backend': backend, 'score': score,
            'strategy_parameters_yaml': params_content, 'executor_version': 'research_v1','session_id':session_id,'category':category})
        try:
            frozen_params = self.store.root/'runs'/run['id']/'strategy_params.yaml'
            atomic_write_text(frozen_params, params_content)
            params_file = str(frozen_params)
            context = build_worker_context('', strategies, params_file, runtime_params, market_id=market, reader=reader)
            def consume(part):
                nonlocal errors, completed
                completed += part['processed_count']
                errors += sum(part['error_counts'].values())
                for strategy in strategies:
                    results[strategy].extend(part['results_by_strategy'][strategy])
                indicators.update(part.get('indicators_dict') or {})
                if observer:
                    observer(part)
                if progress:
                    progress(completed, len(candidates))
            if backend == 'sequential':
                for chunk in chunks:
                    if cancel and cancel():
                        raise InterruptedError('research selection cancelled')
                    consume(process_selection_chunk(chunk, category, return_data, context))
            else:
                pool = ProcessPoolExecutor(max_workers=max_workers, initializer=initialize_selection_worker,
                    initargs=('', strategies, params_file, runtime_params, reader.descriptor())) if backend == 'process' else ThreadPoolExecutor(max_workers=max_workers)
                with pool:
                    futures = [pool.submit(process_selection_chunk, chunk, category, return_data) if backend == 'process'
                               else pool.submit(process_selection_chunk, chunk, category, return_data, context) for chunk in chunks]
                    for future in as_completed(futures):
                        if cancel and cancel():
                            for pending in futures:
                                pending.cancel()
                            raise InterruptedError('research selection cancelled')
                        consume(future.result())
            if cancel and cancel():
                raise InterruptedError('research selection cancelled')
            for strategy, items in results.items():
                if score:
                    for item in items:
                        ratios = [finite_number(signal.get('volume_ratio')) for signal in item['signals']]
                        values = [value for value in ratios if value is not None]
                        item['score'] = min(100., max(0., 50 + (max(values) - 1) * 25)) if values else None
                        item['score_version'] = score
                    if all(item['score'] is not None for item in items):
                        items.sort(key=lambda item: (-item['score'], item['symbol']))
                    else:
                        items.sort(key=lambda item: item['symbol'])
                else:
                    items.sort(key=lambda item: item['symbol'])
            finished = self.finish_run(run['id'], results, errors=errors)
            return {**finished,'indicators_dict':indicators} if return_data else finished
        except BaseException as exc:
            self.finish_run(run['id'], results, errors=errors, status='cancelled' if isinstance(exc, InterruptedError) else 'failed')
            raise

    def save_pool(self, payload, pool_id=None):
        if pool_id:
            old = self.store.get('pool', pool_id)
        else:
            old = {'members': {}, 'session_id': payload.get('session_id')}
            if old['session_id']:
                self.store.get('session',old['session_id'])
        return self.store.put('pool', {**old, 'name': text(payload.get('name'), 80), 'archived': bool(payload.get('archived', False))}, object_id=pool_id)

    def member(self, pool_id, market, symbol, *, remove=False, metadata=None):
        key = watchlist_identity(market, symbol)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            pool = self.store.get('pool', pool_id, db=db)
            if pool.get('archived'):
                raise ValueError('pool is archived')
            members = dict(pool['members'])
            if remove:
                members.pop(key, None)
            else:
                values = metadata or {}
                note = str(values.get('note') or '')
                if len(note) > 2000:
                    raise ValueError('note exceeds 2000 characters')
                origin = values.get('run_id')
                if origin:
                    source_run = self.store.get('run', origin, db=db)
                    if pool.get('session_id') and source_run['context'].get('session_id')!=pool['session_id']:
                        raise ValueError('run belongs to another historical session')
                if key not in members and len(members)>=6000:
                    raise ValueError('pool exceeds 6000 securities')
                members[key] = {**members.get(key, {}), 'market': market,
                    'symbol': canonical_equity_symbol(market, symbol), 'note': note,
                    'run_id': origin, 'added_at': members.get(key, {}).get('added_at') or now()}
            return self.store.put('pool', {**pool, 'members': members}, object_id=pool_id, db=db)

    def marker(self, market, symbol, payload):
        key = watchlist_identity(market, symbol).replace(':', '_').replace('.', '_')
        until = day(payload['cooldown_until']) if payload.get('cooldown_until') else None
        return self.store.put('marker', {'market': market, 'symbol': canonical_equity_symbol(market, symbol),
            'cooldown_until': until, 'reason': str(payload.get('reason') or '')[:2000], 'label': str(payload.get('label') or '')[:80]}, object_id=key)

    def migrate_watchlist(self, watchlist):
        # Original file remains an untouched backup; identity/order retained.
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                return self.store.get('pool', 'legacy_watchlist', db=db)
            except KeyError:
                with watchlist._lock:
                    payload, _changed = watchlist._normalized_payload()
                    items = list(payload['items'].values())
                members = {watchlist_identity(item['market'], item['symbol']): {**item, 'added_at':item.get('created_at') or now()} for item in items}
                return self.store.put('pool', {'name':'原有自选', 'members': members, 'session_id':None,
                    'migration_source': str(watchlist.path), 'order': list(members), 'archived':False}, object_id='legacy_watchlist', db=db)

    def chart(self, snapshot_id, symbol, *, as_of=None, phase='close', start=None, end=None, run_id=None, include_indicators=True, account_id=None):
        reader = SnapshotReader(self.store, snapshot_id, as_of=as_of, phase=phase)
        frame = reader.read_analysis_frame(symbol)
        indicators = {f'MA{window}': MA(frame.close, window) for window in (5, 20, 60)} if include_indicators and not frame.empty else {}
        if start and end and day(start) > day(end):
            raise ValueError('range start follows end')
        mask = pd.Series(True, index=frame.index)
        if start:
            mask &= frame.date.ge(pd.Timestamp(day(start)))
        if end:
            mask &= frame.date.le(pd.Timestamp(day(end)))
        selected = frame[mask].sort_values('date')
        visible = selected.tail(2000)
        candles = [{key: value for key, value in zip(('date','open','high','low','close','volume','amount'),
                    (row.date.strftime('%Y-%m-%d'), *[finite_number(row.get(column)) for column in ('open','high','low','close','volume','amount')]))} for _, row in visible.iterrows()]
        stats = None
        if not selected.empty:
            first, last = selected.iloc[0], selected.iloc[-1]
            stats = {'first_date':first.date.strftime('%Y-%m-%d'), 'last_date':last.date.strftime('%Y-%m-%d'), 'bars':len(selected),
                     'return_pct':finite_number((last.close / first.close - 1)*100) if first.close > 0 else None,
                     'amplitude_pct':finite_number((selected.high.max()/selected.low.min()-1)*100) if selected.low.min()>0 else None,
                     'volume':finite_number(selected.volume.sum(min_count=1)), 'price_view':reader.snapshot['price_view']}
        series = [{'type':'line','name':name,'values':[finite_number(values.loc[index]) for index in visible.index]} for name, values in indicators.items()]
        if include_indicators:
            series.append({'type':'bar','name':'成交量','pane':'volume','values':[finite_number(value) for value in visible.volume]})
        marks = []
        if run_id:
            run = self.store.get('run', run_id)
            if run['snapshot_id'] != snapshot_id:
                raise ValueError('signal run belongs to another data version')
            for strategy, items in run.get('results', {}).items():
                for item in items:
                    if item['symbol'] == canonical_equity_symbol(reader.snapshot['market'], symbol):
                        marks.append({'run_id':run_id,'strategy':strategy,'date':item.get('data_as_of'),'reasons':[signal.get('reasons') for signal in item['signals']]})
        trades=[]
        if account_id:
            account=self.store.get('account',account_id)
            session=self.store.get('session',account['session_id'])
            if session['snapshot_id']!=snapshot_id:
                raise ValueError('account belongs to another data version')
            for order in account['orders']:
                if order['symbol']==canonical_equity_symbol(reader.snapshot['market'],symbol):
                    trades.extend({**fill,'side':order['side'],'account_id':account_id} for fill in order['fills'] if not as_of or fill['date']<=as_of)
        return {'snapshot_id':snapshot_id,'candles':candles,'indicators':series,'range':stats,'signals':marks,'trades':trades,
                'price_view':reader.snapshot['price_view'], 'as_of':as_of, 'phase':phase,
                'display_price_view':'qfq' if reader.snapshot['price_view']=='raw_with_factors' else reader.snapshot['price_view'],
                'adjustment_anchor':frame.iloc[0].date.strftime('%Y-%m-%d') if not frame.empty else None,
                'source':reader.snapshot.get('source') or reader.snapshot['provider'],
                'volume_unit':reader.snapshot['instruments'][canonical_equity_symbol(reader.snapshot['market'],symbol)].get('volume_unit') or ('hands' if reader.snapshot['provider']=='tushare' else 'provider_native')}
