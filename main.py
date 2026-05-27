#!/usr/bin/env python3
"""
A股量化选股系统 - 主程序

使用方法:
    python main.py init      # 首次全量抓取
    python main.py select    # 仅执行筛选
    python main.py run       # 完整流程（更新+选股+通知）
    python main.py web       # 启动 Web 界面
    python main.py calendar  # 查看或更新交易日历缓存
    python main.py export    # 导出单只股票 CSV 到 Downloads
"""
import sys
import os
import argparse
import platform
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, time as dt_time
import time
import getpass

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 版本信息
__version__ = "1.0.0"

from utils.csv_manager import CSVManager
from utils.dingtalk_notifier import DingTalkNotifier
from strategy.strategy_registry import get_registry
from utils.kline_chart import generate_kline_chart
from utils.data_provider import BOARD_LABELS, create_data_provider, get_config_value, DataProviderError
from utils.progress import ProgressTracker
from utils.provider_router import activate_provider, active_data_dir, warehouse_summary
from utils.selection_worker import build_worker_context, process_selection_chunk, initialize_selection_worker
from utils.strategy_labels import CATEGORY_DISPLAY_ORDER, category_label, is_invalid_stock_name
from utils.local_config import load_config_file


def prompt_for_provider(default_provider="akshare"):
    """交互式选择数据源"""
    default_provider = (default_provider or "akshare").strip().lower()
    choices = {"1": "akshare", "2": "tushare", "3": "tencent"}
    reverse_choices = {value: key for key, value in choices.items()}
    default_choice = reverse_choices.get(default_provider, "1")

    print("\n请选择数据源：")
    print("  1. akshare")
    print("  2. tushare")
    print("  3. tencent")

    while True:
        choice = input(f"输入 1 / 2 / 3 (默认: {default_choice}): ").strip() or default_choice
        if choice in choices:
            return choices[choice]
        print("请输入 1、2 或 3。")


def prompt_yes_no(message, default=True):
    """终端 yes/no 提示"""
    default_hint = "Y/n" if default else "y/N"
    default_value = "y" if default else "n"
    while True:
        choice = input(f"{message} ({default_hint}): ").strip().lower() or default_value
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def prompt_for_strategy(available_strategies, default_strategy="all"):
    """交互式选择筛选策略"""
    options = ["all"] + list(available_strategies)
    default_index = options.index(default_strategy) + 1 if default_strategy in options else 1

    print("\n请选择筛选策略：")
    for index, strategy_name in enumerate(options, 1):
        label = "全部策略" if strategy_name == "all" else strategy_name
        print(f"  {index}. {label}")

    while True:
        choice = input(f"输入序号 (默认: {default_index}): ").strip() or str(default_index)
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(options):
                return options[selected_index]
        print("请输入有效序号。")


def resolve_provider_name(args, config):
    """解析当前运行使用的数据源"""
    configured_provider = get_config_value(config, "data_source", "default_provider", default="akshare")

    if args.provider:
        return args.provider

    is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if is_interactive and args.command in {"init", "select", "run", "web", "calendar"}:
        return prompt_for_provider(configured_provider)

    return configured_provider


def _default_tushare_token(config):
    token = os.getenv("TUSHARE_TOKEN")
    if token:
        return token.strip(), "环境变量 TUSHARE_TOKEN"

    token = get_config_value(config, "data_source", "tushare", "token")
    if token:
        return str(token).strip(), "本机配置"

    return None, None


def resolve_tushare_token(config, interactive_prompt=False):
    """解析 Tushare Token，交互模式可选择默认 Token 或手动输入"""
    default_token, default_source = _default_tushare_token(config)

    if interactive_prompt and default_token:
        print("\n请选择 Tushare Token：")
        print(f"  1. 使用默认 Token（{default_source}，内容已隐藏）")
        print("  2. 手动输入 Token")
        while True:
            choice = input("输入 1 或 2 (默认: 1): ").strip() or "1"
            if choice == "1":
                return default_token
            if choice == "2":
                manual_token = getpass.getpass("请输入 Tushare Token: ").strip()
                if manual_token:
                    return manual_token
                print("Token 不能为空。")
                continue
            print("请输入 1 或 2。")

    if default_token:
        return default_token

    if interactive_prompt:
        token = getpass.getpass("请输入 Tushare Token: ").strip()
        if token:
            return token

    return None


