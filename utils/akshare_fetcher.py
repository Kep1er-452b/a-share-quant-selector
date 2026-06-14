"""
A股数据抓取模块 - 使用 akshare / 直接HTTP请求
"""
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import time
import sys
import os
from pathlib import Path
import json
import requests
import random
import re
from threading import Lock

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.csv_manager import CSVManager
from utils.data_provider import BaseDataProvider, DataProviderError, normalize_market_cap_yuan

# 设置请求会话
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://quote.eastmoney.com/',
    'Connection': 'keep-alive',
})


# 备选A股股票列表（当网络获取失败时使用）
DEFAULT_STOCK_LIST = {
    # 上证指数成分股（部分）
    "600519": "贵州茅台", "600036": "招商银行", "601398": "工商银行",
    "600900": "长江电力", "601288": "农业银行", "601088": "中国神华",
    "601857": "中国石油", "600030": "中信证券", "601628": "中国人寿",
    "600276": "恒瑞医药", "601318": "中国平安", "600309": "万华化学",
    "600887": "伊利股份", "601166": "兴业银行", "600028": "中国石化",
    "601888": "中国中免", "600031": "三一重工", "601012": "隆基绿能",
    "603288": "海天味业", "600009": "上海机场", "600436": "片仔癀",
    "603259": "药明康德", "601668": "中国建筑", "600048": "保利发展",
    "600585": "海螺水泥", "601601": "中国太保", "603501": "韦尔股份",
    "600690": "海尔智家", "601818": "光大银行", "600893": "航发动力",
    "601688": "华泰证券", "601211": "国泰君安", "600837": "海通证券",
    "601669": "中国电建", "600406": "国电南瑞", "601989": "中国重工",
    "601186": "中国铁建", "601390": "中国中铁", "601800": "中国交建",
    "601618": "中国中冶", "601117": "中国化学", "601669": "中国电建",
    # 深证主板
    "000001": "平安银行", "000002": "万科A", "000333": "美的集团",
    "000858": "五粮液", "002594": "比亚迪", "000568": "泸州老窖",
    "000538": "云南白药", "002415": "海康威视", "000725": "京东方A",
    "000063": "中兴通讯", "002142": "宁波银行", "000651": "格力电器",
    "000895": "双汇发展", "002304": "洋河股份", "000776": "广发证券",
    "002271": "东方雨虹", "000938": "中芯国际", "002230": "科大讯飞",
    "000100": "TCL科技", "002460": "赣锋锂业", "002024": "苏宁易购",
    "000625": "长安汽车", "002007": "华兰生物", "000768": "中航西飞",
    "002049": "紫光国微", "000166": "申万宏源", "000069": "华侨城A",
    "000063": "中兴通讯", "000338": "潍柴动力", "000983": "山西焦煤",
    "000921": "海信家电", "000999": "华润三九", "000750": "国海证券",
    # 创业板
    "300750": "宁德时代", "300059": "东方财富", "300760": "迈瑞医疗",
    "300124": "汇川技术", "300015": "爱尔眼科", "300014": "亿纬锂能",
    "300433": "蓝思科技", "300003": "乐普医疗", "300122": "智飞生物",
    "300142": "沃森生物", "300408": "三环集团", "300413": "芒果超媒",
    "300001": "特锐德", "300033": "同花顺", "300496": "中科创达",
    "300136": "信维通信", "300383": "光环新网", "300316": "晶盛机电",
    "300454": "深信服", "300661": "圣邦股份", "300285": "国瓷材料",
    "300751": "迈为股份", "300618": "寒锐钴业", "300677": "英科医疗",
    "300776": "帝尔激光", "300073": "当升科技", "300724": "捷佳伟创",
    "300274": "阳光电源", "300763": "锦浪科技", "300012": "华测检测",
    "300496": "中科创达", "300223": "北京君正", "300373": "扬杰科技",
    "300207": "欣旺达", "300118": "东方日升", "300450": "先导智能",
    "300604": "长川科技", "300395": "菲利华", "300073": "当升科技",
    "300124": "汇川技术", "300760": "迈瑞医疗", "300015": "爱尔眼科",
    "300122": "智飞生物", "300142": "沃森生物", "300003": "乐普医疗",
    "300529": "健帆生物", "300601": "康泰生物", "300676": "华大基因",
    "300595": "欧普康视", "300357": "我武生物", "300832": "新产业",
    "300009": "安科生物", "300463": "迈克生物", "300026": "红日药业",
    "300026": "红日药业", "300244": "迪安诊断", "300298": "三诺生物",
    "300347": "泰格医药", "300558": "贝达药业", "300630": "普利制药",
    "300841": "康华生物", "300896": "爱美客", "300999": "金龙鱼",
    "300888": "稳健医疗", "300866": "安克创新", "300999": "金龙鱼",
}


