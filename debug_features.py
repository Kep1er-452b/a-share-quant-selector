#!/usr/bin/env python3
"""
详细诊断：检查特征提取的数据问题
"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, '/root/quant-csv')

from utils.csv_manager import CSVManager
from utils.technical import calculate_zhixing_trend, KDJ

def debug_feature_extraction():
    """调试特征提取"""
    print("=" * 70)
    print("🔍 特征提取详细调试 - 国轩高科")
    print("=" * 70)
    
    csv_manager = CSVManager('/root/quant-csv/data')
    
    # 读取国轩高科数据
    code = "002074"
    df = csv_manager.read_stock(code)
    
    if df.empty:
        print("❌ 无数据")
        return
    
    print(f"\n📊 原始数据预览（前5行）：")
    print(df[['date', 'open', 'high', 'low', 'close', 'volume']].head())
    print(f"\n数据顺序: {'倒序(最新在前)' if pd.to_datetime(df['date'].iloc[0]) > pd.to_datetime(df['date'].iloc[-1]) else '正序'}")
    
    # 提取突破日期窗口的数据
    breakout_date = "2025-08-04"
    breakout_dt = pd.to_datetime(breakout_date)
    mask = df['date'] <= breakout_dt
    window_df = df[mask].head(25).copy()
    
    print(f"\n📅 突破日期: {breakout_date}")
    print(f"📊 窗口数据条数: {len(window_df)}")
    print(f"\n窗口数据日期范围: {window_df['date'].iloc[-1]} 到 {window_df['date'].iloc[0]}")
    
    if len(window_df) == 0:
        print("❌ 窗口数据为空")
        return
    
    print(f"\n📊 窗口数据预览（正序）：")
    window_sorted = window_df.sort_values('date').reset_index(drop=True)
    print(window_sorted[['date', 'open', 'high', 'low', 'close', 'volume']].head(10))
    
    # 计算知行趋势线
    print(f"\n📈 计算知行趋势线...")
    trend_df = calculate_zhixing_trend(window_sorted)
    window_sorted['short_term_trend'] = trend_df['short_term_trend']
    window_sorted['bull_bear_line'] = trend_df['bull_bear_line']
    
    print(f"\n知行趋势线数据（前10行）：")
    print(window_sorted[['date', 'close', 'short_term_trend', 'bull_bear_line']].head(10))
    
    # 计算KDJ
    print(f"\n📊 计算KDJ...")
    kdj_df = KDJ(window_sorted, n=9, m1=3, m2=3)
    window_sorted['K'] = kdj_df['K']
    window_sorted['D'] = kdj_df['D']
    window_sorted['J'] = kdj_df['J']
    
    print(f"\nKDJ数据（前10行）：")
    print(window_sorted[['date', 'close', 'K', 'D', 'J']].head(10))
    
    # 取最后一天（最新）的数据
    latest = window_sorted.iloc[-1]
    print(f"\n📍 最后一天 ({latest['date']}) 的数据：")
    print(f"  close: {latest['close']}")
    print(f"  short_term_trend: {latest['short_term_trend']}")
    print(f"  bull_bear_line: {latest['bull_bear_line']}")
    print(f"  J: {latest['J']}")
    
    # 计算特征值
    print(f"\n🔢 计算的特征值：")
    
    # 短期趋势 vs 多空线
    if latest['bull_bear_line'] != 0:
        short_vs_bullbear = latest['short_term_trend'] / latest['bull_bear_line']
        print(f"  short_vs_bullbear: {short_vs_bullbear:.4f} ({latest['short_term_trend']:.4f} / {latest['bull_bear_line']:.4f})")
    
    # 斜率计算（近5日）
    if len(window_sorted) >= 5:
        short_slope = (window_sorted['short_term_trend'].iloc[-1] / window_sorted['short_term_trend'].iloc[-5] - 1) * 100
        print(f"  short_slope: {short_slope:.4f}% (第-1日: {window_sorted['short_term_trend'].iloc[-1]:.4f}, 第-5日: {window_sorted['short_term_trend'].iloc[-5]:.4f})")
    
    # 价格位置
    if latest['short_term_trend'] != 0:
        price_vs_short = latest['close'] / latest['short_term_trend']
        print(f"  price_vs_short: {price_vs_short:.4f} ({latest['close']:.4f} / {latest['short_term_trend']:.4f})")
    
    # 是否在碗中
    is_in_bowl = (latest['short_term_trend'] > latest['close'] > latest['bull_bear_line'])
    print(f"  is_in_bowl: {is_in_bowl} ({latest['short_term_trend']:.4f} > {latest['close']:.4f} > {latest['bull_bear_line']:.4f})")
    
    # 趋势发散程度
    if latest['bull_bear_line'] != 0:
        trend_spread = (latest['short_term_trend'] - latest['bull_bear_line']) / latest['bull_bear_line'] * 100
        print(f"  trend_spread: {trend_spread:.4f}%")

if __name__ == "__main__":
    debug_feature_extraction()
