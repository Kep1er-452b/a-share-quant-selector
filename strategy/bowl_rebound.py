"""
碗口反弹策略 - 通达信公式 Python 实现

指标定义：
1. 知行短期趋势线 = EMA(EMA(CLOSE,10),10)
   - 对收盘价先做一次10日EMA，再做一次10日EMA

2. 知行多空线 = (MA(CLOSE,5) + MA(CLOSE,10) + MA(CLOSE,20) + MA(CLOSE,30)) / 4
   - 5日、10日、20日、30日均线平均值

选股条件：
3. 趋势线在上 = 知行短期趋势线 > 知行多空线
   - 短期趋势在多空线上方，表示上升趋势

4. 回落碗中 = CLOSE >= 知行多空线 AND CLOSE <= 知行短期趋势线
   - 价格回落至碗中（多空线和短期趋势线之间）

5. 回落短期趋势 = CLOSE >= 知行短期趋势线*0.98 AND CLOSE <= 知行短期趋势线*1.02
   - 价格在短期趋势线附近±2%范围内

6. KDJ计算(9,3,3): RSV->K->D->J
   - RSV = (CLOSE - LLV(LOW,9)) / (HHV(HIGH,9) - LLV(LOW,9)) * 100
   - K = SMA(RSV,3,1)
   - D = SMA(K,3,1)
   - J = 3*K - 2*D

7. 关键K线 = V>=REF(V,1)*N AND C>O AND 流通市值>CAP
   - 成交量是前一天的N倍以上 AND 阳线 AND 流通市值达标

8. 异动 = EXIST(关键K线, M)
   - 在M天内存在关键K线

9. 选股信号 = 异动 AND 趋势线在上 AND (回落碗中 OR 回落短期趋势) AND J<=J_VAL
   - 同时满足以上所有条件
"""
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategy.base_strategy import BaseStrategy
from utils.technical import (
    MA, EMA, LLV, HHV, REF, EXIST,
    KDJ, calculate_zhixing_trend
)


