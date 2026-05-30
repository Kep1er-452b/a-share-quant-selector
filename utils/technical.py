"""
技术指标计算模块 - 通达信公式函数实现
"""
import pandas as pd
import numpy as np


def normalize_price_frame(df: pd.DataFrame, *, descending: bool = True) -> pd.DataFrame:
    """
    Return a date-sorted OHLCV frame.

    Most project strategy formulas expect local CSV order: newest row first.
    This helper makes that invariant explicit at module boundaries while keeping
    the caller's index shape stable after sorting.
    """
    if df is None or df.empty or 'date' not in df.columns:
        return df.copy() if df is not None else pd.DataFrame()

    result = df.copy()
    result['date'] = pd.to_datetime(result['date'], errors='coerce')
    result = result.dropna(subset=['date'])
    if result.empty:
        return result
    needs_sort = (
        not result['date'].is_monotonic_decreasing
        if descending
        else not result['date'].is_monotonic_increasing
    )
    if needs_sort:
        result = result.sort_values('date', ascending=not descending).reset_index(drop=True)
    return result


def MA(series, n):
    """
    简单移动平均 - 正确处理倒序排列的数据
    
    对于倒序数据，MA(n)应该取当前及之后n-1个数据的平均值
    实现方式：反转数据 -> 计算rolling -> 反转回来
    """
    # 反转数据，使数据按时间正序排列
    reversed_series = series.iloc[::-1]
    
    # 在正序数据上计算MA（向前看n个值）
    ma_reversed = reversed_series.rolling(window=n, min_periods=1).mean()
    
    # 反转回来，恢复倒序
    return ma_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def EMA(series, n):
    """
    指数移动平均 - 正确处理倒序排列的数据
    """
    reversed_series = series.iloc[::-1]
    ema_reversed = reversed_series.ewm(span=n, adjust=False, min_periods=1).mean()
    return ema_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def LLV(series, n):
    """
    N周期最低值 - 正确处理倒序排列的数据
    """
    reversed_series = series.iloc[::-1]
    llv_reversed = reversed_series.rolling(window=n, min_periods=1).min()
    return llv_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def HHV(series, n):
    """
    N周期最高值 - 正确处理倒序排列的数据
    """
    reversed_series = series.iloc[::-1]
    hhv_reversed = reversed_series.rolling(window=n, min_periods=1).max()
    return hhv_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def SMA(X, n, m):
    """
    移动平均 - 通达信风格（向量化版本）
    SMA(X,N,M): X的N日移动平均, M为权重
    公式: Y = (X*M + Y'*(N-M)) / N
    """
    if len(X) == 0:
        return pd.Series(index=X.index, dtype=float)

    # 转换为 numpy 数组进行计算，比 pandas .iloc 快 50x+
    arr = X.iloc[::-1].reset_index(drop=True).values.astype(float)
    length = len(arr)
    result = np.empty(length)
    result[0] = arr[0]
    
    # numpy 数组访问比 pandas .iloc 快得多
    for i in range(1, length):
        result[i] = (arr[i] * m + result[i - 1] * (n - m)) / n

    return pd.Series(result[::-1], index=X.index)


def REF(series, n):
    """
    向前引用N周期 - 正确处理倒序排列的数据
    
    对于倒序数据（最新在前），REF(series, 1)应该获取"前一天"的数据
    实现方式：反转数据 -> shift -> 反转回来
    """
    reversed_series = series.iloc[::-1]
    ref_reversed = reversed_series.shift(n)
    return ref_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def EXIST(cond, n):
    """
    N周期内是否存在满足COND的情况 - 正确处理倒序排列的数据
    """
    reversed_cond = cond.iloc[::-1]
    exist_reversed = reversed_cond.rolling(window=n, min_periods=1).max().astype(bool)
    return exist_reversed.iloc[::-1].reset_index(drop=True).set_axis(cond.index)


def COUNT(cond, n):
    """
    N周期内满足条件的次数 - 正确处理倒序排列的数据
    """
    reversed_cond = cond.astype(int).iloc[::-1]
    count_reversed = reversed_cond.rolling(window=n, min_periods=1).sum()
    return count_reversed.iloc[::-1].reset_index(drop=True).set_axis(cond.index)


def SUM(series, n):
    """
    N周期求和 - 正确处理倒序排列的数据
    调用方应保证传入的 series 已是数值类型。
    """
    reversed_series = series.iloc[::-1]
    sum_reversed = reversed_series.rolling(window=n, min_periods=1).sum()
    return sum_reversed.iloc[::-1].reset_index(drop=True).set_axis(series.index)