class QuantSystem:
    """量化系统主类"""
    
    def __init__(self, config_file="config/config.yaml", provider_name="akshare", provider_token=None):
        self.config = load_config_file(config_file)
        self.data_dir = self.config.get('data_dir', 'data')
        self.csv_manager = CSVManager(active_data_dir(self.data_dir))
        self.provider_name = provider_name
        self.fetcher = create_data_provider(
            provider_name=provider_name,
            data_dir=self.data_dir,
            config=self.config,
            token=provider_token,
        )
        self.notifier = self._init_notifier()
        self.registry = get_registry("config/strategy_params.yaml")
        self._strategies_loaded = False

    def _refresh_active_csv_manager(self):
        self.csv_manager = CSVManager(active_data_dir(self.data_dir))

    def _activate_fetcher_provider(self):
        summary = warehouse_summary(self.data_dir, self.provider_name)
        if summary.get("stock_count", 0) > 0 and (summary.get("coverage_ratio") or 0) >= 0.98:
            activate_provider(self.data_dir, self.provider_name, summary)
        self._refresh_active_csv_manager()
    
    def _init_notifier(self):
        """初始化通知器"""
        webhook = self.config.get('dingtalk', {}).get('webhook_url')
        secret = self.config.get('dingtalk', {}).get('secret')
        return DingTalkNotifier(webhook, secret)

    def _notifications_enabled(self):
        """是否启用钉钉通知。默认关闭，避免测试时阻塞主流程。"""
        return bool(self.config.get('dingtalk', {}).get('enabled', False))

    def _ensure_strategies_loaded(self):
        """确保策略已动态注册"""
        if self._strategies_loaded:
            return
        self.registry.auto_register_from_directory("strategy")
        self._strategies_loaded = True

    def get_available_strategy_names(self):
        """获取当前可用策略名称列表"""
        self._ensure_strategies_loaded()
        return self.registry.list_strategies()

    def _resolve_selected_strategies(self, strategy_filter='all'):
        """按名称过滤要执行的策略"""
        self._ensure_strategies_loaded()
        available = self.registry.list_strategies()
        if not available:
            return []

        if strategy_filter in (None, 'all'):
            return [(name, self.registry.strategies[name]) for name in available]

        if strategy_filter not in self.registry.strategies:
            raise ValueError(f"未找到策略: {strategy_filter}。当前可用策略: {', '.join(available)}")

        return [(strategy_filter, self.registry.strategies[strategy_filter])]

    def _resolve_target_universe(self, board='all', max_stocks=None):
        """根据 provider、板块和数量限制计算本次目标股票池"""
        target_universe = self.fetcher.get_target_universe(board=board, max_stocks=max_stocks)
        if not target_universe:
            print(f"✗ 未找到可用股票池: {BOARD_LABELS.get(board, board)}")
            return []

        print("\n🎯 目标股票池")
        print(f"  数据源: {self.provider_name}")
        print(f"  板块: {BOARD_LABELS.get(board, board)}")
        print(f"  股票数: {len(target_universe)} 只")
        return target_universe

    def _sync_target_universe(self, board='all', max_stocks=None, purpose='init'):
        """按目标股票池执行智能续抓"""
        target_universe = self._resolve_target_universe(board=board, max_stocks=max_stocks)
        if not target_universe:
            return []
        self.fetcher.sync_target_data(
            target_universe,
            board=board,
            max_stocks=max_stocks,
            purpose=purpose,
        )
        self._activate_fetcher_provider()
        return target_universe
    
    def _load_stock_names(self, stock_data):
        """加载股票名称（优先从CSV文件）"""
        names_file = Path(self.csv_manager.data_dir) / 'stock_names.json'

        # 优先使用本地缓存，避免每次选股都额外请求远端接口
        if names_file.exists():
            import json
            with open(names_file, 'r', encoding='utf-8') as f:
                return json.load(f)

        # 本地不存在时，再尝试从数据源获取
        try:
            stock_names = self.fetcher.get_all_stock_codes()
            if stock_names:
                import json
                with open(names_file, 'w', encoding='utf-8') as f:
                    json.dump(stock_names, f, ensure_ascii=False)
                return stock_names
        except Exception:
            pass
        
        # 使用默认名称
        return {code: f"股票{code}" for code in stock_data.keys()}
    
    def init_data(self, max_stocks=None, board='all'):
        """首次全量抓取"""
        print("=" * 60)
        print(f"🚀 首次全量数据抓取 [{self.provider_name}]")
        print("=" * 60)
        self._sync_target_universe(board=board, max_stocks=max_stocks, purpose='init')
        print("\n✓ 数据初始化完成")

    def update_data(self, max_stocks=None, board='all'):
        """每日增量更新"""
        print("=" * 60)
        print(f"🔄 每日增量更新 [{self.provider_name}]")
        print("=" * 60)
        self._sync_target_universe(board=board, max_stocks=max_stocks, purpose='run')
        print("\n✓ 数据更新完成")

    def _get_selection_settings(self):
        """读取选股执行配置。默认开启并行模式，以提升大股票池筛选速度。"""
        raw_mode = str(get_config_value(self.config, 'selection', 'mode', default='parallel')).strip().lower()
        mode = raw_mode if raw_mode in {'parallel', 'sequential'} else 'parallel'

        raw_backend = str(get_config_value(self.config, 'selection', 'backend', default='process')).strip().lower()
        backend = raw_backend if raw_backend in {'process', 'thread', 'sequential'} else 'process'

        default_workers = min(max(os.cpu_count() or 4, 1), 12)
        raw_workers = get_config_value(self.config, 'selection', 'max_workers', default=default_workers)
        try:
            max_workers = int(raw_workers)
        except (TypeError, ValueError):
            max_workers = default_workers
        max_workers = max(1, min(max_workers, 32))

        raw_chunk_size = get_config_value(self.config, 'selection', 'chunk_size', default=50)
        try:
            chunk_size = int(raw_chunk_size)
        except (TypeError, ValueError):
            chunk_size = 50
        chunk_size = max(1, min(chunk_size, 500))

        return {
            'mode': mode,
            'backend': backend,
            'max_workers': max_workers,
            'chunk_size': chunk_size,
        }

    @staticmethod
    def _is_invalid_stock_name(name):
        """统一处理退市/ST 股票过滤规则。"""
        return is_invalid_stock_name(name)

    @staticmethod
    def _merge_indicator_frames(base_df, frames):
        """把多个策略算出的指标列合并到同一份 DataFrame，便于后续画图复用。"""
        merged = base_df.copy()
        for frame in frames:
            if frame is None or frame.empty:
                continue
            for column in frame.columns:
                if column not in merged.columns:
                    merged[column] = frame[column].values
        return merged

    @staticmethod
    def _chunk_candidates(candidates, chunk_size):
        return [candidates[i:i + chunk_size] for i in range(0, len(candidates), chunk_size)]

    def _resolve_selection_backend(self, candidate_count, settings):
        """根据股票池规模与策略支持情况决定最终执行后端。"""
        if settings['mode'] != 'parallel' or candidate_count <= 1:
            return 'sequential'

        requested_backend = settings['backend']
        if requested_backend == 'sequential':
            return 'sequential'

        if requested_backend == 'process':
            if candidate_count < max(settings['chunk_size'] * 4, 200):
                return 'thread'
            return 'process'

        if candidate_count < max(settings['chunk_size'], 40):
            return 'sequential'
        return 'thread'

    def _analyze_single_stock(self, code, name, selected_strategies, category='all', return_data=False):
        """单只股票的多策略分析入口。"""
        df = self.csv_manager.read_stock_for_analysis(code)
        if df.empty or len(df) < 60:
            return {
                'code': code,
                'name': name,
                'status': 'skipped',
                'signals_by_strategy': {},
                'indicator_df': None,
                'errors': [],
            }

        signals_by_strategy = {}
        indicator_frames = []
        errors = []

        for strategy_name, strategy in selected_strategies:
            try:
                df_with_indicators = strategy.calculate_indicators(df)
                signal_list = strategy.select_stocks(df_with_indicators, name)
            except Exception as exc:
                errors.append(f"{strategy_name}: {exc}")
                continue

            filtered_signals = []
            for signal in signal_list or []:
                signal_category = signal.get('category', 'unknown')
                if category == 'all' or signal_category == category:
                    filtered_signals.append(signal)

            if filtered_signals:
                signals_by_strategy[strategy_name] = filtered_signals
                if return_data:
                    indicator_frames.append(df_with_indicators)

        indicator_df = None
        if return_data and indicator_frames:
            indicator_df = self._merge_indicator_frames(df, indicator_frames)

        return {
            'code': code,
            'name': name,
            'status': 'ok',
            'signals_by_strategy': signals_by_strategy,
            'indicator_df': indicator_df,
            'errors': errors,
        }

    def select_stocks(self, category='all', max_stocks=None, return_data=False, board='all', target_universe=None, strategy_filter='all'):
        """执行选股
        :param category: 股票分类筛选，'all'表示全部，其他值按分类筛选
        :param max_stocks: 限制处理的股票数量（用于快速测试）
        :param return_data: 是否返回股票数据字典（用于K线图生成）
        :return: (results, stock_names) 或 (results, stock_names, stock_data_dict)
        """
        print("=" * 60)
        print("🎯 执行选股策略")
        if max_stocks:
            print(f"   快速测试模式：只处理前 {max_stocks} 只股票")
        print("=" * 60)
        
        # 加载策略
        print("\n加载策略...")
        selected_strategies = self._resolve_selected_strategies(strategy_filter=strategy_filter)

        if not selected_strategies:
            print("✗ 没有找到可用策略")
            return {}, {}
        
        print(f"已选中 {len(selected_strategies)} 个策略")
        
        # 输出当前策略参数
        print("\n当前策略参数:")
        for strategy_name, strategy_obj in selected_strategies:
            print(f"\n  🎯 {strategy_name}:")
            for param_name, param_value in strategy_obj.params.items():
                # 对特定参数添加说明
                note = ""
                if param_name == 'N':
                    note = " (成交量倍数)"
                elif param_name == 'M':
                    note = " (回溯天数)"
                elif param_name == 'CAP':
                    note = f" ({param_value/1e8:.0f}亿市值门槛)"
                elif param_name == 'J_VAL':
                    note = " (J值上限)"
                elif param_name in ['M1', 'M2', 'M3', 'M4']:
                    note = " (MA周期)"
                print(f"      {param_name}: {param_value}{note}")

        if target_universe is None:
            target_universe = self._resolve_target_universe(board=board, max_stocks=max_stocks)
        stock_codes = [item['code'] for item in target_universe]

        if not stock_codes:
            print("✗ 目标股票池为空，请先执行 init")
            return {}, {}

        selection_settings = self._get_selection_settings()
        selected_strategy_names = [strategy_name for strategy_name, _ in selected_strategies]
        execution_backend = self._resolve_selection_backend(
            len(stock_codes),
            selection_settings,
        )
        worker_count = selection_settings['max_workers']
        chunk_size = selection_settings['chunk_size']

        backend_labels = {
            'process': '进程批处理',
            'thread': '线程批处理',
            'sequential': '顺序处理',
        }
        print(f"\n执行选股（{backend_labels.get(execution_backend, execution_backend)}，优先提升吞吐）...")
        print(f"目标股票池共 {len(stock_codes)} 只股票")

        # 先获取股票名称
        stock_names = self._load_stock_names({})

        # 先按名称做预过滤，避免无意义读取 CSV
        candidates = []
        invalid_name_count = 0
        for code in stock_codes:
            name = stock_names.get(code, '未知')
            if self._is_invalid_stock_name(name):
                invalid_name_count += 1
                continue
            candidates.append((code, name))

        print(f"名称预过滤后剩余 {len(candidates)} 只股票，跳过 {invalid_name_count} 只风险/异常股票")

        results = {strategy_name: [] for strategy_name, _ in selected_strategies}
        indicators_dict = {}  # 只保存入选股票的数据
        category_count = {}
        strategy_valid_counts = {strategy_name: 0 for strategy_name, _ in selected_strategies}
        strategy_error_counts = {strategy_name: 0 for strategy_name, _ in selected_strategies}
        skipped_data_count = 0
        valid_total_count = 0
        completed = 0
        tracker = ProgressTracker(len(candidates) or 1, label="选股进度")

        def consume_chunk(chunk_result):
            nonlocal skipped_data_count, completed, valid_total_count
            completed += chunk_result['processed_count']
            skipped_data_count += chunk_result['skipped_count']
            valid_total_count += chunk_result['valid_count']

            for strategy_name in strategy_valid_counts:
                strategy_valid_counts[strategy_name] += chunk_result['valid_count']
                strategy_error_counts[strategy_name] += chunk_result['error_counts'].get(strategy_name, 0)
                results[strategy_name].extend(chunk_result['results_by_strategy'].get(strategy_name, []))

            if return_data:
                indicators_dict.update(chunk_result['indicators_dict'])

            for category_key, count in chunk_result['category_count'].items():
                category_count[category_key] = category_count.get(category_key, 0) + count

            selected_total = sum(len(items) for items in results.values())
            print("  " + tracker.line(
                completed,
                extra=(
                    f"有效 {valid_total_count} 只 | "
                    f"已选出 {selected_total} 只"
                )
            ))

        candidate_chunks = self._chunk_candidates(candidates, chunk_size)
        effective_workers = min(worker_count, max(len(candidate_chunks), 1))

        if execution_backend != 'sequential':
            print(f"工作单元数: {effective_workers}")
            print(f"批次大小: {chunk_size}")

        if execution_backend == 'process':
            with ProcessPoolExecutor(
                max_workers=effective_workers,
                initializer=initialize_selection_worker,
                initargs=(str(self.csv_manager.data_dir), selected_strategy_names, str(self.registry.params_file)),
            ) as executor:
                futures = [
                    executor.submit(
                        process_selection_chunk,
                        chunk,
                        category,
                        return_data,
                    )
                    for chunk in candidate_chunks
                ]
                for future in as_completed(futures):
                    consume_chunk(future.result())
        elif execution_backend == 'thread':
            worker_context = build_worker_context(
                str(self.csv_manager.data_dir),
                selected_strategy_names,
                str(self.registry.params_file),
            )
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = [
                    executor.submit(
                        process_selection_chunk,
                        chunk,
                        category,
                        return_data,
                        worker_context,
                    )
                    for chunk in candidate_chunks
                ]
                for future in as_completed(futures):
                    consume_chunk(future.result())
        else:
            worker_context = build_worker_context(
                str(self.csv_manager.data_dir),
                selected_strategy_names,
                str(self.registry.params_file),
            )
            for chunk in candidate_chunks:
                consume_chunk(process_selection_chunk(
                    chunk,
                    category,
                    return_data,
                    worker_context,
                ))

        for strategy_name in results:
            results[strategy_name] = sorted(results[strategy_name], key=lambda item: item['code'])

        for strategy_name, signals in results.items():
            print(f"\n执行策略: {strategy_name}")
            print(
                f"  ✓ 选股完成: 共 {len(signals)} 只 "
                f"(有效 {strategy_valid_counts.get(strategy_name, 0)} 只, "
                f"名称过滤 {invalid_name_count} 只, 数据不足 {skipped_data_count} 只, "
                f"策略异常 {strategy_error_counts.get(strategy_name, 0)} 次)"
            )
        print(f"\n总耗时: {tracker.elapsed_text()}")

        # 显示结果汇总
        print("\n" + "=" * 60)
        print("📊 选股结果汇总")
        print("=" * 60)
        
        for strategy_name, signals in results.items():
            print(f"\n{strategy_name}: {len(signals)} 只")
            for signal in signals:
                code = signal['code']
                name = signal.get('name', stock_names.get(code, '未知'))
                for s in signal['signals']:
                    cat_emoji = category_label(s.get('category')).split(' ', 1)[0]
                    print(f"  {cat_emoji} {code} {name}: 价格={s['close']}, J={s['J']}, 理由={s['reasons']}")
        
        # 显示分类统计
        print("\n" + "-" * 60)
        print("分类统计:")
        if category_count:
            known = [key for key in CATEGORY_DISPLAY_ORDER if key in category_count]
            extras = sorted(key for key in category_count if key not in CATEGORY_DISPLAY_ORDER)
            for key in known + extras:
                print(f"  {category_label(key)}: {category_count[key]} 只")
        else:
            print("  无分类结果")
        print("-" * 60)
        
        # 如果需要返回数据字典（用于K线图生成）
        if return_data:
            # 返回计算了指标的数据（包含趋势线）
            return results, stock_names, indicators_dict
        
        return results, stock_names
    
    def run_full(self, category='all', max_stocks=None, board='all'):
        """完整流程：更新 + 选股 + 通知（带K线图）
        :param max_stocks: 限制处理的股票数量（用于快速测试）
        """

        print("=" * 60)
        print("🚀 执行完整流程")
        if max_stocks:
            print(f"   快速测试模式：只处理前 {max_stocks} 只股票")
        print(f"   板块范围: {BOARD_LABELS.get(board, board)}")
        print("=" * 60)

        # 1. 智能续抓目标股票池
        target_universe = self._sync_target_universe(board=board, max_stocks=max_stocks, purpose='run')

        # 2. 选股（返回数据和结果）
        need_stock_data = self._notifications_enabled()
        selection_result = self.select_stocks(
            category=category,
            max_stocks=max_stocks,
            return_data=need_stock_data,
            board=board,
            target_universe=target_universe,
            strategy_filter='all',
        )
        if need_stock_data:
            results, stock_names, stock_data_dict = selection_result
        else:
            results, stock_names = selection_result
            stock_data_dict = {}

        # 3. 发送通知（带K线图）
        if results:
            if self._notifications_enabled():
                default_strategy = self.registry.strategies.get('BowlReboundStrategy')
                # 使用带K线图的发送方法
                self.notifier.send_stock_selection_with_charts(
                    results,
                    stock_names,
                    category_filter=category,
                    stock_data_dict=stock_data_dict,
                    params=default_strategy.params if default_strategy else {},
                    send_text_first=True
                )
            else:
                print("\n🔕 钉钉通知已禁用，跳过发送")

        return results
    
    def select_with_b1_match(self, category='all', max_stocks=None, min_similarity=None, lookback_days=None, board='all', target_universe=None, strategy_filter='all'):
        """
        执行选股 + B1完美图形匹配排序
        
        Args:
            category: 股票分类筛选，'all'表示全部
            max_stocks: 限制处理的股票数量
            min_similarity: 最小相似度阈值，低于此值不显示
            lookback_days: 回看天数，默认25天
            
        Returns:
            dict: 包含选股结果和匹配结果
        """
        # 从配置读取默认值
        from strategy.pattern_config import MIN_SIMILARITY_SCORE, DEFAULT_LOOKBACK_DAYS
        if min_similarity is None:
            min_similarity = MIN_SIMILARITY_SCORE
        if lookback_days is None:
            lookback_days = DEFAULT_LOOKBACK_DAYS
        
        print("=" * 60)
        print("🎯 执行选股 + B1完美图形匹配")
        if max_stocks:
            print(f"   快速测试模式：只处理前 {max_stocks} 只股票")
        print(f"   板块范围: {BOARD_LABELS.get(board, board)}")
        print(f"   相似度阈值: {min_similarity}%")
        print(f"   回看天数: {lookback_days}天")
        print("=" * 60)
        
        # 1. 先执行原有选股逻辑
        print("\n[1/3] 执行策略选股...")
        results, stock_names, stock_data_dict = self.select_stocks(
            category=category, 
            max_stocks=max_stocks, 
            return_data=True,
            board=board,
            target_universe=target_universe,
            strategy_filter=strategy_filter,
        )
        
        # 统计选股总数
        total_selected = sum(len(signals) for signals in results.values())
        if total_selected == 0:
            print("\n✗ 策略未选出任何股票，跳过匹配")
            return {'results': results, 'stock_names': stock_names, 'matched': []}
        
        print(f"\n✓ 策略选出 {total_selected} 只股票")
        
        # 2. 初始化B1完美图形库
        print("\n[2/3] 初始化B1完美图形库...")
        try:
            from strategy.pattern_library import B1PatternLibrary
            from strategy.pattern_config import MIN_SIMILARITY_SCORE
            
            library = B1PatternLibrary(self.csv_manager)
            
            if not library.cases:
                print("⚠️ 警告: 案例库为空，可能数据不足")
                return {'results': results, 'stock_names': stock_names, 'matched': []}
            
            print(f"✓ 案例库加载完成: {len(library.cases)} 个案例")
            
        except Exception as e:
            print(f"✗ 初始化案例库失败: {e}")
            import traceback
            traceback.print_exc()
            return {'results': results, 'stock_names': stock_names, 'matched': []}
        
        # 3. 对每只候选股进行匹配
        print("\n[3/3] 执行B1完美图形匹配...")
        matched_results = []
        
        for strategy_name, signals in results.items():
            for signal in signals:
                code = signal['code']
                name = signal.get('name', stock_names.get(code, '未知'))
                
                # 获取该股票的完整数据
                if code not in stock_data_dict:
                    continue
                
                df = stock_data_dict[code]
                if df.empty:
                    continue
                
                try:
                    # 匹配最佳案例（使用指定回看天数）
                    match_result = library.find_best_match(code, df, lookback_days=lookback_days)
                    
                    if match_result.get('best_match'):
                        best = match_result['best_match']
                        score = best.get('similarity_score', 0)
                        
                        # 只保留超过阈值的股票
                        if score >= min_similarity:
                            # 获取第一个信号的信息
                            s = signal['signals'][0] if signal.get('signals') else {}
                            
                            matched_results.append({
                                'stock_code': code,
                                'stock_name': name,
                                'strategy': strategy_name,
                                'category': s.get('category', 'unknown'),
                                'close': s.get('close', '-'),
                                'J': s.get('J', '-'),
                                'similarity_score': score,
                                'matched_case': best.get('case_name', ''),
                                'matched_date': best.get('case_date', ''),
                                'matched_code': best.get('case_code', ''),
                                'breakdown': best.get('breakdown', {}),
                                'tags': best.get('tags', []),
                                'all_matches': best.get('all_matches', []),
                            })
                            
                except Exception as e:
                    print(f"  ⚠️ 匹配 {code} 失败: {e}")
                    continue
        
        # 按相似度排序
        matched_results.sort(key=lambda x: x['similarity_score'], reverse=True)
        
        print(f"\n✓ 匹配完成: {len(matched_results)} 只股票超过阈值")
        
        # 显示Top N结果（使用配置）
        from strategy.pattern_config import TOP_N_RESULTS
        if matched_results:
            print("\n" + "=" * 60)
            print(f"📊 Top {TOP_N_RESULTS} B1完美图形匹配结果")
            print("=" * 60)
            for i, r in enumerate(matched_results[:TOP_N_RESULTS], 1):
                emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                print(f"{emoji} {r['stock_code']} {r['stock_name']}")
                print(f"   相似度: {r['similarity_score']}% | 匹配: {r['matched_case']}")
                bd = r.get('breakdown', {})
                print(f"   趋势:{bd.get('trend_structure', 0)}% "
                      f"KDJ:{bd.get('kdj_state', 0)}% "
                      f"量能:{bd.get('volume_pattern', 0)}% "
                      f"形态:{bd.get('price_shape', 0)}%")
        
        return {
            'results': results,
            'stock_names': stock_names,
            'matched': matched_results,
            'total_selected': total_selected,
        }
    
    def run_with_b1_match(self, category='all', max_stocks=None, min_similarity=60.0, lookback_days=25, board='all', strategy_filter='all'):
        """
        完整流程：更新 + 选股 + B1完美图形匹配 + 通知

        Args:
            category: 股票分类筛选
            max_stocks: 限制处理的股票数量
            min_similarity: 最小相似度阈值
            lookback_days: 回看天数，默认25天
        """
        from datetime import datetime

        print("=" * 60)
        print("🚀 执行完整流程（含B1完美图形匹配）")
        if max_stocks:
            print(f"   快速测试模式：只处理前 {max_stocks} 只股票")
        print(f"   板块范围: {BOARD_LABELS.get(board, board)}")
        print(f"   回看天数: {lookback_days}天")
        print("=" * 60)

        # 1. 智能续抓目标股票池
        target_universe = self._sync_target_universe(board=board, max_stocks=max_stocks, purpose='run')

        # 2. 选股 + B1完美图形匹配
        match_result = self.select_with_b1_match(
            category=category,
            max_stocks=max_stocks,
            min_similarity=min_similarity,
            lookback_days=lookback_days,
            board=board,
            target_universe=target_universe,
            strategy_filter=strategy_filter,
        )
        
        # 3. 发送通知
        if match_result.get('matched'):
            if self._notifications_enabled():
                print("\n📤 发送钉钉通知...")
                self.notifier.send_b1_match_results(
                    match_result['matched'],
                    match_result.get('total_selected', 0)
                )
                print("✓ 通知发送完成")
            else:
                print("\n🔕 钉钉通知已禁用，跳过发送")
        else:
            print("\n⚠️ 没有匹配结果，跳过通知")
        
        return match_result

    def select_only(self, category='all', max_stocks=None, board='all', strategy_filter='all', force_select=False):
        """只执行筛选，不自动抓取；若本地数据过期则提醒并可先补齐"""
        print("=" * 60)
        print("🔎 执行独立筛选")
        if max_stocks:
            print(f"   快速测试模式：只处理前 {max_stocks} 只股票")
        print(f"   板块范围: {BOARD_LABELS.get(board, board)}")
        print("=" * 60)

        target_universe = self._resolve_target_universe(board=board, max_stocks=max_stocks)
        if not target_universe:
            return {}

        freshness = self.fetcher.assess_target_data(target_universe)
        summary = freshness["summary"]
        print("\n🗂️ 本地数据状态")
        print(f"  最新交易日: {freshness.get('latest_trade_date') or '未知'}")
        print(f"  已最新: {summary.get('up_to_date', 0)} 只")
        print(f"  过期待补: {summary.get('stale', 0)} 只")
        print(f"  缺失/损坏: {summary.get('full_refresh', 0)} 只")

        if not freshness["is_fresh"] and not force_select:
            is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
            message = "检测到本地数据不是最新，是否先抓取/补齐目标股票池再筛选？"
            if is_interactive and prompt_yes_no(message, default=True):
                self.fetcher.sync_target_data(target_universe, board=board, max_stocks=max_stocks, purpose='run')
                self._activate_fetcher_provider()
            else:
                print("⚠️ 本地数据不是最新，已停止筛选。可使用 `select --force-select` 强制按现有数据筛选。")
                return {}
        elif not freshness["is_fresh"] and force_select:
            print("⚠️ 已启用强制筛选，将直接使用当前本地数据继续执行")

        return self.select_stocks(
            category=category,
            max_stocks=max_stocks,
            return_data=False,
            board=board,
            target_universe=target_universe,
            strategy_filter=strategy_filter,
        )
    
    def run_schedule(self):
        """启动定时调度"""
        try:
            import schedule
        except ImportError:
            print("✗ 请安装 schedule: pip install schedule")
            return
        
        schedule_time = self.config.get('schedule', {}).get('time', '15:05')
        
        print("=" * 60)
        print(f"⏰ 启动定时调度")
        print(f"   每日 {schedule_time} 执行选股任务")
        print("=" * 60)
        
        # 设置定时任务
        schedule.every().day.at(schedule_time).do(self.run_full)
        
        print("\n按 Ctrl+C 停止")
        
        while True:
            schedule.run_pending()
            time.sleep(60)


