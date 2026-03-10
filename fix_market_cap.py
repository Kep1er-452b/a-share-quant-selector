#!/usr/bin/env python3
"""
市值修复脚本 - 使用批量API
将此脚本放在项目根目录，执行: python3 fix_market_cap.py
"""
import pandas as pd
import glob
import akshare as ak
from pathlib import Path

def fix_market_cap():
    print('=== 修复市值数据 ===')
    
    # 批量获取实时数据
    print('获取实时行情数据...')
    spot_df = ak.stock_zh_a_spot_em()
    
    # 创建市值映射
    cap_map = {}
    for _, row in spot_df.iterrows():
        code = str(row['代码']).zfill(6)
        cap = row['总市值']
        if pd.notna(cap) and cap > 0:
            # 统一转为元
            cap_map[code] = int(cap * 1e8) if cap < 1e10 else int(cap)
    
    print(f'获取到 {len(cap_map)} 只股票市值')
    
    # 批量修复CSV
    csv_files = glob.glob('data/**/*.csv', recursive=True)
    print(f'总CSV文件: {len(csv_files)}')
    
    updated = 0
    for csv_file in csv_files:
        code = Path(csv_file).stem
        if code not in cap_map:
            continue
        try:
            df = pd.read_csv(csv_file)
            if df.empty:
                continue
            df['market_cap'] = cap_map[code]
            df.to_csv(csv_file, index=False)
            updated += 1
        except:
            pass
    
    print(f'完成: 修复 {updated} 个文件')

if __name__ == '__main__':
    fix_market_cap()