def FINANCE(df, field_code):
    """
    财务数据获取
    39: 总市值（注意：原通达信39是流通市值，本项目使用总市值）
    """
    if field_code == 39:
        return df.get('market_cap', pd.Series([0] * len(df), index=df.index))
    return pd.Series([0] * len(df), index=df.index)


def KDJ(df, n=9, m1=3, m2=3):
    """
    KDJ指标计算 - 标准实现（向量化版本）
    通达信公式：
    RSV = (CLOSE - LLV(LOW,N)) / (HHV(HIGH,N) - LLV(LOW,N)) * 100
    K = SMA(RSV,M1,1)
    D = SMA(K,M2,1)
    J = 3*K - 2*D
    
    注意：数据可能是倒序（最新在前）或正序，需要自动检测并处理
    """
    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    if not df.empty and not (df['date'].is_monotonic_decreasing or df['date'].is_monotonic_increasing):
        df = df.sort_values('date', ascending=False).reset_index(drop=True)
    if df.empty:
        return pd.DataFrame({'K': [], 'D': [], 'J': []}, index=df.index)

    # 检测数据顺序
    is_descending = df['date'].iloc[0] > df['date'].iloc[-1]

    # 统一转换为正序计算（从早到晚）
    if is_descending:
        df_calc = df.iloc[::-1].copy().reset_index(drop=True)
    else:
        df_calc = df.copy().reset_index(drop=True)

    length = len(df_calc)
    
    # 转换为 numpy 数组进行向量化计算
    close_arr = df_calc['close'].values.astype(float)
    low_arr = df_calc['low'].values.astype(float)
    high_arr = df_calc['high'].values.astype(float)
    
    # 计算RSV - 向量化
    low_min = pd.Series(low_arr).rolling(window=n, min_periods=1).min().values
    high_max = pd.Series(high_arr).rolling(window=n, min_periods=1).max().values
    range_val = high_max - low_min
    
    # RSV计算，前n-1个周期不足时用50填充
    rsv = np.full(length, 50.0)
    valid_mask = (np.arange(length) >= n - 1) & (range_val != 0)
    rsv[valid_mask] = (close_arr[valid_mask] - low_min[valid_mask]) / range_val[valid_mask] * 100

    # SMA计算 - 通达信风格（使用 numpy 数组，比 pandas .iloc 快 50x+）
    # K = SMA(RSV, M1, 1): K = (RSV*1 + K'*(M1-1)) / M1
    k = np.full(length, 50.0)
    d = np.full(length, 50.0)

    # 递归计算 - numpy 数组访问比 pandas .iloc 快得多
    for i in range(1, length):
        k[i] = (rsv[i] * 1 + k[i - 1] * (m1 - 1)) / m1
        d[i] = (k[i] * 1 + d[i - 1] * (m2 - 1)) / m2

    # 计算J值
    j = 3 * k - 2 * d

    # 构建结果
    result = pd.DataFrame({
        'K': k,
        'D': d,
        'J': j
    })

    if is_descending:
        result = result.iloc[::-1].reset_index(drop=True)

    result.index = df.index
    return result


def prepare_selection_features(df, include_standard_trend=True):
    """
    预计算多策略共享的中间列，避免单只股票重复计算。
    """
    result = normalize_price_frame(df, descending=True)

    if 'ref_close_1' not in result.columns:
        result['ref_close_1'] = REF(result['close'], 1)
    if 'ref_vol_1' not in result.columns:
        result['ref_vol_1'] = REF(result['volume'], 1)

    if 'REAL_YANG' not in result.columns:
        result['REAL_YANG'] = (result['close'] > result['open']) & ~(result['close'] < result['ref_close_1'])
    if 'REAL_YIN' not in result.columns:
        result['REAL_YIN'] = (result['close'] < result['open']) & ~(result['close'] > result['ref_close_1'])

    if not {'K', 'D', 'J'}.issubset(result.columns):
        kdj_df = KDJ(result, n=9, m1=3, m2=3)
        result['K'] = kdj_df['K']
        result['D'] = kdj_df['D']
        result['J'] = kdj_df['J']

    if include_standard_trend and not {'short_term_trend', 'bull_bear_line'}.issubset(result.columns):
        trend_df = calculate_zhixing_trend(result, m1=14, m2=28, m3=57, m4=114)
        result['short_term_trend'] = trend_df['short_term_trend']
        result['bull_bear_line'] = trend_df['bull_bear_line']

    return result