def print_version():
    """打印版本信息"""
    import pandas
    import importlib
    
    print(f"A-Share Quant v{__version__}")
    print(f"Python: {sys.version.split()[0]}")
    if importlib.util.find_spec("akshare"):
        import akshare
        print(f"akshare: {akshare.__version__}")
    else:
        print("akshare: 未安装")
    if importlib.util.find_spec("tushare"):
        import tushare
        print(f"tushare: {tushare.__version__}")
    else:
        print("tushare: 未安装")
    print(f"pandas: {pandas.__version__}")
    print(f"System: {platform.system()}")
    print(f"B1 Pattern Match: 支持（基于双线+量比+形态三维匹配，10个历史案例）")


def print_calendar_status(status):
    """打印交易日历缓存状态"""
    years = ", ".join(status.get("years", [])) or "无"
    latest_cached_date = status.get("latest_cached_date") or "无"
    print("=" * 60)
    print("📅 交易日历缓存状态")
    print(f"   Provider: {status.get('provider', 'unknown')}")
    print(f"   缓存可用: {'是' if status.get('cache_available') else '否'}")
    print(f"   已缓存年份: {years}")
    print(f"   缓存截至: {latest_cached_date}")
    print(f"   来源: {status.get('source', 'unknown')}")
    print("=" * 60)


