"""
Operational health checks for the local quant selector.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from strategy.strategy_registry import StrategyRegistry
from utils.config_schema import assert_strategy_params_file, load_yaml_file
from utils.csv_manager import CSVManager
from utils.data_provider import create_data_provider, get_config_value
from utils.local_config import load_config_file
from utils.provider_router import active_data_dir, load_active_provider
from utils.selection_worker import initialize_selection_worker, process_selection_chunk
from utils.strategy_labels import fallback_stock_name, is_invalid_stock_name


class Doctor:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.failures = []
        self.warnings = []

    def ok(self, message):
        print(f"OK   {message}")

    def warn(self, message):
        self.warnings.append(message)
        print(f"WARN {message}")

    def fail(self, message):
        self.failures.append(message)
        print(f"FAIL {message}")

    def run(self, full_local=False, provider_smoke=None, max_network_stocks=3, timeout_seconds=600):
        print("=" * 60)
        print("A股量化系统 doctor")
        print("=" * 60)
        self.check_environment()
        config = self.check_config_files()
        strategies = self.check_strategy_registry()
        self.check_local_data_shape(config)
        self.check_web_api_contract()

        if full_local:
            self.check_full_local_selection(config, strategies, timeout_seconds=timeout_seconds)
        if provider_smoke:
            self.check_provider_smoke(config, provider_smoke, max_network_stocks=max_network_stocks)

        print("=" * 60)
        if self.warnings:
            print(f"WARNINGS: {len(self.warnings)}")
        if self.failures:
            print(f"FAILED: {len(self.failures)}")
            return 1
        print("PASSED")
        return 0

    def check_environment(self):
        self.ok(f"python={sys.version.split()[0]}")
        for package in ["akshare", "pandas", "numpy", "flask", "yaml"]:
            if importlib.util.find_spec(package):
                self.ok(f"package {package} importable")
            else:
                self.fail(f"package {package} not importable")

    def check_config_files(self):
        config_path = self.project_root / "config" / "config.yaml"
        strategy_path = self.project_root / "config" / "strategy_params.yaml"

        config = {}
        try:
            config = load_config_file(config_path)
            self.ok("config/config.yaml readable")
        except Exception as exc:
            self.fail(f"config/config.yaml unreadable: {exc}")

        try:
            assert_strategy_params_file(strategy_path)
            self.ok("config/strategy_params.yaml schema valid")
        except Exception as exc:
            self.fail(f"strategy params invalid: {exc}")

        if self._git_tracks("config/config.yaml"):
            self.warn("config/config.yaml is still tracked by git; keep secrets out or remove it from the index")

        local_config_path = self.project_root / "config" / "config_local.yaml"
        if local_config_path.exists():
            self.ok("config/config_local.yaml local override loaded")
            if self._git_ignored("config/config_local.yaml"):
                self.ok("config/config_local.yaml ignored by git")
            else:
                self.fail("config/config_local.yaml exists but is not ignored by git")

        webhook = get_config_value(config, "dingtalk", "webhook_url", default="")
        if webhook and "YOUR_TOKEN_HERE" not in str(webhook):
            self.warn("dingtalk webhook is configured; confirm this local file is not committed with secrets")

        return config

    def _git_tracks(self, path):
        try:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", path],
                cwd=self.project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _git_ignored(self, path):
        try:
            result = subprocess.run(
                ["git", "check-ignore", "-q", path],
                cwd=self.project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False

    def check_strategy_registry(self):
        registry = StrategyRegistry(self.project_root / "config" / "strategy_params.yaml")
        cwd = os.getcwd()
        os.chdir(self.project_root)
        try:
            registry.auto_register_from_directory("strategy")
        finally:
            os.chdir(cwd)

        names = registry.list_strategies()
        if names:
            self.ok(f"strategies registered={len(names)} ({', '.join(names)})")
        else:
            self.fail("no strategies registered")

        errors = registry.get_load_errors()
        if errors:
            for error in errors:
                self.fail(f"strategy load error {error['module']}: {error['error']}")
        else:
            self.ok("strategy load errors=0")
        return names

    def check_local_data_shape(self, config):
        storage_root = self.project_root / str(get_config_value(config, "data_dir", default="data"))
        data_dir = active_data_dir(storage_root)
        active_provider = load_active_provider(storage_root).get("active_provider")
        manager = CSVManager(data_dir)
        codes = manager.list_all_stocks()
        if not codes:
            self.fail(f"local stock CSV pool is empty for active provider={active_provider}")
            return

        self.ok(f"local stock CSV pool={len(codes)} active_provider={active_provider}")
        checked = 0
        for code in codes[:200]:
            df = manager.read_stock(code)
            if df.empty:
                self.fail(f"{code}: CSV unreadable or empty")
                continue
            try:
                manager._validate_stock_dataframe(df)
            except Exception as exc:
                self.fail(f"{code}: CSV invalid: {exc}")
            checked += 1
        self.ok(f"sample CSV shape checked={checked}")

    def check_web_api_contract(self):
        try:
            import web_server

            client = web_server.app.test_client()
            stats = client.get("/api/stats")
            stats_payload = stats.get_json(silent=True) or {}
            if stats.status_code == 200 and stats_payload.get("success"):
                self.ok("web /api/stats ok")
            else:
                self.fail(f"web /api/stats failed status={stats.status_code}")

            no_token = client.post("/api/config", json={"BowlReboundStrategy": {"N": -1}})
            if no_token.status_code == 403:
                self.ok("web mutating API rejects missing session token")
            else:
                self.fail(f"web mutating API token check failed status={no_token.status_code}")

            bad_config = client.post(
                "/api/config",
                json={"BowlReboundStrategy": {"N": -1}},
                headers={"X-Quant-Session": web_server.WEB_SESSION_TOKEN},
            )
            if bad_config.status_code == 400:
                self.ok("web config API rejects invalid strategy params")
            else:
                self.fail(f"web config validation failed status={bad_config.status_code}")

            fallback = web_server._stock_display_name("000001", {})
            if fallback == fallback_stock_name("000001") and not is_invalid_stock_name(fallback):
                self.ok("web missing stock_names fallback is selectable")
            else:
                self.fail("web missing stock_names fallback is unsafe")
        except Exception as exc:
            self.fail(f"web API contract check failed: {exc}")

    def check_full_local_selection(self, config, strategy_names, timeout_seconds=600):
        started = time.monotonic()
        data_dir = str(self.project_root / str(get_config_value(config, "data_dir", default="data")))
        manager = CSVManager(data_dir)
        codes = manager.list_all_stocks()

        names_path = Path(data_dir) / "stock_names.json"
        stock_names = {}
        if names_path.exists():
            stock_names = json.loads(names_path.read_text(encoding="utf-8"))

        candidates = []
        invalid_count = 0
        for code in codes:
            name = stock_names.get(code) or fallback_stock_name(code)
            if is_invalid_stock_name(name):
                invalid_count += 1
                continue
            candidates.append((code, name))

        chunk_size = 100
        chunks = [candidates[index:index + chunk_size] for index in range(0, len(candidates), chunk_size)]
        if not chunks:
            self.fail("full-local selection has no candidates")
            return

        max_workers = min(max(os.cpu_count() or 2, 1), 8)
        processed = 0
        valid = 0
        skipped = 0
        error_counts = {name: 0 for name in strategy_names}
        selected_total = 0
        error_details = []

        print(f"FULL local candidates={len(candidates)} invalid_names={invalid_count} workers={max_workers}")
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=initialize_selection_worker,
            initargs=(data_dir, strategy_names, str(self.project_root / "config" / "strategy_params.yaml")),
        ) as executor:
            futures = [executor.submit(process_selection_chunk, chunk, "all", False) for chunk in chunks]
            for future in as_completed(futures):
                if time.monotonic() - started > timeout_seconds:
                    for pending in futures:
                        pending.cancel()
                    self.fail(f"full-local selection timeout after {timeout_seconds}s")
                    return

                payload = future.result()
                processed += payload.get("processed_count", 0)
                valid += payload.get("valid_count", 0)
                skipped += payload.get("skipped_count", 0)
                selected_total += sum(len(items) for items in payload.get("results_by_strategy", {}).values())
                for strategy_name, count in payload.get("error_counts", {}).items():
                    error_counts[strategy_name] = error_counts.get(strategy_name, 0) + count
                error_details.extend(payload.get("error_details", []))
                if processed == len(candidates) or processed % 1000 == 0:
                    elapsed = int(time.monotonic() - started)
                    print(f"FULL progress {processed}/{len(candidates)} valid={valid} selected={selected_total} elapsed={elapsed}s")

        total_errors = sum(error_counts.values())
        if total_errors:
            for detail in error_details[:10]:
                self.fail(f"strategy error {detail['strategy']} {detail['code']}: {detail['error']}")
            self.fail(f"full-local strategy errors={error_counts}")
        else:
            self.ok(
                f"full-local selection completed processed={processed} valid={valid} "
                f"skipped={skipped} selected={selected_total}"
            )

    def check_provider_smoke(self, config, provider_name, max_network_stocks=3):
        provider_name = str(provider_name or "").lower()
        try:
            token = os.getenv("TUSHARE_TOKEN") or get_config_value(config, "data_source", "tushare", "token")
            if provider_name == "tushare" and not token:
                self.warn("tushare provider smoke skipped because token is not configured")
                return
            provider = create_data_provider(
                provider_name=provider_name,
                data_dir=str(self.project_root / str(get_config_value(config, "data_dir", default="data"))),
                config=config,
                token=token,
            )
            universe = provider.get_target_universe(board="all", max_stocks=max_network_stocks)
            if not universe:
                self.fail(f"{provider_name} provider returned empty universe")
                return
            self.ok(f"{provider_name} universe sample={len(universe)}")

            for item in universe[:max_network_stocks]:
                df = provider.fetch_stock_history(item["code"], years=1)
                if df is None or df.empty:
                    self.fail(f"{provider_name} history smoke failed for {item['code']}")
                else:
                    self.ok(f"{provider_name} history {item['code']} rows={len(df)}")
        except Exception as exc:
            self.fail(f"{provider_name} provider smoke failed: {exc}")


def run_doctor(project_root, full_local=False, provider_smoke=None, max_network_stocks=3, timeout_seconds=600):
    return Doctor(Path(project_root)).run(
        full_local=full_local,
        provider_smoke=provider_smoke,
        max_network_stocks=max_network_stocks,
        timeout_seconds=timeout_seconds,
    )