def calculate_zhixing_trend(df, m1=14, m2=28, m3=57, m4=114):
    """
    计算知行趋势线指标
    
    指标定义:
    - 知行短期趋势线 = EMA(EMA(CLOSE,10),10)
      对收盘价连续做两次10日指数移动平均
    
    - 知行多空线 = (MA(CLOSE,m1) + MA(CLOSE,m2) + MA(CLOSE,m3) + MA(CLOSE,m4)) / 4
      四条均线平均值，默认使用 14, 28, 57, 114
    
    参数:
        m1, m2, m3, m4: 多空线计算用的MA周期，默认14, 28, 57, 114
    """
    df = normalize_price_frame(df, descending=True)
    # 知行短期趋势线 = EMA(EMA(CLOSE,10),10)
    short_term_trend = EMA(EMA(df['close'], 10), 10)
    
    # 知行多空线 = (MA(m1) + MA(m2) + MA(m3) + MA(m4)) / 4
    bull_bear_line = (MA(df['close'], m1) + MA(df['close'], m2) + 
                      MA(df['close'], m3) + MA(df['close'], m4)) / 4
    
    return pd.DataFrame({
        'short_term_trend': short_term_trend,
        'bull_bear_line': bull_bear_line
    }, index=df.index)


def _bars_last_count(cond: pd.Series) -> pd.Series:
    """正序布尔序列的连续成立计数；调用前必须先把行情转为旧到新。"""
    values = cond.fillna(False).astype(bool).tolist()
    counts = []
    current = 0
    for value in values:
        current = current + 1 if value else 0
        counts.append(current)
    return pd.Series(counts, index=cond.index, dtype=int)


def _bars_last(cond: pd.Series) -> pd.Series:
    """正序布尔序列距离上一次成立的周期数；调用前必须先把行情转为旧到新。"""
    values = cond.fillna(False).astype(bool).tolist()
    result = []
    last_index = None
    for index, value in enumerate(values):
        if value:
            last_index = index
            result.append(0)
        elif last_index is None:
            result.append(-1)
        else:
            result.append(index - last_index)
    return pd.Series(result, index=cond.index, dtype=int)


def _backset(cond: pd.Series, counts: pd.Series) -> pd.Series:
    """通达信 BACKSET 的正序近似实现；调用前必须先把行情转为旧到新。"""
    flags = [False] * len(cond)
    cond_values = cond.fillna(False).astype(bool).tolist()
    count_values = pd.to_numeric(counts, errors='coerce').fillna(0).astype(int).tolist()
    for index, value in enumerate(cond_values):
        if not value:
            continue
        count = max(count_values[index], 0)
        start = max(0, index - count + 1)
        for mark_index in range(start, index + 1):
            flags[mark_index] = True
    return pd.Series(flags, index=cond.index, dtype=bool)


def _ref_by_variable_period(series: pd.Series, periods: pd.Series) -> pd.Series:
    """正序序列按每行不同 REF 周期取值。"""
    values = series.reset_index(drop=True)
    period_values = pd.to_numeric(periods, errors='coerce').fillna(0).astype(int).tolist()
    result = []
    for index, period in enumerate(period_values):
        source_index = index - period
        result.append(values.iloc[source_index] if 0 <= source_index < len(values) else np.nan)
    return pd.Series(result, index=series.index, dtype=float)


