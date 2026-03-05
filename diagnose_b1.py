#!/usr/bin/env python3
"""
B1完美图形匹配诊断脚本
用于调试相似度计算问题
"""
import sys
import pandas as pd
import numpy as np
from pathlib import Path

# 添加项目路径
sys.path.insert(0, '/root/quant-csv')

from utils.csv_manager import CSVManager
from strategy.pattern_library import B1PatternLibrary
from strategy.pattern_config import B1_PERFECT_CASES

def diagnose_matching():
    """诊断匹配逻辑"""
    print("=" * 70)
    print("🔍 B1完美图形匹配诊断")
    print("=" * 70)
    
    csv_manager = CSVManager('/root/quant-csv/data')
    library = B1PatternLibrary(csv_manager)
    
    print("\n📊 案例库信息：")
    for case_id, case_data in library.cases.items():
        meta = case_data['meta']
        features = case_data['features']
        print(f"\n  {meta['name']} ({meta['code']}) - 突破日期: {meta['breakout_date']}")
        
        # 显示趋势结构特征
        trend = features.get('trend_structure', {})
        print(f"    趋势结构:")
        print(f"      - short_vs_bullbear: {trend.get('short_vs_bullbear', 'N/A')}")
        print(f"      - short_slope: {trend.get('short_slope', 'N/A')}%")
        print(f"      - is_in_bowl: {trend.get('is_in_bowl', 'N/A')}")
        print(f"      - price_vs_short_pct: {trend.get('price_vs_short_pct', trend.get('price_vs_short', 'N/A'))}%")
        print(f"      - price_bias_pct: {trend.get('price_bias_pct', 'N/A')}%")
        print(f"      - trend_spread_pct: {trend.get('trend_spread_pct', trend.get('trend_spread', 'N/A'))}%")
        
        # 显示KDJ特征
        kdj = features.get('kdj_state', {})
        print(f"    KDJ状态:")
        print(f"      - j_value: {kdj.get('j_value', 'N/A')}")
        print(f"      - j_position: {kdj.get('j_position', 'N/A')}")
        print(f"      - k_cross_d: {kdj.get('k_cross_d', 'N/A')}")
        
        # 显示量能特征
        vol = features.get('volume_pattern', {})
        print(f"    量能特征:")
        print(f"      - avg_volume_ratio: {vol.get('avg_volume_ratio', 'N/A')}")
        print(f"      - volume_trend: {vol.get('volume_trend', 'N/A')}")
        
        # 显示形态特征
        shape = features.get('price_shape', {})
        print(f"    形态特征:")
        print(f"      - max_drawdown: {shape.get('max_drawdown', 'N/A')}%")
        print(f"      - breakout_strength: {shape.get('breakout_strength', 'N/A')}%")
        print(f"      - overall_trend: {shape.get('overall_trend', 'N/A')}")
    
    # 测试国轩高科自匹配（应该100%相似）
    print("\n" + "=" * 70)
    print("🧪 自匹配测试（国轩高科匹配自己）")
    print("=" * 70)
    
    guoxuan_code = "002074"
    df = csv_manager.read_stock(guoxuan_code)
    
    if not df.empty:
        # 找到国轩高科案例的配置
        guoxuan_case = None
        for case in B1_PERFECT_CASES:
            if case['code'] == guoxuan_code:
                guoxuan_case = case
                break
        
        if guoxuan_case:
            # 提取突破日期窗口的数据
            from datetime import datetime
            breakout_dt = pd.to_datetime(guoxuan_case['breakout_date'])
            mask = df['date'] <= breakout_dt
            window_df = df[mask].head(25)
            
            print(f"\n  测试数据: {guoxuan_case['name']} 突破日期 {guoxuan_case['breakout_date']}")
            print(f"  数据条数: {len(window_df)}")
            
            if len(window_df) > 0:
                # 执行匹配
                result = library.find_best_match(guoxuan_code, window_df, lookback_days=25)
                
                print(f"\n  匹配结果:")
                if result.get('best_match'):
                    best = result['best_match']
                    print(f"    最佳匹配: {best['case_name']} ({best['case_code']})")
                    print(f"    相似度: {best['similarity_score']}%")
                    print(f"    分项得分:")
                    for dim, score in best.get('breakdown', {}).items():
                        print(f"      - {dim}: {score}%")
                else:
                    print("    ⚠️ 无匹配结果")
    
    print("\n" + "=" * 70)
    print("📈 相似度计算细节分析")
    print("=" * 70)
    
    # 比较两个案例的特征差异
    if len(library.cases) >= 2:
        case_list = list(library.cases.items())
        case1_id, case1_data = case_list[0]
        case2_id, case2_data = case_list[1]
        
        print(f"\n  对比: {case1_data['meta']['name']} vs {case2_data['meta']['name']}")
        
        f1 = case1_data['features']
        f2 = case2_data['features']
        
        # 趋势结构对比
        print(f"\n  趋势结构对比:")
        for key in ['short_vs_bullbear', 'short_slope', 'price_vs_short', 'trend_spread']:
            v1 = f1.get('trend_structure', {}).get(key, 0)
            v2 = f2.get('trend_structure', {}).get(key, 0)
            diff = abs(v1 - v2) if v1 and v2 else 'N/A'
            print(f"    {key}: {v1:.4f} vs {v2:.4f} (差值: {diff})")
        
        # KDJ对比
        print(f"\n  KDJ对比:")
        for key in ['j_value']:
            v1 = f1.get('kdj_state', {}).get(key, 0)
            v2 = f2.get('kdj_state', {}).get(key, 0)
            diff = abs(v1 - v2) if v1 and v2 else 'N/A'
            print(f"    {key}: {v1:.2f} vs {v2:.2f} (差值: {diff})")

if __name__ == "__main__":
    diagnose_matching()