class BowlReboundStrategy(BaseStrategy):
    """碗口反弹策略"""
    
    def __init__(self, params=None):
        # 默认参数
        default_params = {
            'N': 4,              # 成交量倍数
            'M': 15,             # 回溯天数
            'CAP': 4000000000,   # 流通市值>40亿
            'J_VAL': 30,         # J值上限
            'M1': 14,            # MA周期1 (多空线)
            'M2': 28,            # MA周期2 (多空线)
            'M3': 57,            # MA周期3 (多空线)
            'M4': 114            # MA周期4 (多空线)
        }
        
        # 合并用户参数
        if params:
            default_params.update(params)
        
        super().__init__("碗口反弹策略", default_params)
    
    def calculate_indicators(self, df) -> pd.DataFrame:
        """
        计算碗口反弹策略所需的所有指标
        """
        result = df.copy()
        
        # 1. 知行趋势线 (使用可配置的MA周期参数)
        trend_df = calculate_zhixing_trend(
            result, 
            m1=self.params['M1'],
            m2=self.params['M2'],
            m3=self.params['M3'],
            m4=self.params['M4']
        )
        result['short_term_trend'] = trend_df['short_term_trend']
        result['bull_bear_line'] = trend_df['bull_bear_line']
        
        # 2. 趋势线在上
        result['trend_above'] = result['short_term_trend'] > result['bull_bear_line']
        
        # 3. 回落碗中
        result['fall_in_bowl'] = ((result['close'] >= result['bull_bear_line']) & 
                                   (result['close'] <= result['short_term_trend']))
        
        # 4. 回落短期趋势 (在趋势线附近±2%)
        result['near_short_trend'] = (
            (result['close'] >= result['short_term_trend'] * 0.98) & 
            (result['close'] <= result['short_term_trend'] * 1.02)
        )
        
        # 5. KDJ指标
        kdj_df = KDJ(result, n=9, m1=3, m2=3)
        result['K'] = kdj_df['K']
        result['D'] = kdj_df['D']
        result['J'] = kdj_df['J']
        
        # 6. 关键K线条件
        # V >= REF(V,1) * N (成交量是前一天的N倍)
        result['vol_ratio'] = result['volume'] / REF(result['volume'], 1)
        result['vol_surge'] = result['vol_ratio'] >= self.params['N']
        
        # C > O (收盘价>开盘价，阳线)
        result['positive_candle'] = result['close'] > result['open']
        
        # 流通市值 > CAP
        result['market_cap_ok'] = result['market_cap'] > self.params['CAP']
        
        # 关键K线 = 放量 AND 阳线 AND 市值达标
        result['key_candle'] = (
            result['vol_surge'] & 
            result['positive_candle'] & 
            result['market_cap_ok']
        )
        
        # 7. 异动 = EXIST(关键K线, M)
        result['abnormal'] = EXIST(result['key_candle'], self.params['M'])
        
        # 8. J值条件
        result['j_low'] = result['J'] <= self.params['J_VAL']
        
        # 9. 选股信号
        result['signal'] = (
            result['abnormal'] & 
            result['trend_above'] & 
            (result['fall_in_bowl'] | result['near_short_trend']) & 
            result['j_low']
        )
        
        return result
    
    def select_stocks(self, df, stock_name='') -> list:
        """
        选股逻辑 - 基于最新一天的数据进行筛选
        只有最新一天满足所有条件时，才返回选股信号
        """
        if df.empty:
            return []
        
        # 过滤退市/异常股票
        if stock_name:
            invalid_keywords = ['退', '未知', '退市', '已退']
            if any(kw in stock_name for kw in invalid_keywords):
                return []
        
        # 获取最新一天的数据
        latest = df.iloc[0]
        latest_date = latest['date']
        
        # 检查最新一天是否有有效交易
        if latest['volume'] <= 0 or pd.isna(latest['close']):
            return []
        
        # 过滤数据异常的股票（J值极端异常，可能是退市股票）
        recent_df = df.head(30)
        if recent_df['J'].abs().mean() > 80:
            return []
        
        # ========== 核心条件检查：最新一天必须满足所有选股条件 ==========
        
        # 1. 趋势线在上
        if not latest['trend_above']:
            return []
        
        # 2. 回落碗中 或 回落短期趋势
        if not (latest['fall_in_bowl'] or latest['near_short_trend']):
            return []
        
        # 3. J值条件
        if not latest['j_low']:
            return []
        
        # 4. 异动条件：在M天内存在关键K线（以最新一天为基准向前回溯）
        # 在回溯期(M天内)查找关键K线
        lookback_df = df.head(self.params['M'])
        key_candles = lookback_df[
            (lookback_df['key_candle'] == True) & 
            (lookback_df['close'] > lookback_df['open'])  # 确保是阳线
        ]
        
        if key_candles.empty:
            return []
        
        # ========== 所有条件满足，构建选股信号 ==========
        
        signals = []
        
        # 买入理由
        reasons = []
        if latest['fall_in_bowl']:
            reasons.append('回落碗中')
        if latest['near_short_trend']:
            reasons.append('回落短期趋势线')
        
        # 最近的关键K线
        latest_key = key_candles.iloc[0]
        
        signal_info = {
            'date': latest_date,                    # 当前日期（最新）
            'close': round(latest['close'], 2),     # 当前最新价格
            'J': round(latest['J'], 2),             # 当前J值
            'volume_ratio': round(latest['vol_ratio'], 2) if not pd.isna(latest['vol_ratio']) else 1.0,
            'market_cap': round(latest['market_cap'] / 1e8, 2),
            'reasons': reasons,
            'key_candle_date': latest_key['date'],  # 最近的关键K线日期
        }
        
        signals.append(signal_info)
        
        return signals