def calculate_zhixing_main_overlay(df):
    """
    计算个股主图上的知行趋势线、13 序列与大暴力K星标。

    输入行情可为倒序或正序；返回结果保持原始索引顺序。
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    df = normalize_price_frame(df, descending=True)
    is_descending = df['date'].iloc[0] > df['date'].iloc[-1]
    df_calc = df.iloc[::-1].copy().reset_index(drop=True) if is_descending else df.copy().reset_index(drop=True)

    close = pd.to_numeric(df_calc['close'], errors='coerce')
    open_ = pd.to_numeric(df_calc['open'], errors='coerce')
    high = pd.to_numeric(df_calc['high'], errors='coerce')
    low = pd.to_numeric(df_calc['low'], errors='coerce')
    volume = pd.to_numeric(df_calc['volume'], errors='coerce').fillna(0)

    # df_calc 已转为正序，直接用 ewm/rolling 计算，与 calculate_zhixing_trend 公式一致
    short_line = close.ewm(span=10, adjust=False, min_periods=1).mean().ewm(span=10, adjust=False, min_periods=1).mean()
    bull_bear_line = (
        close.rolling(window=14, min_periods=1).mean() +
        close.rolling(window=28, min_periods=1).mean() +
        close.rolling(window=57, min_periods=1).mean() +
        close.rolling(window=114, min_periods=1).mean()
    ) / 4

    ref_close_1 = close.shift(1)
    ref_close_2 = close.shift(2)
    ref_close_4 = close.shift(4)
    ref_low_1 = low.shift(1)
    ref_volume_1 = volume.shift(1)

    up_count = _bars_last_count(close > ref_close_4)
    up_ninth = up_count == 9
    up_dist = _bars_last(up_ninth)
    last_bar = pd.Series(False, index=df_calc.index)
    last_bar.iloc[-1] = True
    up_partial_last = last_bar & up_count.between(5, 8)
    up_mask = _backset(up_ninth, pd.Series([9] * len(df_calc), index=df_calc.index)) | _backset(up_partial_last, up_count)

    down_count = _bars_last_count(close < ref_close_4)
    down_ninth = down_count == 9
    down_dist = _bars_last(down_ninth)
    down_partial_last = last_bar & down_count.between(5, 8)
    down_mask = _backset(down_ninth, pd.Series([9] * len(df_calc), index=df_calc.index)) | _backset(down_partial_last, down_count)

    up_label = pd.Series(np.nan, index=df_calc.index, dtype=float)
    up_label[(up_mask & (up_count > 0) & (up_count < 9))] = up_count[(up_mask & (up_count > 0) & (up_count < 9))]
    up_after = (up_dist >= 0) & (up_dist <= 4)
    up_label[up_after] = 9 + up_dist[up_after]

    down_label = pd.Series(np.nan, index=df_calc.index, dtype=float)
    down_label[(down_mask & (down_count > 0) & (down_count < 9))] = down_count[(down_mask & (down_count > 0) & (down_count < 9))]
    down_after = (down_dist >= 0) & (down_dist <= 4)
    down_label[down_after] = 9 + down_dist[down_after]

    low_min_9 = low.rolling(window=9, min_periods=1).min()
    high_max_9 = high.rolling(window=9, min_periods=1).max()
    rsv_range = (high_max_9 - low_min_9).replace(0, np.nan)
    rsv = ((close - low_min_9) / rsv_range * 100).fillna(50)
    k = pd.Series(index=df_calc.index, dtype=float)
    d = pd.Series(index=df_calc.index, dtype=float)
    k.iloc[0] = 50.0
    d.iloc[0] = 50.0
    for index in range(1, len(df_calc)):
        k.iloc[index] = (rsv.iloc[index] + k.iloc[index - 1] * 2) / 3
        d.iloc[index] = (k.iloc[index] + d.iloc[index - 1] * 2) / 3
    j = 3 * k - 2 * d

    pc = ((close - ref_close_1) / ref_close_1.replace(0, np.nan)).fillna(0)
    ty = (pc > 0) & (close > open_)
    vr = volume >= ref_volume_1.fillna(0) * 1.75
    dt1 = (ref_close_1 <= ref_close_2 * 0.901) & (ref_close_1 == ref_low_1)
    ndt = ~dt1.fillna(False)

    yv = (close > open_) & (volume > ref_volume_1)
    yn = (close < open_) & (volume > ref_volume_1)
    al = yv | yn
    ref_al = al.shift(1).fillna(False).astype(bool)
    ref_yn = yn.shift(1).fillna(False).astype(bool)
    currbarscount = pd.Series(len(df_calc) - np.arange(len(df_calc)), index=df_calc.index)

    def count_true(series, window):
        return series.astype(int).rolling(window=window, min_periods=window).sum().fillna(0)

    conditions = {}
    for window, max_yin in [(10, 3), (9, 3), (8, 2), (7, 2), (6, 2), (5, 1), (4, 1), (3, 1)]:
        conditions[window] = (
            (currbarscount > window) &
            (count_true(ref_al, window) == window) &
            (count_true(ref_yn, window) <= max_yin)
        )

    bl = pd.Series(0, index=df_calc.index, dtype=int)
    for window in [10, 9, 8, 7, 6, 5, 4, 3]:
        bl = bl.mask((bl == 0) & conditions[window], window)

    to = _ref_by_variable_period(open_, bl).where(bl > 0, open_)
    open_low_56 = open_.rolling(window=56, min_periods=1).min()
    open_high_56 = open_.rolling(window=56, min_periods=1).max()
    op = to <= open_low_56 + (open_high_56 - open_low_56) * 0.25
    j_ref_by_bl = _ref_by_variable_period(j, bl)
    jok = j_ref_by_bl.where(bl > 0, j.shift(1)) <= 55
    violent_k = (ty & vr & ndt & jok.fillna(False) & op).fillna(False)

    result = pd.DataFrame({
        'ZX_SHORT': short_line,
        'ZX_LONG': bull_bear_line,
        'UP_SEQ': up_label,
        'UP_SEQ_Y': high * 1.002,
        'DOWN_SEQ': down_label,
        'DOWN_SEQ_Y': low * 0.998,
        'VIOLENT_K': violent_k,
        'VIOLENT_K_Y': high * 1.015,
    })

    if is_descending:
        result = result.iloc[::-1].reset_index(drop=True)
    result.index = df.index
    return result