def print_export_result(result):
    code = result.get("code", "--")
    name = result.get("name", "")
    print("=" * 60)
    print("📤 股票 CSV 导出")
    print(f"   股票: {code} {name}".rstrip())
    if result.get("freshness"):
        freshness = result["freshness"]
        print(f"   本地最新: {freshness.get('local_latest_date') or '无'}")
        print(f"   目标交易日: {freshness.get('latest_trade_date') or '未知'}")
    print(f"   文件: {result.get('path')}")
    print("=" * 60)


def prompt_export_stale_choice(result):
    """过期数据导出时的终端选择。"""
    print("⚠️ 检测到本地 CSV 不是最新。")
    print(f"   {result.get('message')}")
    print("\n请选择：")
    print("  1. 先用当前 active provider 单独更新这只股票，再导出")
    print("  2. 直接导出当前本地 CSV，不管是否最新")
    while True:
        choice = input("输入 1 或 2 (默认: 1): ").strip() or "1"
        if choice in {"1", "2"}:
            return choice
        print("请输入 1 或 2。")


def main():
    parser = argparse.ArgumentParser(
        description='A股量化选股系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py init                          # 首次抓取6年历史数据
  python main.py init --board main             # 同步主板股票池
  python main.py select --strategy B1V242BStrategy  # 用指定策略直接筛选本地数据
  python main.py run                           # 完整流程（更新+选股+通知）
  python main.py run --board star              # 只运行科创板股票池
  python main.py run --provider tushare        # 使用 tushare 数据源执行完整流程
  python main.py run --b1-match                # 完整流程+B1完美图形匹配排序
  python main.py run --b1-match --min-similarity 70  # 匹配+提高相似度阈值到70%
  python main.py run --b1-match --lookback-days 30   # 使用30天回看期
  python main.py web                           # 启动Web界面
  python main.py calendar --provider tushare   # 查看交易日历缓存状态
  python main.py calendar --provider tushare --update --years 2026 2027
  python main.py export 300888                 # 导出单只股票 CSV 到 Downloads
  python main.py export tqly --update-first     # 先用 tushare 更新匹配股票再导出
  python main.py --version                     # 显示版本信息

分类说明:
  all              - 全部（回落碗中 + 靠近多空线 + 靠近短期趋势线）
  bowl_center      - 回落碗中（优先级最高）
  near_duokong     - 靠近多空线（±duokong_pct%，默认3%）
  near_short_trend - 靠近短期趋势线（±short_pct%，默认2%）

B1完美图形匹配:
  基于10个历史成功案例（双线+量比+形态三维相似度匹配）
  使用 --b1-match 参数启用，--lookback-days 调整回看天数（默认25天）
  使用 --min-similarity 调整匹配阈值（默认60%，范围0-100）
        """
    )

    parser.add_argument(
        '--version',
        action='store_true',
        help='显示版本信息并退出'
    )

    parser.add_argument(
        'command',
        choices=['init', 'select', 'run', 'web', 'calendar', 'doctor', 'export'],
        nargs='?',
        help='要执行的命令: init(初始化数据), select(仅筛选), run(更新+筛选), web(启动Web服务器), calendar(查看/更新交易日历缓存), doctor(健康检查), export(导出单股CSV)'
    )

    parser.add_argument(
        'stock_query',
        nargs='?',
        help='export 命令使用：股票代码、名称或拼音首字母，例如 300888 或 tqly'
    )

    parser.add_argument(
        '--max-stocks',
        type=int,
        default=None,
        help='限制处理的股票数量；若配合 --board 使用，则先按板块过滤再截取前 N 只'
    )

    parser.add_argument(
        '--config',
        default='config/config.yaml',
        help='配置文件路径'
    )

    parser.add_argument(
        '--board',
        choices=['all', 'main', 'chinext', 'star'],
        default='all',
        help='股票池范围: all(全市场), main(主板), chinext(创业板), star(科创板)'
    )

    parser.add_argument(
        '--provider',
        choices=['akshare', 'tushare', 'tencent'],
        default=None,
        help='指定数据源，不指定时交互式终端会先询问'
    )

    parser.add_argument(
        '--strategy',
        default=None,
        help='指定筛选策略名称；不指定时交互式终端可选，默认执行全部策略'
    )

    parser.add_argument(
        '--force-select',
        action='store_true',
        help='在 select 命令中强制使用当前本地数据筛选，即使数据不是最新'
    )

    parser.add_argument(
        '--update-first',
        action='store_true',
        help='在 export 命令中先用 Tushare 单独更新该股票，再导出 CSV'
    )

    parser.add_argument(
        '--force-export',
        action='store_true',
        help='在 export 命令中忽略数据是否最新，直接导出当前本地 CSV'
    )

    parser.add_argument(
        '--host',
        default=None,
        help='Web服务器监听地址 (默认从配置读取，未配置时为 127.0.0.1)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='Web服务器端口 (默认从配置读取，未配置时为 5080)'
    )

    parser.add_argument(
        '--update',
        action='store_true',
        help='在 calendar 命令中主动更新交易日历缓存'
    )

    parser.add_argument(
        '--years',
        type=int,
        nargs='*',
        default=None,
        help='calendar 命令要查看/更新的年份列表，例如: --years 2026 2027'
    )
    
    parser.add_argument(
        '--category',
        type=str,
        choices=['all'] + CATEGORY_DISPLAY_ORDER,
        default='all',
        help='筛选股票分类: all(全部) 或具体策略分类'
    )
    
    # 从配置读取B1PatternMatch默认值
    try:
        from strategy.pattern_config import MIN_SIMILARITY_SCORE, DEFAULT_LOOKBACK_DAYS
        default_min_similarity = MIN_SIMILARITY_SCORE
        default_lookback_days = DEFAULT_LOOKBACK_DAYS
    except:
        default_min_similarity = 60.0
        default_lookback_days = 25
    
    parser.add_argument(
        '--min-similarity',
        type=float,
        default=None,
        help=f'B1完美图形匹配的最小相似度阈值 (默认: {default_min_similarity})'
    )
    
    parser.add_argument(
        '--b1-match',
        action='store_true',
        help='启用B1完美图形匹配排序（在run命令中使用）'
    )
    
    parser.add_argument(
        '--lookback-days',
        type=int,
        default=None,
        help=f'B1完美图形匹配的回看天数 (默认: {default_lookback_days})'
    )

    parser.add_argument(
        '--offline',
        action='store_true',
        help='doctor 命令默认离线运行；保留该参数用于显式表达'
    )

    parser.add_argument(
        '--full-local',
        action='store_true',
        help='doctor 命令执行本地全量 CSV 选股验证'
    )

    parser.add_argument(
        '--provider-smoke',
        choices=['akshare', 'tushare', 'tencent'],
        default=None,
        help='doctor 命令执行小批联网数据源验证'
    )

    parser.add_argument(
        '--max-network-stocks',
        type=int,
        default=3,
        help='doctor 联网 smoke 最多抽样股票数'
    )

    parser.add_argument(
        '--doctor-timeout',
        type=int,
        default=600,
        help='doctor --full-local 超时时间（秒）'
    )

    args = parser.parse_args()

    # 处理 --version 参数
    if args.version:
        print_version()
        sys.exit(0)

    # 检查命令是否提供
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # 切换工作目录
    os.chdir(project_root)

    config = load_config_file(args.config)
    provider_name = resolve_provider_name(args, config)
    provider_token = None
    if provider_name == 'tushare' and args.command in {'init', 'select', 'run', 'calendar'}:
        provider_token = resolve_tushare_token(
            config,
            interactive_prompt=(sys.stdin.isatty() and sys.stdout.isatty())
        )
        if not provider_token:
            print("✗ 未提供 Tushare Token，无法使用 tushare 数据源")
            print("  可通过环境变量 TUSHARE_TOKEN、config/config_local.yaml 或 config/config.yaml 配置")
            sys.exit(1)
    
    # 执行命令
    try:
        if args.command == 'doctor':
            from utils.doctor import run_doctor
            return_code = run_doctor(
                project_root=project_root,
                full_local=args.full_local,
                provider_smoke=args.provider_smoke,
                max_network_stocks=args.max_network_stocks,
                timeout_seconds=args.doctor_timeout,
            )
            sys.exit(return_code)

        if args.command == 'export':
            if not args.stock_query:
                print("✗ export 命令需要股票代码、名称或拼音首字母，例如: python main.py export tqly")
                sys.exit(1)
            if args.update_first and args.force_export:
                print("✗ --update-first 与 --force-export 不能同时使用")
                sys.exit(1)

            from utils.stock_exporter import StockExportService

            service = StockExportService(
                data_dir=str(config.get('data_dir', 'data')),
                config=config,
            )
            result = service.export_stock(
                args.stock_query,
                update_first=args.update_first,
                force_export=args.force_export,
            )
            if result.get("needs_update"):
                is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
                if is_interactive:
                    choice = prompt_export_stale_choice(result)
                    result = service.export_stock(
                        args.stock_query,
                        update_first=(choice == "1"),
                        force_export=(choice == "2"),
                    )
                else:
                    print(f"⚠️ {result.get('message')}")
                    print("  可运行：")
                    print(f"  python main.py export {args.stock_query} --update-first")
                    print(f"  python main.py export {args.stock_query} --force-export")
                    sys.exit(2)
            print_export_result(result)
            return

        if args.command == 'calendar':
            if provider_name != 'tushare':
                print("⚠️ 当前只有 tushare provider 支持交易日历缓存。")
                print("  若使用 akshare，系统将仅使用本地工作日近似判断，不支持 trade_cal 缓存更新。")
                if args.update:
                    print("✗ `calendar --update` 仅支持 `--provider tushare`")
                    sys.exit(1)
            quant = QuantSystem(args.config, provider_name=provider_name, provider_token=provider_token)
            status = quant.fetcher.get_trade_calendar_status()
            print_calendar_status(status)

            if args.update:
                years = args.years or [datetime.now().year]
                print(f"\n🔄 开始更新交易日历缓存: {', '.join(str(year) for year in years)}")
                updated_status = quant.fetcher.update_trade_calendar_cache(years=years)
                print("✓ 交易日历缓存更新完成")
                print_calendar_status(updated_status)
            return

        if args.command == 'init':
            quant = QuantSystem(args.config, provider_name=provider_name, provider_token=provider_token)
            quant.init_data(max_stocks=args.max_stocks, board=args.board)
        
        elif args.command in {'select', 'run'}:
            quant = QuantSystem(args.config, provider_name=provider_name, provider_token=provider_token)
            available_strategies = quant.get_available_strategy_names()
            strategy_filter = args.strategy
            if not strategy_filter and sys.stdin.isatty() and sys.stdout.isatty():
                strategy_filter = prompt_for_strategy(available_strategies, default_strategy="all")
            strategy_filter = strategy_filter or 'all'

            if strategy_filter != 'all' and strategy_filter not in available_strategies:
                print(f"✗ 未找到策略: {strategy_filter}")
                print(f"  当前可用策略: {', '.join(available_strategies)}")
                sys.exit(1)

            if args.command == 'select':
                quant.select_only(
                    category=args.category,
                    max_stocks=args.max_stocks,
                    board=args.board,
                    strategy_filter=strategy_filter,
                    force_select=args.force_select,
                )
                return

            # 原有选股流程（支持B1完美图形匹配）
            if args.b1_match:
                min_sim = args.min_similarity if args.min_similarity is not None else default_min_similarity
                lookback = args.lookback_days if args.lookback_days is not None else default_lookback_days
                quant.run_with_b1_match(
                    category=args.category,
                    max_stocks=args.max_stocks,
                    min_similarity=min_sim,
                    lookback_days=lookback,
                    board=args.board,
                    strategy_filter=strategy_filter,
                )
            else:
                if strategy_filter == 'all':
                    quant.run_full(category=args.category, max_stocks=args.max_stocks, board=args.board)
                else:
                    # 指定单一策略时，仍沿用 run 的“先更新再筛选”语义
                    print("=" * 60)
                    print("🚀 执行完整流程")
                    if args.max_stocks:
                        print(f"   快速测试模式：只处理前 {args.max_stocks} 只股票")
                    print(f"   板块范围: {BOARD_LABELS.get(args.board, args.board)}")
                    print(f"   指定策略: {strategy_filter}")
                    print("=" * 60)
                    target_universe = quant._sync_target_universe(board=args.board, max_stocks=args.max_stocks, purpose='run')
                    need_stock_data = quant._notifications_enabled()
                    selection_result = quant.select_stocks(
                        category=args.category,
                        max_stocks=args.max_stocks,
                        return_data=need_stock_data,
                        board=args.board,
                        target_universe=target_universe,
                        strategy_filter=strategy_filter,
                    )
                    if quant._notifications_enabled():
                        results_dict, stock_names, stock_data_dict = selection_result
                        strategy_obj = quant.registry.strategies.get(strategy_filter)
                        quant.notifier.send_stock_selection_with_charts(
                            results_dict,
                            stock_names,
                            category_filter=args.category,
                            stock_data_dict=stock_data_dict,
                            params=strategy_obj.params if strategy_obj else {},
                            send_text_first=True
                        )
                    else:
                        print("\n🔕 钉钉通知已禁用，跳过发送")
        
        elif args.command == 'web':
            from web_server import run_web_server
            run_web_server(host=args.host, port=args.port, config=config)
    except DataProviderError as e:
        print(f"✗ {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
