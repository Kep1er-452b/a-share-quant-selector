#!/usr/bin/env python3
"""
实际匹配效果测试
对比两支股票与案例库的相似度
"""
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, '/root/quant-csv')

from utils.csv_manager import CSVManager
from strategy.pattern_library import B1PatternLibrary

def test_real_matching():
    """测试实际匹配效果"""
    print("=" * 70)
    print("🧪 实际匹配效果测试")
    print("=" * 70)
    
    csv_manager = CSVManager('/root/quant-csv/data')
    library = B1PatternLibrary(csv_manager)
    
    # 测试股票列表
    test_stocks = [
        ("002074", "国轩高科"),  # B1案例之一
        ("000001", "平安银行"),  # 随机股票
    ]
    
    for code, name in test_stocks:
        print(f"\n{'='*70}")
        print(f"📊 测试股票: {name} ({code})")
        print(f"{'='*70}")
        
        df = csv_manager.read_stock(code)
        if df.empty:
            print(f"❌ 无数据")
            continue
        
        print(f"数据时间范围: {df['date'].iloc[-1]} 到 {df['date'].iloc[0]}")
        print(f"总数据条数: {len(df)}")
        
        # 取最近25天进行匹配
        recent_df = df.head(25)
        print(f"匹配窗口: {recent_df['date'].iloc[-1]} 到 {recent_df['date'].iloc[0]} ({len(recent_df)}天)")
        
        # 执行匹配
        result = library.find_best_match(code, recent_df, lookback_days=25)
        
        print(f"\n📈 匹配结果:")
        if result.get('best_match'):
            best = result['best_match']
            print(f"  最佳匹配案例: {best['case_name']} ({best['case_code']})")
            print(f"  突破日期: {best['case_date']}")
            print(f"  相似度: {best['similarity_score']:.2f}%")
            print(f"\n  分项得分:")
            for dim, score in best.get('breakdown', {}).items():
                print(f"    - {dim}: {score:.2f}%")
            
            # 显示前3匹配
            print(f"\n  Top 3 匹配:")
            for i, match in enumerate(result.get('all_matches', [])[:3], 1):
                print(f"    {i}. {match['case_name']}: {match['similarity_score']:.2f}%")
        else:
            print("  ⚠️ 无匹配结果")

if __name__ == "__main__":
    test_real_matching()
