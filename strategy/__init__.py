"""
策略模块

自动注册所有策略类
"""
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 导入策略
from strategy.bowl_rebound import BowlReboundStrategy
from strategy.b1_v242b import B1V242BStrategy

# 策略类映射
STRATEGIES = {
    'BowlReboundStrategy': BowlReboundStrategy,
    'B1V242BStrategy': B1V242BStrategy,
}

__all__ = [
    'BowlReboundStrategy',
    'B1V242BStrategy',
    'STRATEGIES'
]
