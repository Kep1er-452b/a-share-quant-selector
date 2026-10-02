"""Event-ledger A-share paper account. Daily-bar fills are explicit assumptions."""
from __future__ import annotations

import math
import uuid
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd

from research.service import day, text
from utils.market_watchlist import canonical_equity_symbol


RULE_VERSION = 'a_share_daily_open_v1_20261002'


def money(value):
    return float(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def decimal(value):
    return Decimal(str(value))


def lot_rule(symbol):
    code = symbol.split('.')[0]
    if code.startswith('688'):
        return 200, 1
    if symbol.endswith('.BJ'):
        return 100, 1
    return 100, 100


def executable_quantity(symbol, requested, maximum, *, sell_all=False):
    quantity = min(int(requested), int(maximum))
    minimum, step = lot_rule(symbol)
    if sell_all and quantity == requested:
        return quantity
    if quantity < minimum:
        return 0
    return minimum + (quantity - minimum) // step * step


class SimulationService:
    def __init__(self, store):
        self.store = store

    def _context(self, account, db=None):
        session = self.store.get('session', account['session_id'], db=db)
        snapshot = self.store.get('snapshot', session['snapshot_id'], db=db)
        if snapshot['price_view'] != 'raw_with_factors' or snapshot['market'] != 'a_share':
            raise ValueError('paper trading requires frozen unadjusted A-share prices and factors')
        if session['as_of'] < '2023-08-28':
            raise ValueError('this fee/rule profile supports dates from 2023-08-28; import an audited older profile before earlier simulations')
        return session, snapshot

    def create(self, session_id, cash, assumptions=None):
        cash = float(cash)
        if not math.isfinite(cash) or not 100 <= cash <= 1e9:
            raise ValueError('initial cash must be between 100 and 1e9 yuan')
        assumptions = assumptions or {}
        defaults = {'commission_rate':0.0003, 'minimum_commission':5., 'sell_stamp_rate':0.0005,
                    'transfer_rate':0.00001, 'slippage_bps':5., 'previous_volume_capacity':0.01}
        if set(assumptions) - set(defaults):
            raise ValueError('unsupported fill assumption')
        settings = {**defaults, **assumptions}
        for key, value in settings.items():
            value = float(value)
            maximum = 100 if key in ('minimum_commission','slippage_bps') else 0.1
            if not math.isfinite(value) or value < 0 or value > maximum:
                raise ValueError('invalid fill assumption')
            settings[key] = value
        if settings['previous_volume_capacity'] <= 0:
            raise ValueError('capacity must be positive')
        account = {'session_id':session_id, 'initial_cash':money(cash), 'cash':money(cash), 'positions':{},
            'orders':[], 'events':[], 'nav':[], 'settled_dates':[], 'receivables':[], 'assumptions':settings,
            'rule_version':RULE_VERSION, 'fill_model':'next_open_with_previous_day_capacity',
            'limitations':['日线无盘口，涨跌停开盘保守不成交','成交容量使用前一交易日成交量，不能证明开盘可成交','佣金和滑点为可调整的模拟假设']}
        self._context(account)
        self._event(account, 'deposit', cash_delta=account['cash'])
        return self.store.put('account', account)

    def _event(self, account, kind, **values):
        if len(account['events']) >= 10000:
            raise ValueError('account event budget exceeded; branch or archive this account')
        account['events'].append({'event_id':uuid.uuid4().hex, 'kind':kind, 'cash_delta':0., 'quantity_delta':0, 'basis_delta':0., **values})

    def _audit(self, account):
        cash = money(sum(event.get('cash_delta',0) for event in account['events']))
        if cash < -0.001 or abs(cash - account['cash']) > 0.011:
            raise RuntimeError('paper cash ledger invariant failed')
        for symbol, position in account['positions'].items():
            quantity = sum(event.get('quantity_delta',0) for event in account['events'] if event.get('symbol') == symbol)
            basis = money(sum(event.get('basis_delta',0) for event in account['events'] if event.get('symbol') == symbol))
            if quantity != position['quantity'] or quantity < 0 or abs(basis - position['basis']) > 0.011 or sum(lot['quantity'] for lot in position['lots']) != quantity:
                raise RuntimeError('paper position ledger invariant failed')

    def available_cash(self, account):
        reserved = sum(order.get('reserved',0) for order in account['orders'] if order['status'] in ('pending','partial'))
        return money(account['cash'] - reserved)

    def submit(self, account_id, symbol, side, quantity, *, request_id, run_id=None):
        from research.store import identity
        request_id = identity(request_id)
        symbol = canonical_equity_symbol('a_share', symbol)
        if side not in ('buy','sell') or isinstance(quantity, bool) or int(quantity) != quantity or not 1 <= int(quantity) <= 1_000_000:
            raise ValueError('invalid side or quantity')
        quantity = int(quantity)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            account = self.store.get('account', account_id, db=db)
            session, snapshot = self._context(account, db=db)
            duplicate = next((order for order in account['orders'] if order['request_id'] == request_id), None)
            if duplicate:
                if (duplicate['symbol'],duplicate['side'],duplicate['quantity'],duplicate.get('run_id')) != (symbol,side,quantity,run_id):
                    raise ValueError('request_id reused with another order')
                return account
            if symbol not in snapshot['instruments']:
                raise ValueError('security outside frozen simulation scope')
            entry=snapshot['instruments'][symbol]
            if entry.get('list_date') and session['as_of']<entry['list_date'] or entry.get('delist_date') and session['as_of']>=entry['delist_date']:
                raise ValueError('security outside historical listing interval')
            if run_id:
                run = self.store.get('run', run_id, db=db)
                signal_date = run['context'].get('as_of') or '9999-12-31'
                if (run['snapshot_id'] != snapshot['id'] or signal_date > session['as_of']
                        or signal_date == session['as_of'] and session['phase']=='open' and run['context'].get('phase')=='close'):
                    raise ValueError('order signal is from another snapshot or future date')
            position = account['positions'].get(symbol, {'quantity':0,'lots':[]})
            minimum, step = lot_rule(symbol)
            if quantity < minimum or (quantity-minimum)%step:
                if not (side == 'sell' and quantity == position['quantity']):
                    raise ValueError('quantity violates board lot or residual-sale rule')
            reserved = 0.
            if side == 'sell':
                sellable = sum(lot['quantity'] for lot in position['lots'] if lot['date'] < session['as_of'] or lot.get('delivered'))
                pending = sum(order['remaining'] for order in account['orders'] if order['symbol']==symbol and order['side']=='sell' and order['status'] in ('pending','partial'))
                if quantity > sellable-pending:
                    raise ValueError('insufficient sellable shares (T+1 or reserved orders)')
            else:
                frame = self.store.read_frame(snapshot['instruments'][symbol]['hash'])
                frame = frame[frame.date.lt(pd.Timestamp(session['as_of'])) if session['phase']=='open' else frame.date.le(pd.Timestamp(session['as_of']))]
                if frame.empty:
                    raise ValueError('no visible reference price')
                reference = float(frame.sort_values('date').iloc[-1].close)
                gross = decimal(reference)*quantity*(1+decimal(account['assumptions']['slippage_bps'])/10000)
                reserved = money(gross + max(decimal(account['assumptions']['minimum_commission']),gross*decimal(account['assumptions']['commission_rate']))+gross*decimal(account['assumptions']['transfer_rate']))
                if reserved > self.available_cash(account):
                    raise ValueError('insufficient available cash')
            order = {'id':uuid.uuid4().hex, 'request_id':request_id, 'symbol':symbol,'side':side,'quantity':quantity,
                'remaining':quantity,'status':'pending','submitted_as_of':session['as_of'],'submitted_phase':session['phase'],
                'earliest_fill':'next_trading_day_open','run_id':run_id,'reserved':reserved,'gross_filled':0.,'commission_paid':0.,'fills':[]}
            account['orders'].append(order)
            self._event(account,'order', date=session['as_of'], order_id=order['id'])
            self._audit(account)
            return self.store.put('account', account, object_id=account_id, db=db)

    def cancel(self, account_id, order_id):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            account = self.store.get('account', account_id, db=db)
            order = next((item for item in account['orders'] if item['id']==order_id), None)
            if not order:
                raise KeyError('order')
            if order['status'] in ('pending','partial'):
                order.update(status='cancelled',reserved=0.)
                self._event(account,'cancel', order_id=order_id)
            return self.store.put('account', account, object_id=account_id, db=db)

    def _corporate_actions(self, account, snapshot, date):
        for symbol, entry in snapshot['instruments'].items():
            for index, action in enumerate(entry.get('corporate_actions', [])):
                key = f'{symbol}/{index}'
                if action['ex_date'] <= date and not any(event.get('action_key')==key for event in account['events']):
                    entitled = sum(event.get('quantity_delta',0) for event in account['events'] if event.get('symbol')==symbol and event.get('date','9999-12-31')<=action['record_date'])
                    if entitled > 0:
                        cash = money(entitled * decimal(action.get('cash_per_share_net',0)))
                        shares = int(entitled * decimal(action.get('bonus_ratio',0)))
                        account['receivables'].append({'action_key':key,'symbol':symbol,'cash':cash,'shares':shares,
                            'pay_date':action.get('pay_date',date),'delivery_date':action.get('delivery_date',date)})
                    self._event(account,'entitlement', date=action['ex_date'], action_key=key, symbol=symbol)
        for item in account['receivables']:
            if item['cash'] and item['pay_date'] <= date:
                account['cash'] = money(account['cash']+item['cash'])
                self._event(account,'dividend', date=date, cash_delta=item['cash'],symbol=item['symbol'], action_key=item['action_key'])
                item['cash']=0.
            if item['shares'] and item['delivery_date'] <= date:
                position = account['positions'].setdefault(item['symbol'], {'quantity':0,'basis':0.,'lots':[]})
                position['quantity'] += item['shares']
                position['lots'].append({'date':date,'quantity':item['shares'],'delivered':True})
                self._event(account,'bonus', date=date, symbol=item['symbol'],quantity_delta=item['shares'],action_key=item['action_key'])
                item['shares']=0

    def settle_open(self, account_id):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            account = self.store.get('account', account_id, db=db)
            session, snapshot = self._context(account, db=db)
            date = session['as_of']
            if session['phase'] != 'open':
                raise ValueError('fills are allowed only at replay open')
            if date in account['settled_dates']:
                return account
            # Applying actions/fills on a copy and committing once avoids half ledgers.
            self._corporate_actions(account, snapshot, date)
            capacity_used = {}
            for order in account['orders']:
                if order['status'] not in ('pending','partial') or order['submitted_as_of'] >= date:
                    continue
                symbol, entry = order['symbol'], snapshot['instruments'][order['symbol']]
                frame = self.store.read_frame(entry['hash']).sort_values('date')
                current = frame[frame.date.eq(pd.Timestamp(date))]
                previous = frame[frame.date.lt(pd.Timestamp(date))]
                if current.empty or previous.empty:
                    order['waiting_reason']='suspension_or_no_prior_bar'
                    continue
                row, prior = current.iloc[0], previous.iloc[-1]
                position = account['positions'].setdefault(symbol, {'quantity':0,'basis':0.,'lots':[]})
                if position['quantity'] and float(row.adj_factor) != float(prior.adj_factor) and not any(action['ex_date']==date for action in entry.get('corporate_actions',[])):
                    raise ValueError('corporate-action data missing; ledger and performance publication stopped')
                if not {'up_limit','down_limit'}.issubset(frame.columns) or pd.isna(row.up_limit) or pd.isna(row.down_limit):
                    order['waiting_reason']='price_limit_data_missing'
                    continue
                if order['side']=='buy' and row.up_limit>0 and row.open>=row.up_limit or order['side']=='sell' and row.down_limit>0 and row.open<=row.down_limit:
                    order['waiting_reason']='opening_price_at_limit'
                    continue
                capacity = int(float(prior.volume)*(100 if entry.get('volume_unit')=='hands' else 1)*account['assumptions']['previous_volume_capacity'])
                maximum = max(capacity-capacity_used.get(symbol,0),0)
                if order['side']=='sell':
                    maximum = min(maximum,sum(lot['quantity'] for lot in position['lots'] if lot['date']<date or lot.get('delivered')))
                qty = executable_quantity(symbol,order['remaining'],maximum,sell_all=order['side']=='sell' and order['remaining']==position['quantity'])
                price = money(decimal(row.open)*(1+(1 if order['side']=='buy' else -1)*decimal(account['assumptions']['slippage_bps'])/10000))
                if price <= 0:
                    order['waiting_reason']='invalid_slipped_price'
                    continue
                if (row.up_limit>0 and price>row.up_limit) or (row.down_limit>0 and price<row.down_limit):
                    order['waiting_reason']='slippage_crosses_limit'
                    continue
                def fees(quantity):
                    gross = money(quantity*decimal(price))
                    commission_total = max(decimal(account['assumptions']['minimum_commission']),(decimal(order['gross_filled'])+decimal(gross))*decimal(account['assumptions']['commission_rate']))
                    commission = money(commission_total-decimal(order['commission_paid']))
                    transfer = money(decimal(gross)*decimal(account['assumptions']['transfer_rate']))
                    stamp = money(decimal(gross)*decimal(account['assumptions']['sell_stamp_rate'])) if order['side']=='sell' else 0.
                    return gross,commission,transfer,stamp
                minimum, step = lot_rule(symbol)
                if order['side']=='buy':
                    cash_for_order = account['cash'] - sum(other.get('reserved',0) for other in account['orders'] if other is not order and other['status'] in ('pending','partial'))
                    approximate = max(int((cash_for_order-account['assumptions']['minimum_commission'])/(price*(1+account['assumptions']['commission_rate']+account['assumptions']['transfer_rate']))),0)
                    qty = executable_quantity(symbol,qty,min(qty,approximate))
                    while qty and sum(fees(qty)) > cash_for_order + .001:
                        qty = executable_quantity(symbol,qty,min(qty,qty-step))
                if not qty:
                    order['waiting_reason']='capacity_or_cash_or_t1'
                    continue
                gross,commission,transfer,stamp = fees(qty)
                charge = money(commission+transfer+stamp)
                if order['side']=='sell' and account['cash']+gross-charge<0:
                    order['waiting_reason']='fees_exceed_available_cash'
                    continue
                if order['side']=='buy':
                    cash_delta,basis_delta,quantity_delta = -money(gross+charge), money(gross+charge),qty
                    position['lots'].append({'date':date,'quantity':qty})
                else:
                    basis_delta = -money(decimal(position['basis'])*qty/position['quantity'])
                    cash_delta,quantity_delta = money(gross-charge),-qty
                    remaining = qty
                    for lot in position['lots']:
                        if lot['date']<date or lot.get('delivered'):
                            used = min(lot['quantity'],remaining)
                            lot['quantity']-=used
                            remaining-=used
                    position['lots']=[lot for lot in position['lots'] if lot['quantity']]
                account['cash']=money(account['cash']+cash_delta)
                position['quantity']+=quantity_delta
                position['basis']=money(position['basis']+basis_delta)
                order['remaining']-=qty
                order['status']='filled' if not order['remaining'] else 'partial'
                order['reserved']=money(order['reserved']*order['remaining']/(order['remaining']+qty)) if order['side']=='buy' else 0.
                other_reserves=sum(other.get('reserved',0) for other in account['orders'] if other is not order and other['status'] in ('pending','partial'))
                order['reserved']=min(order['reserved'],max(money(account['cash']-other_reserves),0.))
                order['gross_filled']=money(order['gross_filled']+gross)
                order['commission_paid']=money(order['commission_paid']+commission)
                fill={'date':date,'phase':'open','quantity':qty,'price':price,'commission':commission,'transfer':transfer,'stamp':stamp,'order_id':order['id'],'rule_version':RULE_VERSION}
                order['fills'].append(fill)
                self._event(account,'fill',**fill,symbol=symbol,cash_delta=cash_delta,quantity_delta=quantity_delta,basis_delta=basis_delta,
                            realized_pnl=money(cash_delta+basis_delta) if order['side']=='sell' else None)
                order.pop('waiting_reason',None)
                capacity_used[symbol]=capacity_used.get(symbol,0)+qty
            account['settled_dates'].append(date)
            self._audit(account)
            return self.store.put('account',account,object_id=account_id,db=db)

    def report(self, account_id):
        account = self.store.get('account',account_id)
        self._audit(account)
        session,snapshot = self._context(account)
        value,positions,issues=0.,[],[]
        # A user may advance the clock without settling the open. Publishing
        # NAV before due entitlements/payments are booked would omit returns.
        for symbol, entry in snapshot['instruments'].items():
            for index, action in enumerate(entry.get('corporate_actions', [])):
                key = f'{symbol}/{index}'
                entitled = sum(event.get('quantity_delta',0) for event in account['events']
                    if event.get('symbol')==symbol and event.get('date','9999-12-31')<=action['record_date'])
                if (action['ex_date']<=session['as_of'] and entitled>0
                        and not any(event.get('action_key')==key for event in account['events'])):
                    issues.append(f'{symbol}: corporate-action settlement pending')
        if any((item['cash'] and item['pay_date']<=session['as_of'])
               or (item['shares'] and item['delivery_date']<=session['as_of']) for item in account['receivables']):
            issues.append('corporate-action payment/delivery settlement pending')
        for symbol,position in account['positions'].items():
            if not position['quantity']:
                continue
            entry=snapshot['instruments'][symbol]
            if entry.get('delist_date') and session['as_of']>=entry['delist_date']:
                issues.append(f'{symbol}: delisting settlement evidence missing')
            frame=self.store.read_frame(entry['hash']).sort_values('date')
            visible=frame[frame.date.le(pd.Timestamp(session['as_of']))]
            if visible.empty:
                issues.append(f'{symbol}: price unavailable')
                continue
            latest=visible.iloc[-1]
            price=float(latest.open if session['phase']=='open' and latest.date==pd.Timestamp(session['as_of']) else latest.close)
            bought=[event['date'] for event in account['events'] if event.get('symbol')==symbol and event['kind']=='fill']
            first=min(bought) if bought else session['as_of']
            changes=frame[frame.date.ge(pd.Timestamp(first)) & frame.date.le(pd.Timestamp(session['as_of']))].copy()
            factor_changes=changes[changes.adj_factor.ne(changes.adj_factor.shift(1))].iloc[1:]
            if any(row.date.strftime('%Y-%m-%d') not in {action['ex_date'] for action in entry.get('corporate_actions',[])} for _,row in factor_changes.iterrows()):
                issues.append(f'{symbol}: corporate action evidence missing')
            amount=money(decimal(price)*position['quantity'])
            value+=amount
            positions.append({'symbol':symbol,**position,'price':price,'market_value':amount,
                'sellable':sum(lot['quantity'] for lot in position['lots'] if lot['date']<session['as_of'] or lot.get('delivered'))})
        receivable_cash=sum(item['cash'] for item in account['receivables'])
        for item in account['receivables']:
            if item['shares']:
                frame=self.store.read_frame(snapshot['instruments'][item['symbol']]['hash'])
                visible=frame[frame.date.le(pd.Timestamp(session['as_of']))].sort_values('date')
                if not visible.empty:
                    latest=visible.iloc[-1]
                    value+=money(item['shares']*decimal(latest.open if session['phase']=='open' and latest.date==pd.Timestamp(session['as_of']) else latest.close))
        equity=money(account['cash']+value+receivable_cash) if not issues else None
        fees={key:money(sum(event.get(key,0) for event in account['events'] if event['kind']=='fill')) for key in ('commission','transfer','stamp')}
        curve=account['nav']
        high=account['initial_cash']
        drawdown=0.
        for point in curve+([{'equity':equity}] if equity is not None else []):
            high=max(high,point['equity'])
            drawdown=min(drawdown,point['equity']/high-1)
        return {'account':account,'session':session,'positions':positions,'equity':equity,'available_cash':self.available_cash(account),
            'return_pct':(equity/account['initial_cash']-1)*100 if equity is not None else None,
            'max_drawdown_pct':drawdown*100 if equity is not None else None,'fees':fees,'issues':issues}

    def mark_close(self, account_id):
        report=self.report(account_id)
        if report['session']['phase']!='close' or report['equity'] is None:
            raise ValueError('valid close prices/actions required to publish equity')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            account=self.store.get('account',account_id,db=db)
            if account['revision']!=report['account']['revision']:
                raise ValueError('account changed; retry valuation')
            session=self.store.get('session',account['session_id'],db=db)
            if session['revision']!=report['session']['revision']:
                raise ValueError('replay clock changed; retry valuation')
            date=report['session']['as_of']
            if not any(point['date']==date for point in account['nav']):
                account['nav'].append({'date':date,'equity':report['equity']})
            return self.store.put('account',account,object_id=account_id,db=db)
