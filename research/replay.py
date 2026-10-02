"""Frozen historical views and branchable replay sessions."""
from __future__ import annotations

from copy import deepcopy
import math

import pandas as pd

from research.service import day, text
from utils.market_watchlist import canonical_equity_symbol


class ReplayService:
    def __init__(self, research):
        self.research, self.store = research, research.store

    def import_archive(self, payload, cancel=None):
        if payload.get('market') != 'a_share' or payload.get('price_view') != 'raw_with_factors':
            raise ValueError('archive must declare A-share raw_with_factors')
        source = text(payload.get('source'), 500)
        for flag in ('historical_universe', 'daily_metrics_point_in_time'):
            if flag in payload and not isinstance(payload[flag], bool):
                raise ValueError('historical evidence flags must be booleans')
        instruments = payload.get('instruments')
        if not isinstance(instruments, list) or not 1 <= len(instruments) <= 100:
            raise ValueError('import 1 to 100 instruments per archive')
        entries, total = {}, 0
        with self.store.connection() as db:
            for item in instruments:
                if cancel and cancel():
                    raise InterruptedError('historical archive import cancelled')
                symbol = canonical_equity_symbol('a_share', item.get('symbol'))
                if symbol in entries:
                    raise ValueError('duplicate archive identity')
                frame = pd.DataFrame(item.get('bars') or [])
                required = {'date','open','high','low','close','volume','amount','adj_factor'}
                if required - set(frame) or not 1 <= len(frame) <= 10000:
                    raise ValueError('archive missing bars or required price/factor fields')
                allowed = required | {'pre_close','up_limit','down_limit','turnover','market_cap'}
                if set(frame) - allowed:
                    raise ValueError('unsupported historical columns')
                frame.date = frame.date.map(day)
                if frame.date.duplicated().any():
                    raise ValueError('duplicate archive date')
                frame.date = pd.to_datetime(frame.date)
                for column in frame.columns.difference(['date']):
                    frame[column] = pd.to_numeric(frame[column], errors='raise')
                    if not frame[column].map(lambda value: math.isfinite(float(value)) and value >= 0).all():
                        raise ValueError('invalid archive numeric field')
                if (frame[['open','high','low','close','adj_factor']] <= 0).any().any() or (frame.high < frame[['open','close','low']].max(axis=1)).any() or (frame.low > frame[['open','close']].min(axis=1)).any():
                    raise ValueError('invalid archive prices/factors')
                if payload.get('daily_metrics_point_in_time') and ({'market_cap','turnover'}-set(frame) or frame.market_cap.le(0).any()):
                    raise ValueError('point-in-time daily metrics require positive dated market_cap and turnover')
                for column in ('turnover', 'market_cap'):
                    if column not in frame:
                        frame[column] = 0.0
                listing = day(item['list_date']) if item.get('list_date') else None
                delisting = day(item['delist_date']) if item.get('delist_date') else None
                if listing and delisting and listing >= delisting:
                    raise ValueError('invalid historical listing interval')
                if listing and frame.date.lt(pd.Timestamp(listing)).any() or delisting and frame.date.ge(pd.Timestamp(delisting)).any():
                    raise ValueError('archive bars outside listing interval')
                history = item.get('name_history') or []
                if not isinstance(history, list) or len(history) > 100:
                    raise ValueError('invalid name history')
                previous_end = None
                for version in sorted(history, key=lambda value: value['from']):
                    start, end = day(version['from']), day(version['to']) if version.get('to') else '9999-12-31'
                    known = day(version.get('known_from', start))
                    text(version['name'], 80)
                    if start >= end or known > start or (previous_end and start < previous_end):
                        raise ValueError('overlapping or future-known name history')
                    previous_end = end
                unit = item.get('volume_unit', 'shares')
                if unit not in ('shares','hands'):
                    raise ValueError('archive volume_unit must be shares or hands')
                actions = item.get('corporate_actions') or []
                if not isinstance(actions,list) or len(actions)>100:
                    raise ValueError('invalid corporate actions')
                for action in actions:
                    if day(action['record_date'])>=day(action['ex_date']):
                        raise ValueError('action record date must precede ex date')
                    for due in ('pay_date','delivery_date'):
                        if action.get(due) and day(action[due])<day(action['ex_date']):
                            raise ValueError('action payment/delivery precedes ex date')
                    for field in ('cash_per_share_net','bonus_ratio'):
                        value=float(action.get(field,0))
                        if not math.isfinite(value) or not 0<=value<=100:
                            raise ValueError('invalid action amount')
                    if float(action.get('cash_per_share_net',0))>0 and not action.get('pay_date'):
                        raise ValueError('cash dividend requires explicit pay_date')
                    if float(action.get('bonus_ratio',0))>0 and not action.get('delivery_date'):
                        raise ValueError('bonus shares require explicit delivery_date')
                entries[symbol] = {'name':str(item.get('name') or symbol), 'hash':self.store.save_frame(frame.sort_values('date', ascending=False), db=db),
                    'rows':len(frame), 'list_date':listing, 'delist_date':delisting, 'name_history':history, 'volume_unit':unit,'corporate_actions':actions}
                total += len(frame)
                if total > 100000:
                    raise ValueError('archive exceeds 100000 rows')
            snapshot = self.store.put('snapshot', {'market':'a_share','provider':'imported','price_view':'raw_with_factors',
                'source':source,'source_revision':str(payload.get('source_revision') or '')[:200], 'instruments':entries,
                'historical_universe':bool(payload.get('historical_universe')),
                'daily_metrics_point_in_time':bool(payload.get('daily_metrics_point_in_time')),
                'visibility':'daily_close_with_open_price', 'scope':'supplied_instruments'}, db=db)
        return snapshot

    def create(self, snapshot_id, as_of, phase='close', mode='price_only'):
        snapshot = self.store.get('snapshot', snapshot_id)
        if snapshot['market'] != 'a_share' or phase not in ('open','close') or mode not in ('price_only','point_in_time'):
            raise ValueError('invalid replay context')
        as_of = day(as_of)
        if mode == 'point_in_time' and (snapshot['price_view'] != 'raw_with_factors' or not snapshot.get('historical_universe') or
                any(not item.get('list_date') or not item.get('name_history') for item in snapshot['instruments'].values())):
            raise ValueError('point-in-time replay requires raw prices, historical listing and name metadata')
        dates = self.dates(snapshot)
        if as_of not in dates:
            raise ValueError('replay date outside frozen trading sessions')
        return self.store.put('session', {'snapshot_id':snapshot_id,'as_of':as_of,'phase':phase,'mode':mode,
            'data_scope':snapshot.get('scope','current_universe'), 'status':'active',
            'limitations': [] if mode == 'point_in_time' else ['当前集合可能有生存者偏差','当前CSV复权锚点只支持价格复盘','禁用历史市值和当前名称筛选']})

    def dates(self, snapshot):
        return sorted({value.strftime('%Y-%m-%d') for entry in snapshot['instruments'].values() if entry.get('hash')
                       for value in self.store.read_frame(entry['hash']).date})

    def configure(self, session_id, execution, *, expected_revision):
        """Freeze the session's selected formula/settings for checkpoint branches."""
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            session = self.store.get('session', session_id, db=db)
            if session['revision'] != expected_revision:
                raise ValueError('历史会话已变化，请刷新后重新提交')
            return self.store.put('session', {**session, 'execution': deepcopy(execution)}, object_id=session_id, db=db)

    def advance(self, session_id):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            session = self.store.get('session', session_id, db=db)
            if session['phase'] == 'open':
                next_day, phase = session['as_of'], 'close'
            else:
                snapshot = self.store.get('snapshot', session['snapshot_id'], db=db)
                dates = self.dates(snapshot)
                remaining = [value for value in dates if value > session['as_of']]
                if not remaining:
                    raise ValueError('end of frozen replay data')
                next_day, phase = remaining[0], 'open'
            return self.store.put('session', {**session,'as_of':next_day,'phase':phase}, object_id=session_id, db=db)

    def checkpoint(self, session_id):
        # One write transaction captures mutually consistent revision references.
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            session = self.store.get('session', session_id, db=db)
            refs = {}
            for kind in ('pool','account'):
                rows = db.execute('SELECT o.id,o.revision,o.payload FROM objects o JOIN pointers p USING(kind,id,revision) WHERE kind=?', (kind,)).fetchall()
                import json
                refs[kind] = [{'id':row[0],'revision':row[1]} for row in rows if json.loads(row[2]).get('session_id') == session_id]
            return self.store.put('checkpoint', {'session_id':session_id,'session_revision':session['revision'],'refs':refs}, db=db)

    def restore(self, checkpoint_id):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            checkpoint = self.store.get('checkpoint', checkpoint_id, db=db)
            source = self.store.get('session', checkpoint['session_id'], checkpoint['session_revision'], db=db)
            session = self.store.put('session', {**source,'branch_of':source['id'],'checkpoint_id':checkpoint_id}, db=db)
            for kind, refs in checkpoint['refs'].items():
                for ref in refs:
                    item = self.store.get(kind, ref['id'], ref['revision'], db=db)
                    self.store.put(kind, {**item,'session_id':session['id'],'branch_of':item['id']}, db=db)
            return session
