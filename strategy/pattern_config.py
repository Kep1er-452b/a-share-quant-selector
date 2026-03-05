"""
B1完美图形配置管理
新增案例只需在这里添加配置，无需改代码
为后续B2、B3等扩展预留空间
"""

# B1完美图形案例配置（10个历史成功案例）
B1_PERFECT_CASES = [
    {
        "id": "case_001",
        "name": "华纳药厂",
        "code": "688799",
        "breakout_date": "2025-05-12",
        "lookback_days": 25,
        "tags": ["科创板", "医药"],
        "description": "杯型整理+缩量突破",
    },
    {
        "id": "case_002",
        "name": "宁波韵升",
        "code": "600366",
        "breakout_date": "2025-08-06",
        "lookback_days": 25,
        "tags": ["主板", "稀土永磁"],
        "description": "回落碗中+放量启动",
    },
    {
        "id": "case_003",
        "name": "微芯生物",
        "code": "688321",
        "breakout_date": "2025-06-20",
        "lookback_days": 25,
        "tags": ["科创板", "医药"],
        "description": "平台整理+知行金叉",
    },
    {
        "id": "case_004",
        "name": "方正科技",
        "code": "600601",
        "breakout_date": "2025-07-23",
        "lookback_days": 25,
        "tags": ["主板", "科技"],
        "description": "V型反转+多空线支撑",
    },
    {
        "id": "case_005",
        "name": "澄天伟业",
        "code": "300689",
        "breakout_date": "2025-07-15",
        "lookback_days": 25,
        "tags": ["创业板", "芯片"],
        "description": "碗底缩量+倍量突破",
    },
    {
        "id": "case_006",
        "name": "国轩高科",
        "code": "002074",
        "breakout_date": "2025-08-04",
        "lookback_days": 25,
        "tags": ["中小板", "新能源"],
        "description": "趋势线上+缩量回踩",
    },
    {
        "id": "case_007",
        "name": "野马电池",
        "code": "605378",
        "breakout_date": "2025-08-01",
        "lookback_days": 25,
        "tags": ["主板", "电池"],
        "description": "旗形整理+知行发散",
    },
    {
        "id": "case_008",
        "name": "光电股份",
        "code": "600184",
        "breakout_date": "2025-07-10",
        "lookback_days": 25,
        "tags": ["主板", "军工"],
        "description": "双底形态+J值低位",
    },
    {
        "id": "case_009",
        "name": "新瀚新材",
        "code": "301076",
        "breakout_date": "2025-08-01",
        "lookback_days": 25,
        "tags": ["创业板", "化工"],
        "description": "杯柄形态+趋势线上",
    },
    {
        "id": "case_010",
        "name": "昂利康",
        "code": "002940",
        "breakout_date": "2025-07-11",
        "lookback_days": 25,
        "tags": ["中小板", "医药"],
        "description": "多空线粘合+放量突破",
    },
]

# 相似度权重配置（可调整）
SIMILARITY_WEIGHTS = {
    "trend_structure": 0.30,    # 知行趋势线结构
    "kdj_state": 0.20,          # KDJ动能状态
    "volume_pattern": 0.25,     # 量能特征
    "price_shape": 0.25,        # 价格形态
}

# 匹配阈值（低于此值不显示）
MIN_SIMILARITY_SCORE = 60.0

# Top N 结果展示
TOP_N_RESULTS = 15