class AKShareFetcher(BaseDataProvider):
    """AKShare 数据抓取器"""

    provider_name = "akshare"
    
    def __init__(self, data_dir="data", config=None):
        super().__init__(data_dir)
        self.csv_manager = CSVManager(data_dir)
        self.full_data_dir = Path(data_dir)
        self.stock_names_file = Path(data_dir) / 'stock_names.json'
        data_source_config = (config or {}).get('data_source', {}) if isinstance(config, dict) else {}
        akshare_config = data_source_config.get('akshare', {}) if isinstance(data_source_config, dict) else {}
        tencent_config = data_source_config.get('tencent', {}) if isinstance(data_source_config, dict) else {}
        self.akshare_timeout = self._coerce_number(
            akshare_config.get('timeout_seconds') or os.getenv('ASHARE_AK_TIMEOUT_SECONDS'),
            default=12.0,
            cast=float,
        )
        self.tencent_timeout = self._coerce_number(
            akshare_config.get('fallback_timeout_seconds') or os.getenv('ASHARE_TENCENT_TIMEOUT_SECONDS'),
            default=15.0,
            cast=float,
        )
        self.network_retries = self._coerce_number(
            akshare_config.get('network_retries') or os.getenv('ASHARE_NETWORK_RETRIES'),
            default=2,
            cast=int,
        )
        self.akshare_direct_retry_timeout = max(
            self._coerce_number(
                akshare_config.get('direct_retry_timeout_seconds')
                or os.getenv('ASHARE_AK_DIRECT_RETRY_TIMEOUT_SECONDS'),
                default=4.0,
                cast=float,
            ),
            1.0,
        )
        if self.provider_name == "akshare":
            self._sync_max_workers = min(
                self._sync_max_workers,
                self._coerce_number(
                    akshare_config.get('max_workers') or os.getenv('ASHARE_AK_MAX_WORKERS'),
                    default=8,
                    cast=int,
                ),
            )
        if self.provider_name == "tencent":
            self._sync_max_workers = min(
                self._sync_max_workers,
                self._coerce_number(
                    tencent_config.get('max_workers') or os.getenv('ASHARE_TENCENT_MAX_WORKERS'),
                    default=4,
                    cast=int,
                ),
            )
        tencent_interval = tencent_config.get('min_request_interval_seconds')
        if tencent_interval is None:
            tencent_interval = os.getenv('ASHARE_TENCENT_MIN_REQUEST_INTERVAL_SECONDS')
        self.tencent_request_interval = max(
            self._coerce_number(
                tencent_interval,
                default=0.35,
                cast=float,
            ),
            0.0,
        )
        self.tencent_request_jitter = max(
            self._coerce_number(
                tencent_config.get('request_jitter_seconds'),
                default=0.05,
                cast=float,
            ),
            0.0,
        )
        self.tencent_cooldown_every = max(
            self._coerce_number(
                tencent_config.get('cooldown_every_requests'),
                default=400,
                cast=int,
            ),
            0,
        )
        self.tencent_cooldown_seconds = max(
            self._coerce_number(
                tencent_config.get('cooldown_seconds'),
                default=8.0,
                cast=float,
            ),
            0.0,
        )
        self.tencent_max_request_interval = max(
            self._coerce_number(
                tencent_config.get('max_request_interval_seconds'),
                default=1.2,
                cast=float,
            ),
            self.tencent_request_interval,
        )
        self._tencent_current_interval = self.tencent_request_interval
        self._tencent_success_streak = 0
        self._tencent_request_count = 0
        self._tencent_request_lock = Lock()
        self._tencent_next_request_at = 0.0
        self.allow_mock_data = bool(
            akshare_config.get('allow_mock_data', False)
            or str(os.getenv('ASHARE_ALLOW_MOCK_DATA', '')).strip().lower() in {'1', 'true', 'yes'}
        )
        self._runtime_stats = {}
        self._runtime_lock = Lock()
        self._network_diagnostics_lock = Lock()
        self._network_error_samples = []
        self._tencent_fallback_lock = Lock()
        self._tencent_fallback_blocked = False
        self._tencent_fallback_error = None
        self._akshare_direct_lock = Lock()
        self._akshare_direct_route = "unknown"
        self._akshare_primary_lock = Lock()
        self._akshare_primary_route = "unknown"

    @staticmethod
    def _coerce_number(value, default, cast=float):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    def _note_runtime_stat(self, key, amount=1):
        with self._runtime_lock:
            self._runtime_stats[key] = self._runtime_stats.get(key, 0) + amount

    def get_runtime_stats(self) -> dict:
        with self._runtime_lock:
            return dict(self._runtime_stats)

    def _record_network_error(self, stage, stock_code, error):
        sample = {
            "stage": str(stage),
            "stock_code": str(stock_code).zfill(6),
            "error_type": type(error).__name__,
            "message": str(error)[:1000],
        }
        with self._network_diagnostics_lock:
            if len(self._network_error_samples) < 12:
                self._network_error_samples.append(sample)

    def get_runtime_diagnostics(self) -> dict:
        with self._network_diagnostics_lock:
            return {
                "network_error_samples": list(self._network_error_samples),
                "tencent_fallback_blocked": self._tencent_fallback_blocked,
                "tencent_fallback_error": self._tencent_fallback_error,
                "network_policy": {
                    "sync_max_workers": self._sync_max_workers,
                    "tencent_base_interval_seconds": self.tencent_request_interval,
                    "tencent_current_interval_seconds": round(self._tencent_current_interval, 4),
                    "tencent_request_jitter_seconds": self.tencent_request_jitter,
                    "tencent_cooldown_every_requests": self.tencent_cooldown_every,
                    "tencent_cooldown_seconds": self.tencent_cooldown_seconds,
                    "tencent_request_count": self._tencent_request_count,
                    "akshare_direct_retry_timeout_seconds": self.akshare_direct_retry_timeout,
                    "akshare_primary_route": self._akshare_primary_route,
                    "akshare_direct_route": self._akshare_direct_route,
                },
            }

    def _run_tencent_fallback(self, stock_code, stage, callback):
        """
        Keep Tencent WAF failures fatal for the Tencent provider, but isolate
        AkShare's optional fallback so it cannot abort the whole AkShare job.
        """
        if self.provider_name != "akshare":
            return callback()

        with self._tencent_fallback_lock:
            if self._tencent_fallback_blocked:
                self._note_runtime_stat("tencent_fallback_skipped")
                return None
            try:
                return callback()
            except DataProviderError as exc:
                self._record_network_error(f"{stage}_tencent_fallback", stock_code, exc)
                with self._network_diagnostics_lock:
                    self._tencent_fallback_blocked = True
                    self._tencent_fallback_error = str(exc)[:1000]
                self._note_runtime_stat("tencent_fallback_blocked")
                print(f"  腾讯兜底已停用，本轮 AkShare 更新将继续: {exc}")
                return None

    @staticmethod
    def _is_tencent_quote_url(url):
        return 'gtimg.cn' in str(url or '').lower()

    def _wait_for_tencent_request_slot(self):
        if self.tencent_request_interval <= 0:
            return
        with self._tencent_request_lock:
            now = time.monotonic()
            scheduled_at = max(now, self._tencent_next_request_at)
            self._tencent_request_count += 1
            if (
                self.tencent_cooldown_every > 0
                and self._tencent_request_count > 1
                and (self._tencent_request_count - 1) % self.tencent_cooldown_every == 0
            ):
                scheduled_at += self.tencent_cooldown_seconds
                self._note_runtime_stat('tencent_periodic_cooldown')
            jitter = random.uniform(0.0, self.tencent_request_jitter) if self.tencent_request_jitter else 0.0
            self._tencent_next_request_at = scheduled_at + self._tencent_current_interval + jitter
        wait_seconds = scheduled_at - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _slow_tencent_requests(self, reason):
        with self._tencent_request_lock:
            self._tencent_current_interval = min(
                self.tencent_max_request_interval,
                max(
                    self._tencent_current_interval * 1.5,
                    self._tencent_current_interval + 0.1,
                ),
            )
            self._tencent_success_streak = 0
        self._note_runtime_stat(f'tencent_throttle_backoff_{reason}')

    def _note_tencent_request_success(self):
        with self._tencent_request_lock:
            self._tencent_success_streak += 1
            if self._tencent_success_streak < 200:
                return
            self._tencent_success_streak = 0
            if self._tencent_current_interval > self.tencent_request_interval:
                self._tencent_current_interval = max(
                    self.tencent_request_interval,
                    self._tencent_current_interval * 0.9,
                )
                self._note_runtime_stat('tencent_throttle_recovered')

    @staticmethod
    def _is_tencent_waf_response(response):
        if getattr(response, 'status_code', None) != 501:
            return False
        body = str(getattr(response, 'text', '') or '')[:1000].lower()
        return 'waf.tencent.com/501page.html' in body

    def _request_get(self, url, *, params=None, headers=None, timeout=None):
        """
        Try the user's normal network first, then direct mode.
        This keeps VPN/proxy setups working when they are healthy, while still
        surviving broken system proxies for quote endpoints.
        """
        timeout = timeout or self.tencent_timeout
        headers = headers or {}
        attempts = (
            ("env", True),
            ("direct", False),
        )
        last_error = None
        for attempt in range(max(self.network_retries, 1)):
            for mode, trust_env in attempts:
                if self._is_tencent_quote_url(url):
                    self._wait_for_tencent_request_slot()
                request_session = requests.Session()
                request_session.trust_env = trust_env
                try:
                    response = request_session.get(
                        url,
                        params=params,
                        timeout=timeout,
                        headers=headers,
                    )
                    if self._is_tencent_waf_response(response):
                        self._note_runtime_stat('tencent_waf_501')
                        self._slow_tencent_requests('waf_501')
                        raise DataProviderError(
                            "Tencent 行情接口触发 WAF 501 拦截，通常与出口 IP 风控或连续请求频率有关。"
                            "请停止重复更新并等待限制解除；切换 VPN/代理不一定立即生效。"
                        )
                    if response.status_code in {403, 429, 500, 502, 503, 504}:
                        self._slow_tencent_requests(f'http_{response.status_code}')
                    response.raise_for_status()
                    if self._is_tencent_quote_url(url):
                        self._note_tencent_request_success()
                    self._note_runtime_stat(f"http_{mode}_success")
                    return response
                except DataProviderError:
                    raise
                except requests.RequestException as exc:
                    last_error = exc
                    self._note_runtime_stat(f"http_{mode}_error")
            if attempt + 1 < max(self.network_retries, 1):
                time.sleep(min(0.5 * (attempt + 1), 2.0))
        raise last_error
    
    def _load_local_stock_names(self):
        """从本地文件加载股票名称"""
        if self.stock_names_file.exists():
            try:
                with open(self.stock_names_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {}
    
    def _save_stock_names(self, stock_dict):
        """保存股票名称到本地"""
        try:
            with open(self.stock_names_file, 'w', encoding='utf-8') as f:
                json.dump(stock_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  保存股票名称失败: {e}")

    def _fetch_market_cap_tencent(self, stock_codes):
        """使用腾讯接口批量获取市值数据（akshare备选方案）"""
        market_cap_map = {}
        batch_size = 100
        total = len(stock_codes)
        
        try:
            for i in range(0, total, batch_size):
                batch = stock_codes[i:i + batch_size]
                query_codes = []
                for code in batch:
                    if code.startswith('6') or code.startswith('8'):
                        query_codes.append(f"sh{code}")
                    else:
                        query_codes.append(f"sz{code}")

                url = f"https://qt.gtimg.cn/q={','.join(query_codes)}"
                resp = self._request_get(url, timeout=self.tencent_timeout, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })

                lines = resp.text.strip().split(';')
                for line in lines:
                    if 'v_' in line and '~' in line:
                        try:
                            # 提取代码
                            code_match = line.split('v_')[1].split('=')[0] if 'v_' in line else ''
                            if not code_match or len(code_match) < 8:
                                continue
                            code = code_match[2:]  # 去掉 sh/sz 前缀
                            
                            parts = line.split('~')
                            if len(parts) >= 46:
                                # 字段44是总市值（亿）
                                cap = normalize_market_cap_yuan(parts[44], source_unit="hundred_million")
                                if cap:
                                    market_cap_map[code] = cap
                        except:
                            continue
                
                if i % 500 == 0 and i > 0:
                    print(f"  已获取 {i}/{total} 只市值...")
                    time.sleep(0.1)
                    
        except Exception as e:
            print(f"  腾讯接口获取市值失败: {e}")
        
        return market_cap_map

    @staticmethod
    def _filter_a_share_stock_dict(stocks):
        filtered = {}
        code_pattern = re.compile(r'^(00|30|60|68|88)\d{4}$')
        exclude_keywords = ['债', '基', 'ETF', 'LOF', '基金', '理财', '信托', 'B股', '指数', '国债', '企债', '转债', '回购', 'R-', 'GC']
        for code, name in (stocks or {}).items():
            code_text = str(code).zfill(6)
            name_text = str(name or '').strip()
            if not code_pattern.match(code_text):
                continue
            if any(keyword in name_text for keyword in exclude_keywords):
                continue
            filtered[code_text] = name_text
        return filtered

    def _fetch_stock_list_akshare_native(self):
        """Use AKShare's own stock-list APIs before any cross-provider fallback."""
        native_sources = []
        if hasattr(ak, 'stock_info_a_code_name'):
            native_sources.append(('akshare:stock_info_a_code_name', ak.stock_info_a_code_name))
        native_sources.append(('akshare:stock_zh_a_spot_em', ak.stock_zh_a_spot_em))

        for source_name, fetcher in native_sources:
            try:
                print(f"  尝试{source_name}...")
                df = fetcher()
                if df is None or df.empty:
                    continue
                code_column = 'code' if 'code' in df.columns else '代码'
                name_column = 'name' if 'name' in df.columns else '名称'
                if code_column not in df.columns or name_column not in df.columns:
                    continue
                stocks = dict(zip(df[code_column].astype(str), df[name_column].astype(str)))
                filtered = self._filter_a_share_stock_dict(stocks)
                if filtered:
                    print(f"✓ akshare 原生股票池获取成功: {len(filtered)} 只A股股票")
                    self._save_stock_names(filtered)
                    return filtered
            except Exception as e:
                print(f"  {source_name} 失败: {e}")
                time.sleep(1)
        return {}

    def get_market_caps(self, stock_codes):
        """批量获取最新市值数据"""
        market_cap_map = {}

        try:
            spot_df = ak.stock_zh_a_spot_em()
            for _, row in spot_df.iterrows():
                code = str(row['代码']).zfill(6)
                if code not in stock_codes:
                    continue
                cap = normalize_market_cap_yuan(row['总市值'], source_unit="auto")
                if cap:
                    market_cap_map[code] = cap
            if market_cap_map:
                return market_cap_map
        except Exception as e:
            print(f"  akshare接口失败: {e}")

        return self._fetch_market_cap_tencent(stock_codes)
    
    def _fetch_stock_list_http(self):
        """使用腾讯接口获取股票列表 - 覆盖5000+只A股"""
        try:
            stocks = {}
            
            # A股完整代码范围定义 - 分批次获取以加快速度
            # 沪市主板：600-609开头
            sh_ranges = []
            for prefix in range(600, 610):  # 600-609
                sh_ranges.append((f'{prefix}000', f'{prefix}999'))
            # 添加其他沪市段
            sh_ranges.extend([
                ('601000', '601999'),  # 601
                ('603000', '603999'),  # 603
                ('605000', '605999'),  # 605
                ('688000', '689999'),  # 科创板688-689
            ])
            
            # 深市完整范围
            sz_ranges = [
                ('000001', '009999'),  # 000开头全部
                ('001000', '001999'),  # 001
                ('002000', '002999'),  # 002中小板
                ('003000', '003999'),  # 003
                ('300000', '309999'),  # 创业板300-309
            ]
            
            # 从缓存加载已有的股票列表，避免重复查询
            cached_stocks = self._load_local_stock_names()
            if len(cached_stocks) >= 3000:
                print(f"  从本地缓存加载 {len(cached_stocks)} 只股票")
                return cached_stocks
            
            print(f"\n  正在通过腾讯接口获取股票列表...")
            print(f"  覆盖全部A股代码范围，约5000+只...")
            print(f"  这可能需要10-15分钟时间，请耐心等待...")
            
            # 分批查询，每次最多100只
            batch_size = 100
            all_codes = []
            
            # 生成密集的代码列表 - 步长改为1，覆盖几乎所有可能代码
            # 步长1可以获取最大数量的股票
            step = 1  # 步长1覆盖100%代码
            
            # 如果已有缓存且超过5000只，直接返回
            cached_stocks = self._load_local_stock_names()
            if len(cached_stocks) >= 5000:
                print(f"  从本地缓存加载 {len(cached_stocks)} 只股票")
                return cached_stocks
            
            # 沪市 - 全覆盖
            for start, end in sh_ranges:
                for code_num in range(int(start), int(end) + 1, step):
                    code = str(code_num).zfill(6)
                    all_codes.append(code)
            
            # 深市 - 全覆盖
            for start, end in sz_ranges:
                for code_num in range(int(start), int(end) + 1, step):
                    code = str(code_num).zfill(6)
                    all_codes.append(code)
            
            print(f"  计划查询 {len(all_codes)} 个代码 (步长{step})...")
            print(f"  预计可获取 3000-5000+ 只有效股票...")
            print(f"  提示: 首次获取需要约5-10分钟，请耐心等待...")
            
            total_batches = (len(all_codes) + batch_size - 1) // batch_size
            print(f"  总共 {total_batches} 批次，开始查询...")
            
            # 分批查询
            for i in range(0, len(all_codes), batch_size):
                batch = all_codes[i:i + batch_size]
                batch_num = i // batch_size + 1
                
                query_codes_list = []
                for c in batch:
                    if c.startswith('6') or c.startswith('8'):
                        query_codes_list.append(f"sh{c}")
                    elif c.startswith('0') or c.startswith('3'):
                        query_codes_list.append(f"sz{c}")
                
                if not query_codes_list:
                    continue
                    
                query_codes = ','.join(query_codes_list)
                url = f"https://qt.gtimg.cn/q={query_codes}"
                
                try:
                    resp = self._request_get(url, timeout=self.tencent_timeout, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    
                    lines = resp.text.strip().split(';')
                    for line in lines:
                        if 'v_' in line and '~' in line:
                            parts = line.split('~')
                            if len(parts) >= 45:  # 确保数据完整
                                code_match = line.split('v_')[1].split('=')[0] if 'v_' in line else ''
                                if code_match:
                                    code = code_match[2:]
                                    name = parts[1] if len(parts) > 1 else ''
                                    
                                    # 过滤条件
                                    exclude_keywords = ['债', '基', 'ETF', 'LOF', '理财', '信托', 'B股', '指数']
                                    
                                    # 检查是否退市或异常
                                    # 腾讯接口字段：
                                    # parts[1]=名称, parts[2]=代码, parts[3]=最新价, parts[4]=昨收, parts[5]=今开
                                    # parts[32]=状态, parts[33]=最高价, parts[34]=最低价
                                    
                                    is_valid = True
                                    
                                    # 1. 名称过滤
                                    if not name or name == '""' or any(x in name for x in exclude_keywords):
                                        is_valid = False
                                    
                                    # 2. 退市股票过滤 - 名称中包含"退"字
                                    if '退' in name:
                                        is_valid = False
                                    
                                    # 3. ST股票过滤（可选）
                                    # if 'ST' in name:
                                    #     is_valid = False
                                    
                                    # 4. 价格异常过滤 - 如果最新价为0或空，可能是停牌或退市
                                    try:
                                        current_price = float(parts[3]) if len(parts) > 3 else 0
                                        if current_price <= 0:
                                            is_valid = False
                                    except:
                                        is_valid = False
                                    
                                    # 5. 成交量异常过滤 - 长期无成交量的股票
                                    try:
                                        volume = float(parts[6]) if len(parts) > 6 else 0
                                        if volume <= 0:
                                            is_valid = False
                                    except:
                                        pass
                                    
                                    if is_valid:
                                        stocks[code] = name
                    
                    if batch_num % 20 == 0 or batch_num == 1:
                        print(f"    进度: {batch_num}/{total_batches} 批次, 已获取 {len(stocks)} 只股票...")
                    
                    time.sleep(0.1)  # 轻微限速
                    
                except Exception as e:
                    continue
            
            if stocks:
                print(f"  ✓ 通过腾讯接口获取: {len(stocks)} 只股票")
                return stocks
            
            # 如果获取失败，使用默认列表
            print(f"  使用默认列表: {len(DEFAULT_STOCK_LIST)} 只股票")
            return DEFAULT_STOCK_LIST.copy()
        except Exception as e:
            print(f"  HTTP获取失败: {e}")
            return DEFAULT_STOCK_LIST.copy()
    
    def get_all_stock_codes(self, max_retries=3):
        """获取所有A股股票代码（过滤债基、ETF、ST等）"""
        print("正在获取A股股票列表...")

        local_stocks = self._load_local_stock_names()
        if len(local_stocks) >= 3000:
            print(f"✓ 从 akshare 本地缓存加载: {len(local_stocks)} 只股票")
            return local_stocks

        native_stocks = self._fetch_stock_list_akshare_native()
        if native_stocks:
            return native_stocks

        bootstrap_stocks, bootstrap_path = self._load_shared_stock_names()
        if bootstrap_stocks:
            if Path(bootstrap_path) != Path(self.stock_names_file):
                self._save_stock_names(bootstrap_stocks)
            print(f"✓ akshare 原生股票池不可用，临时从本地中性股票池缓存加载: {len(bootstrap_stocks)} 只股票 ({bootstrap_path})")
            return bootstrap_stocks
        
        # 方法1: 直接HTTP请求
        for attempt in range(max_retries):
            try:
                print(f"  尝试HTTP直连 (第{attempt+1}/{max_retries}次)...")
                stocks = self._fetch_stock_list_http()
                if stocks:
                    filtered = self._filter_a_share_stock_dict(stocks)
                    
                    if filtered:
                        print(f"✓ HTTP获取成功: {len(filtered)} 只A股股票")
                        self._save_stock_names(filtered)
                        return filtered
            except Exception as e:
                print(f"  HTTP失败: {e}")
                time.sleep(1)
        
        # 方法2: akshare
        for attempt in range(max_retries):
            try:
                print(f"  尝试akshare (第{attempt+1}/{max_retries}次)...")
                
                sh_df = ak.stock_sh_a_spot_em()
                sz_df = ak.stock_sz_a_spot_em()
                
                all_stocks = pd.concat([sh_df[['代码', '名称']], sz_df[['代码', '名称']]])
                all_stocks = all_stocks.drop_duplicates(subset=['代码'])
                
                code_pattern = r'^(00|30|60|68|88)\d{4}$'
                all_stocks = all_stocks[all_stocks['代码'].str.match(code_pattern)]
                
                exclude_keywords = ['债', '基', 'ETF', 'LOF', '基金', '理财', '信托', 'B股', '指数', '国债', '企债', '转债', '回购', 'R-', 'GC']
                for keyword in exclude_keywords:
                    all_stocks = all_stocks[~all_stocks['名称'].str.contains(keyword, na=False)]
                
                stock_dict = dict(zip(all_stocks['代码'], all_stocks['名称']))
                print(f"✓ akshare获取成功: {len(stock_dict)} 只A股股票")
                self._save_stock_names(stock_dict)
                return stock_dict
                
            except Exception as e:
                print(f"  akshare失败: {e}")
                time.sleep(2 ** attempt)
        
        # 降级: 本地缓存或默认列表
        print("\n网络连接失败，尝试加载本地缓存...")
        local_stocks = self._load_local_stock_names()
        if local_stocks:
            print(f"✓ 从本地缓存加载: {len(local_stocks)} 只股票")
            return local_stocks
        
        print("\n使用内置默认股票列表...")
        print(f"✓ 加载默认列表: {len(DEFAULT_STOCK_LIST)} 只股票")
        return DEFAULT_STOCK_LIST.copy()
    
    def _fetch_stock_history_http(self, stock_code, years=6, source='tencent:fqkline'):
        """使用腾讯接口获取股票历史数据"""
        try:
            # 判断市场前缀
            if stock_code.startswith('6') or stock_code.startswith('88'):
                market_code = 'sh' + stock_code
            else:
                market_code = 'sz' + stock_code
            
            # 腾讯财经接口 - 获取日K线数据
            # 腾讯接口最多返回约1000条数据，所以分批获取或限制年限
            max_days = min(years * 365, 1000)  # 最多1000天
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={market_code},day,,,{max_days},qfq"
            
            resp = self._request_get(url, timeout=self.tencent_timeout, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://stock.finance.qq.com/'
            })
            
            data = resp.json()
            
            # 解析腾讯返回的数据（处理不同返回格式）
            data_level = data.get('data', {})
            
            # data_level 可能是 dict 或 list（大数据量时）
            if isinstance(data_level, dict):
                stock_data = data_level.get(market_code, {})
                if isinstance(stock_data, dict):
                    klines = stock_data.get('qfqday', []) or stock_data.get('day', [])
                else:
                    klines = []
            elif isinstance(data_level, list) and len(data_level) > 0:
                # 大数据量时返回列表，第一项是代码，第二项是数据
                # 找到对应股票代码的数据
                klines = []
                for item in data_level:
                    if isinstance(item, list) and len(item) >= 2 and item[0] == market_code:
                        # item[1] 是K线数据
                        if isinstance(item[1], list):
                            klines = item[1]
                        break
            else:
                klines = []
            
            if klines:
                records = []
                for item in klines:
                    # 腾讯格式: [日期, 开盘, 收盘, 最高, 最低, 成交量, ...]
                    # 注意: item[6] 可能是分红信息(dict)而不是成交额
                    if len(item) >= 6 and isinstance(item, list):
                        # 跳过分红信息，只取前6个字段
                        # 注意：腾讯接口返回的是 [日期, 开盘, 收盘, 最高, 最低, 成交量]
                        records.append({
                            'date': str(item[0]),
                            'open': float(item[1]),
                            'close': float(item[2]),
                            'high': float(item[3]),  # 最高 (item[3])
                            'low': float(item[4]),   # 最低 (item[4])
                            'volume': int(float(item[5])),
                            'amount': 0,  # 腾讯接口不直接提供成交额
                            'turnover': 0,  # 腾讯接口没有换手率
                        })
                
                if records:
                    df = pd.DataFrame(records)
                    df['date'] = pd.to_datetime(df['date'])
                    df['market_cap'] = 0
                    df = df.sort_values('date', ascending=False)
                    return self._mark_data_source(df, source)
            
            return None
        except DataProviderError:
            raise
        except Exception as e:
            print(f"  HTTP获取历史数据失败: {e}")
            return None
    
    def _get_realtime_market_cap(self, stock_code):
        """从实时数据获取总市值"""
        try:
            import akshare as ak
            spot_df = ak.stock_individual_info_em(symbol=stock_code, timeout=self.akshare_timeout)
            if not spot_df.empty:
                total_cap_row = spot_df[spot_df['item'] == '总市值']
                if not total_cap_row.empty:
                    total_cap = total_cap_row['value'].values[0]
                    if isinstance(total_cap, str):
                        if '亿' in total_cap:
                            return normalize_market_cap_yuan(total_cap.replace('亿', ''), source_unit="hundred_million")
                        else:
                            return normalize_market_cap_yuan(total_cap, source_unit="auto")
                    return normalize_market_cap_yuan(total_cap, source_unit="auto")
        except Exception as e:
            print(f"  获取总市值失败: {e}")
        return None

    @staticmethod
    def _mark_data_source(df, source):
        if df is None or df.empty:
            return df
        result = df.copy()
        result['data_source'] = source
        return result

    @staticmethod
    def _history_coverage_report(df, years=6):
        if df is None or df.empty or 'date' not in df.columns:
            return {'ok': False, 'strict_ok': False, 'rows': 0, 'span_days': 0}

        dates = pd.to_datetime(df['date'], errors='coerce').dropna()
        if dates.empty:
            return {'ok': False, 'strict_ok': False, 'rows': len(df), 'span_days': 0}

        rows = len(df)
        latest_date = dates.max().date()
        span_days = int((dates.max() - dates.min()).days)
        minimum_rows = 120
        minimum_span_days = 180
        minimum_recent_listing_rows = 5
        recent_data_window_days = 14
        preferred_span_days = int(years * 365 * 0.75)
        looks_like_recent_listing = (
            rows >= minimum_recent_listing_rows
            and (datetime.now().date() - latest_date).days <= recent_data_window_days
        )
        return {
            'ok': (rows >= minimum_rows and span_days >= minimum_span_days) or looks_like_recent_listing,
            'strict_ok': rows >= minimum_rows and span_days >= preferred_span_days,
            'rows': rows,
            'span_days': span_days,
            'recent_listing': looks_like_recent_listing,
        }
    
    def _generate_mock_data(self, stock_code, years=6):
        """生成模拟数据（当网络不可用时使用）"""
        import numpy as np
        
        np.random.seed(int(str(stock_code).zfill(6)) % 2**32)
        
        days = int(365 * years)
        end_date = datetime.now()
        dates = [end_date - timedelta(days=i) for i in range(days)]
        
        # 生成随机价格序列
        base_price = 10 + np.random.random() * 30
        returns = np.random.normal(0.0005, 0.02, days)
        prices = base_price * np.exp(np.cumsum(returns))
        
        # 生成OHLC数据
        df = pd.DataFrame({
            'date': dates,
            'close': prices,
            'volume': np.random.randint(1000000, 10000000, days),
            'amount': np.random.randint(10000000, 100000000, days),
            'turnover': np.random.uniform(1, 10, days),
        })
        
        # 生成合理的 open, high, low
        df['open'] = df['close'] * (1 + np.random.normal(0, 0.005, days))
        df['high'] = np.maximum(df[['open', 'close']].max(axis=1) * (1 + abs(np.random.normal(0, 0.01, days))), 
                                df[['open', 'close']].max(axis=1))
        df['low'] = np.minimum(df[['open', 'close']].min(axis=1) * (1 - abs(np.random.normal(0, 0.01, days))),
                               df[['open', 'close']].min(axis=1))
        
        # 添加总市值（从实时数据获取）
        market_cap = self._get_realtime_market_cap(stock_code)
        if market_cap:
            df['market_cap'] = market_cap
        else:
            df['market_cap'] = 0
        df['data_source'] = 'mock'
        
        # 按日期倒序排列
        df = df.sort_values('date', ascending=False)
        
        return df
    
    def _normalize_akshare_history(self, stock_code, df, source):
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            '日期': 'date', '开盘': 'open', '最高': 'high', '最低': 'low',
            '收盘': 'close', '成交量': 'volume', '成交额': 'amount', '换手率': 'turnover'
        })
        required = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'turnover']
        missing = [column for column in required if column not in df.columns]
        if missing:
            print(f"  akshare 行情缺少字段: {missing}")
            return None
        df = df[required]
        df['market_cap'] = 0
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date', ascending=False)
        return self._mark_data_source(df, source)

    def _fetch_eastmoney_history_direct(self, stock_code, start_date, end_date, source):
        """Retry AkShare's Eastmoney history endpoint without system proxy settings."""
        market_code = 1 if str(stock_code).startswith('6') else 0
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101",
            "fqt": "1",
            "secid": f"{market_code}.{stock_code}",
            "beg": start_date,
            "end": end_date,
        }
        direct_session = requests.Session()
        direct_session.trust_env = False
        response = direct_session.get(url, params=params, timeout=self.akshare_direct_retry_timeout)
        response.raise_for_status()
        payload = response.json()
        klines = ((payload.get("data") or {}).get("klines") or [])
        records = []
        for line in klines:
            values = str(line).split(",")
            if len(values) < 11:
                continue
            records.append({
                "date": values[0],
                "open": values[1],
                "close": values[2],
                "high": values[3],
                "low": values[4],
                "volume": values[5],
                "amount": values[6],
                "turnover": values[10],
            })
        if not records:
            return None

        frame = pd.DataFrame(records)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ["open", "close", "high", "low", "volume", "amount", "turnover"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["date", "open", "close", "high", "low"])
        if frame.empty:
            return None
        frame["market_cap"] = 0
        frame = frame.sort_values("date", ascending=False)
        return self._mark_data_source(frame, source)

    def _try_eastmoney_direct(self, stock_code, start_date, end_date, source):
        if self._akshare_direct_route == "blocked":
            self._note_runtime_stat('akshare_direct_retry_skipped')
            return None
        if self._akshare_direct_route == "available":
            try:
                return self._fetch_eastmoney_history_direct(
                    stock_code,
                    start_date,
                    end_date,
                    source,
                )
            except Exception as exc:
                self._note_runtime_stat('akshare_direct_retry_error')
                self._record_network_error(f'{source}_failed', stock_code, exc)
                with self._akshare_direct_lock:
                    self._akshare_direct_route = "blocked"
                return None

        with self._akshare_direct_lock:
            if self._akshare_direct_route == "blocked":
                self._note_runtime_stat('akshare_direct_retry_skipped')
                return None
            if self._akshare_direct_route == "available":
                return self._try_eastmoney_direct(
                    stock_code,
                    start_date,
                    end_date,
                    source,
                )
            try:
                frame = self._fetch_eastmoney_history_direct(
                    stock_code,
                    start_date,
                    end_date,
                    source,
                )
            except Exception as exc:
                self._akshare_direct_route = "blocked"
                self._note_runtime_stat('akshare_direct_retry_error')
                self._record_network_error(f'{source}_probe', stock_code, exc)
                print(f"  akshare直连探测失败，本轮不再重复探测: {exc}")
                return None
            if frame is not None and not frame.empty:
                self._akshare_direct_route = "available"
                self._note_runtime_stat('akshare_direct_retry_success')
                return frame
            self._akshare_direct_route = "blocked"
            self._note_runtime_stat('akshare_direct_retry_short')
            return None

    def _mark_akshare_primary_success(self):
        with self._akshare_primary_lock:
            self._akshare_primary_route = "available"

    def _mark_akshare_primary_network_failure(self):
        with self._akshare_primary_lock:
            if self._akshare_primary_route == "available":
                self._note_runtime_stat('akshare_primary_transient_error')
                return
            self._akshare_primary_route = "blocked"
        self._note_runtime_stat('akshare_primary_route_blocked')

    def fetch_stock_history(self, stock_code, years=6):
        """
        抓取单只股票历史数据
        前复权，按日期倒序排列
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365 * years)
        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        primary_blocked = self._akshare_primary_route == "blocked"
        if primary_blocked:
            self._note_runtime_stat('akshare_primary_route_skipped')
            direct_df = self._try_eastmoney_direct(
                stock_code,
                start_str,
                end_str,
                'akshare:eastmoney:direct',
            )
            coverage = self._history_coverage_report(direct_df, years=years)
            if coverage['ok']:
                return direct_df

        if not primary_blocked:
            try:
                df = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust="qfq",
                    timeout=self.akshare_timeout,
                )

                if df is not None and not df.empty:
                    df = self._normalize_akshare_history(stock_code, df, 'akshare:stock_zh_a_hist')
                    coverage = self._history_coverage_report(df, years=years)
                    if coverage['ok']:
                        self._mark_akshare_primary_success()
                        self._note_runtime_stat('akshare_history_success')
                        return df
                    print(f"  akshare历史数据不足: {coverage['rows']}条/{coverage['span_days']}天")
            except Exception as e:
                self._note_runtime_stat('akshare_history_error')
                self._record_network_error('akshare_history', stock_code, e)
                print(f"  akshare获取失败: {e}")
                if isinstance(e, requests.exceptions.RequestException):
                    self._mark_akshare_primary_network_failure()
                    direct_df = self._try_eastmoney_direct(
                        stock_code,
                        start_str,
                        end_str,
                        'akshare:eastmoney:direct',
                    )
                    if direct_df is not None and not direct_df.empty:
                        coverage = self._history_coverage_report(direct_df, years=years)
                        if coverage['ok']:
                            return direct_df

        fallback_df = self._run_tencent_fallback(
            stock_code,
            'history',
            lambda: self._fetch_stock_history_http(
                stock_code,
                years=years,
                source='tencent:fqkline:fallback',
            ),
        )
        coverage = self._history_coverage_report(fallback_df, years=years)
        if coverage['ok']:
            self._note_runtime_stat('tencent_history_fallback_success')
            return fallback_df
        if fallback_df is not None and not fallback_df.empty:
            self._note_runtime_stat('tencent_history_fallback_short')
            print(f"  腾讯兜底历史数据不足: {coverage['rows']}条/{coverage['span_days']}天")

        if self.allow_mock_data:
            print("  ⚠️ 已显式启用模拟数据，将生成 demo 行情")
            return self._generate_mock_data(stock_code, years)

        print("  ✗ 真实历史数据不可用，已拒绝写入模拟行情")
        self._note_runtime_stat('history_fetch_failed')
        return None
    
    def _fetch_stock_update_http(self, stock_code, days=10, source='tencent:fqkline:update'):
        """
        使用腾讯 HTTP 抓取近期数据。TencentFetcher 会显式调用该路径。
        """
        try:
            # 判断市场前缀
            if stock_code.startswith('6') or stock_code.startswith('88'):
                market_code = 'sh' + stock_code
            else:
                market_code = 'sz' + stock_code
            
            # 腾讯接口：直接指定获取天数（最多1000天）
            # 多取2天确保覆盖周末节假日
            fetch_days = min(days + 2, 1000)
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={market_code},day,,,{fetch_days},qfq"
            
            resp = self._request_get(url, timeout=self.tencent_timeout, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://stock.finance.qq.com/'
            })
            
            data = resp.json()
            
            # 解析数据
            data_level = data.get('data', {})
            klines = []
            
            if isinstance(data_level, dict):
                stock_data = data_level.get(market_code, {})
                if isinstance(stock_data, dict):
                    klines = stock_data.get('qfqday', []) or stock_data.get('day', [])
            elif isinstance(data_level, list) and len(data_level) > 0:
                for item in data_level:
                    if isinstance(item, list) and len(item) >= 2 and item[0] == market_code:
                        if isinstance(item[1], list):
                            klines = item[1]
                        break
            
            if klines:
                records = []
                for item in klines:
                    if len(item) >= 6 and isinstance(item, list):
                        # 腾讯格式: [日期, 开盘, 收盘, 最高, 最低, 成交量]
                        records.append({
                            'date': str(item[0]),
                            'open': float(item[1]),
                            'close': float(item[2]),
                            'high': float(item[3]),  # 最高
                            'low': float(item[4]),   # 最低
                            'volume': int(float(item[5])),
                            'amount': 0,
                            'turnover': 0,
                        })
                
                if records:
                    df = pd.DataFrame(records)
                    df['date'] = pd.to_datetime(df['date'])
                    df['amount'] = 0
                    df['turnover'] = 0
                    df['market_cap'] = 0
                    df = df.sort_values('date', ascending=False)
                    return self._mark_data_source(df, source)
            
            return None
        except DataProviderError:
            raise
        except Exception as e:
            print(f"  获取更新数据失败: {e}")
            return None

    def fetch_stock_update(self, stock_code, days=10):
        """
        抓取近期数据用于增量更新。
        market_cap 由调用方通过 _apply_market_cap_override 批量注入，此处不再逐股请求。
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=max(days * 4 + 10, 20))

        primary_blocked = self._akshare_primary_route == "blocked"
        if primary_blocked:
            self._note_runtime_stat('akshare_primary_route_skipped')
            direct_df = self._try_eastmoney_direct(
                stock_code,
                start_date.strftime("%Y%m%d"),
                end_date.strftime("%Y%m%d"),
                'akshare:eastmoney:direct:update',
            )
            if direct_df is not None and not direct_df.empty:
                return direct_df

        if not primary_blocked:
            try:
                df = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period="daily",
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust="qfq",
                    timeout=self.akshare_timeout,
                )
                normalized = self._normalize_akshare_history(stock_code, df, 'akshare:stock_zh_a_hist:update')
                if normalized is not None and not normalized.empty:
                    self._mark_akshare_primary_success()
                    self._note_runtime_stat('akshare_update_success')
                    return normalized
            except Exception as e:
                self._note_runtime_stat('akshare_update_error')
                self._record_network_error('akshare_update', stock_code, e)
                print(f"  akshare获取更新数据失败: {e}")
                if isinstance(e, requests.exceptions.RequestException):
                    self._mark_akshare_primary_network_failure()
                    direct_df = self._try_eastmoney_direct(
                        stock_code,
                        start_date.strftime("%Y%m%d"),
                        end_date.strftime("%Y%m%d"),
                        'akshare:eastmoney:direct:update',
                    )
                    if direct_df is not None and not direct_df.empty:
                        return direct_df

        fallback_df = self._run_tencent_fallback(
            stock_code,
            'update',
            lambda: self._fetch_stock_update_http(
                stock_code,
                days=days,
                source='tencent:fqkline:update:fallback',
            ),
        )
        if fallback_df is not None and not fallback_df.empty:
            self._note_runtime_stat('tencent_update_fallback_success')
            return fallback_df
        self._note_runtime_stat('update_fetch_failed')
        return None
    
    def init_full_data(self, max_stocks=None, skip_failed=True):
        """
        首次全量抓取
        :param max_stocks: 限制抓取数量（用于测试）
        :param skip_failed: 是否跳过之前失败的股票
        """
        import akshare as ak
        
        stock_dict = self.get_all_stock_codes()
        
        if not stock_dict:
            print("无法获取股票列表")
            return
        
        stock_codes = list(stock_dict.keys())
        
        # 加载之前失败的股票列表
        failed_stocks_file = self.full_data_dir / 'failed_stocks.json'
        failed_stocks = set()
        if skip_failed and failed_stocks_file.exists():
            try:
                with open(failed_stocks_file, 'r', encoding='utf-8') as f:
                    failed_stocks = set(json.load(f))
                print(f"  将跳过 {len(failed_stocks)} 只之前获取失败的股票")
                # 从列表中移除失败的股票
                stock_codes = [c for c in stock_codes if c not in failed_stocks]
            except:
                pass
        
        if max_stocks:
            stock_codes = stock_codes[:max_stocks]
        
        # 批量获取市值数据（主接口：akshare，备选：腾讯）
        print("\n正在批量获取市值数据...")
        market_cap_map = {}
        
        # 方法1: 尝试akshare接口
        try:
            spot_df = ak.stock_zh_a_spot_em()
            for _, row in spot_df.iterrows():
                code = str(row['代码']).zfill(6)
                cap = normalize_market_cap_yuan(row['总市值'], source_unit="auto")
                if cap:
                    market_cap_map[code] = cap
            print(f"  ✓ akshare接口成功: {len(market_cap_map)} 只股票市值")
        except Exception as e:
            print(f"  akshare接口失败: {e}")
            print("  尝试腾讯备选接口...")
            # 方法2: 使用腾讯接口备选
            market_cap_map = self._fetch_market_cap_tencent(stock_codes)
            if market_cap_map:
                print(f"  ✓ 腾讯接口成功: {len(market_cap_map)} 只股票市值")
            else:
                print(f"  ✗ 腾讯接口也失败，市值数据将缺失")
        
        total = len(stock_codes)
        success = 0
        failed = 0
        failed_list = []
        
        print(f"\n开始抓取 {total} 只股票的6年历史数据...")
        print("=" * 60)
        
        for i, code in enumerate(stock_codes, 1):
            print(f"[{i}/{total}] 抓取 {code} {stock_dict.get(code, '')} ...", end=" ")
            
            df = self.fetch_stock_history(code, years=6)
            
            if df is not None and not df.empty:
                # 数据校验 - 检查是否有有效价格数据
                valid_data = True
                if len(df) < 10:  # 数据太少，可能是新股或数据异常
                    print(f"⚠ 数据太少({len(df)}条)")
                    valid_data = False
                    failed_list.append(code)
                elif df['close'].mean() <= 0:  # 价格异常
                    print(f"⚠ 价格异常")
                    valid_data = False
                    failed_list.append(code)
                else:
                    # 使用批量获取的市值数据
                    if code in market_cap_map:
                        df['market_cap'] = market_cap_map[code]
                    self.csv_manager.write_stock(code, df)
                    print(f"✓ ({len(df)}条)")
                    success += 1
            else:
                print("✗ 失败")
                failed += 1
                failed_list.append(code)
            
            # 限速，避免请求过快
            if i % 10 == 0:
                time.sleep(1)
        
        # 保存失败的股票列表
        if failed_list:
            try:
                with open(failed_stocks_file, 'w', encoding='utf-8') as f:
                    json.dump(failed_list, f)
                print(f"\n  已保存 {len(failed_list)} 只获取失败的股票到 failed_stocks.json")
            except Exception as e:
                print(f"\n  保存失败列表出错: {e}")
        
        print("=" * 60)
        print(f"完成! 成功: {success}, 失败: {failed + len(failed_list)}")
        if failed_list and not max_stocks:
            print(f"提示: 再次运行 init 命令可跳过失败股票，专注于成功获取的数据")
    
    def daily_update(self, max_stocks=None):
        """
        每日增量更新 - 只获取实际需要的天数
        优化：使用快速缓存机制，避免重复读取已更新的股票
        修复：盘中执行时不会将盘中数据误存为收盘数据
        """
        from datetime import datetime
        
        existing_stocks = self.csv_manager.list_all_stocks()
        
        if not existing_stocks:
            print("没有找到已有数据，请先执行 init")
            return
        
        if max_stocks:
            existing_stocks = existing_stocks[:max_stocks]
        
        total = len(existing_stocks)
        updated = 0
        failed = 0
        skipped = 0
        
        print(f"\n开始更新 {total} 只股票的数据...")
        print("=" * 60)
        
        today = datetime.now().date()
        today_str = today.strftime('%Y-%m-%d')
        current_time = datetime.now().time()
        
        # 判断是否在收盘后（15:00 之后）
        # A股收盘时间：工作日 15:00
        market_close_time = datetime.strptime("15:00", "%H:%M").time()
        is_after_market_close = current_time >= market_close_time
        
        if not is_after_market_close and not max_stocks:
            print(f"⚠️ 当前时间 {current_time.strftime('%H:%M')}，尚未收盘 (15:00)")
            print("  盘中数据不是收盘价，建议收盘后再执行 update")
            print("  如需强制更新，请使用 --max-stocks 参数")
            print("=" * 60)
            return
        
        # 快速缓存：检查上次更新记录
        update_cache_file = self.full_data_dir / '.update_cache.json'
        update_cache = {}
        if update_cache_file.exists():
            try:
                with open(update_cache_file, 'r', encoding='utf-8') as f:
                    update_cache = json.load(f)
            except:
                update_cache = {}
        
        # 如果今天已经更新过（且已收盘），直接跳过
        cache_date = update_cache.get('last_update_date')
        if cache_date == today_str and not max_stocks:
            print(f"✓ 数据已于 {cache_date} 收盘后更新过，无需重复更新")
            print("=" * 60)
            return
        
        # 预筛选：快速检查哪些股票需要更新（只读取第一行）
        stocks_to_update = []
        print("  正在检查股票更新状态...")
        
        for code in existing_stocks:
            # 快速读取：只读CSV第一行（最新日期）
            path = self.csv_manager.get_stock_path(code)
            if not path.exists():
                stocks_to_update.append((code, 30))  # 默认取30天
                continue
            
            try:
                # 只读取第一行（header + 第一行数据）
                df_quick = pd.read_csv(path, nrows=1)
                if df_quick.empty:
                    stocks_to_update.append((code, 30))
                    continue
                
                latest_date = pd.to_datetime(df_quick.iloc[0]['date']).date()
                days_needed = (today - latest_date).days
                
                if days_needed > 0:
                    days_to_fetch = min(days_needed + 2, 60)
                    stocks_to_update.append((code, days_to_fetch))
                elif days_needed == 0:
                    # 最新日期是今天
                    # 如果是收盘后，或者强制更新模式(max_stocks)，都需要重新获取
                    if is_after_market_close or max_stocks:
                        stocks_to_update.append((code, 2))
                    else:
                        skipped += 1
                else:
                    skipped += 1
            except Exception:
                stocks_to_update.append((code, 30))
        
        need_update = len(stocks_to_update)
        print(f"  需要更新: {need_update} 只, 已最新: {skipped} 只")
        
        if need_update == 0:
            # 只有在完整更新（非max_stocks模式）且收盘后才记录缓存
            if not max_stocks and is_after_market_close:
                update_cache['last_update_date'] = today_str
                with open(update_cache_file, 'w', encoding='utf-8') as f:
                    json.dump(update_cache, f)
            print("✓ 所有数据已是最新")
            print("=" * 60)
            return
        
        # 批量获取最新市值数据（主接口：akshare，备选：腾讯）
        print("\n正在批量获取最新市值数据...")
        market_cap_map = {}
        
        # 方法1: 尝试akshare接口
        try:
            import akshare as ak
            spot_df = ak.stock_zh_a_spot_em()
            for _, row in spot_df.iterrows():
                code = str(row['代码']).zfill(6)
                cap = normalize_market_cap_yuan(row['总市值'], source_unit="auto")
                if cap:
                    market_cap_map[code] = cap
            print(f"  ✓ akshare接口成功: {len(market_cap_map)} 只股票市值")
        except Exception as e:
            print(f"  akshare接口失败: {e}")
            print("  尝试腾讯备选接口...")
            # 方法2: 使用腾讯接口备选（只获取需要更新的股票）
            update_codes = [code for code, _ in stocks_to_update]
            market_cap_map = self._fetch_market_cap_tencent(update_codes)
            if market_cap_map:
                print(f"  ✓ 腾讯接口成功: {len(market_cap_map)} 只股票市值")
            else:
                print(f"  ✗ 腾讯接口也失败，市值数据将缺失")
        
        print(f"\n开始更新 {need_update} 只股票...")
        print("=" * 60)
        
        for i, (code, days_to_fetch) in enumerate(stocks_to_update, 1):
            print(f"[{i}/{need_update}] 更新 {code} (需获取 {days_to_fetch} 天数据)...", end=" ")
            
            # 重新读取现有数据以获取旧记录数
            existing_df = self.csv_manager.read_stock(code)
            old_count = len(existing_df)
            
            df = self.fetch_stock_update(code, days=days_to_fetch)
            
            if df is not None and not df.empty:
                # 更新市值数据（和价格数据一起更新）
                if code in market_cap_map:
                    df['market_cap'] = market_cap_map[code]
                self.csv_manager.update_stock(code, df)
                new_df = self.csv_manager.read_stock(code)
                new_count = len(new_df)
                added = new_count - old_count
                print(f"✓ (新增 {added} 条)")
                updated += 1
            else:
                print("✗ 失败")
                failed += 1
            
            if i % 10 == 0:
                time.sleep(0.1)  # 降低限速
        
        # 更新缓存记录
        update_cache['last_update_date'] = today_str
        with open(update_cache_file, 'w', encoding='utf-8') as f:
            json.dump(update_cache, f)
        
        print("=" * 60)
        print(f"完成! 更新成功: {updated}, 跳过: {skipped}, 失败: {failed}")
