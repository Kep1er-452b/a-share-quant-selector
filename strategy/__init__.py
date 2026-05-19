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
from strategy.b1_v242p import B1V242PStrategy
from strategy.b2_beta import B2BetaStrategy
from strategy.b1_min_j_simple import B1MinJSimpleStrategy
from strategy.b1_min_j_complex import B1MinJComplexStrategy

# 策略类映射
STRATEGIES = {
    'BowlReboundStrategy': BowlReboundStrategy,
    'B1V242BStrategy': B1V242BStrategy,
    'B1V242PStrategy': B1V242PStrategy,
    'B2BetaStrategy': B2BetaStrategy,
    'B1MinJSimpleStrategy': B1MinJSimpleStrategy,
    'B1MinJComplexStrategy': B1MinJComplexStrategy,
}

__all__ = [
    'BowlReboundStrategy',
    'B1V242BStrategy',
    'B1V242PStrategy',
    'B2BetaStrategy',
    'B1MinJSimpleStrategy',
    'B1MinJComplexStrategy',
    'STRATEGIES'
]
