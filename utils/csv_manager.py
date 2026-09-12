"""
CSV 数据管理工具
"""
import os
import re
import tempfile
import hashlib
import json
import weakref
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from threading import RLock
import pandas as pd
from pathlib import Path

from utils.price_adjustment import repair_adjustment_gaps


class CSVManager:
    """CSV文件管理器"""

    STOCK_CODE_PATTERN = re.compile(r"^\d{6}$")
    REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume", "amount", "turnover", "market_cap"}
    NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "amount", "turnover", "market_cap"]
    _locks_guard = RLock()
    _path_locks = weakref.WeakValueDictionary()
    
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._listing_metadata_signature = None
        self._listing_dates = {}

    @classmethod
    def _lock_for_path(cls, path: Path):
        key = str(Path(path).resolve())
        with cls._locks_guard:
            path_lock = cls._path_locks.get(key)
            if path_lock is None:
                path_lock = RLock()
                cls._path_locks[key] = path_lock
            return path_lock

    @staticmethod
    @contextmanager
    def _process_lock_for_path(path: Path):
        """Coordinate CSV writers across Web/CLI processes."""
        path = Path(path).resolve()
        # Use a bounded lock striping directory. Per-stock ``.csv.lock`` files
        # accumulate forever in a full-market warehouse, while 256 stable lock
        # slots retain cross-process safety with only modest false contention.
        digest = hashlib.sha256(str(path).encode("utf-8")).digest()
        slot = int.from_bytes(digest[:2], "big") % 256
        data_root = path.parent.parent if path.parent.name.isdigit() else path.parent
        lock_path = data_root / ".aqs-locks" / f"{slot:03d}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                if lock_path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    
    @classmethod
    def validate_stock_code(cls, stock_code):
        """Validate and normalize a local A-share stock code."""
        code = str(stock_code or "").strip()
        if not cls.STOCK_CODE_PATTERN.match(code):
            raise ValueError(f"非法股票代码: {stock_code}")
        return code

    def get_stock_path(self, stock_code, create_dirs=True):
        """获取股票CSV文件路径"""
        stock_code = self.validate_stock_code(stock_code)
        # 按股票代码前两位分目录，避免单目录文件过多
        prefix = stock_code[:2] if len(stock_code) >= 2 else stock_code
        subdir = self.data_dir / prefix
        if create_dirs:
            subdir.mkdir(exist_ok=True)
        return subdir / f"{stock_code}.csv"
    
    def read_stock(self, stock_code):
        """读取股票数据"""
        path = self.get_stock_path(stock_code, create_dirs=False)
        try:
            if not path.exists() or path.stat().st_size == 0:
                return pd.DataFrame()
        except FileNotFoundError:
            # The file may disappear between exists() and stat() during an
            # atomic provider switch. Treat that exactly like a missing file.
            return pd.DataFrame()

        # Real permission, locking, decoding, and I/O failures must propagate.
        # Returning an empty frame here makes incremental sync mistake a
        # six-year warehouse for a missing file and replace it with a tiny
        # update window.
        with self._lock_for_path(path):
            df = pd.read_csv(path, parse_dates=['date'])
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df = df.dropna(subset=['date']).sort_values('date', ascending=False).reset_index(drop=True)
        return df

    def read_stock_for_analysis(self, stock_code):
        """读取股票数据并返回技术分析用的复权修复视图。"""
        stock_code = self.validate_stock_code(stock_code)
        df = self.read_stock(stock_code)
        if df.empty:
            return df
        repaired, repairs = repair_adjustment_gaps(
            df,
            stock_code=stock_code,
            list_date=self._listing_date(stock_code),
            board=self._board_for_code(stock_code),
            market="a_share",
        )
        repaired.attrs["adjustment_repairs"] = repairs
        return repaired

    @staticmethod
    def _board_for_code(stock_code):
        if stock_code.startswith(("300", "301")):
            return "chinext"
        if stock_code.startswith(("688", "689")):
            return "star"
        if stock_code.startswith(("43", "83", "87", "88", "92")):
            return "beijing"
        return "main"

    def _listing_date(self, stock_code):
        """Read provider-neutral listing metadata once per file revision."""
        paths = (
            self.data_dir / "listing_dates.json",
            self.data_dir / "tushare_stock_map.json",
        )
        signatures = []
        for path in paths:
            try:
                stat = path.stat()
                signatures.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                signatures.append((str(path), None, None))
        signature = tuple(signatures)
        if signature != self._listing_metadata_signature:
            listing_dates = {}
            for path in paths:
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                except (OSError, ValueError, TypeError):
                    continue
                if not isinstance(payload, Mapping):
                    continue
                for code, item in payload.items():
                    if isinstance(item, Mapping):
                        item = item.get("list_date") or item.get("listing_date")
                    date_text = str(item or "").strip()
                    if date_text:
                        listing_dates[str(code).strip().zfill(6)] = date_text
            self._listing_dates = listing_dates
            self._listing_metadata_signature = signature
        return self._listing_dates.get(stock_code)

    def _validate_stock_dataframe(self, df):
        """Validate and normalize stock OHLCV data before writing."""
        if df is None or df.empty:
            raise ValueError("股票数据为空，拒绝写入")

        missing_columns = self.REQUIRED_COLUMNS - set(df.columns)
        if missing_columns:
            raise ValueError(f"股票数据缺少字段: {', '.join(sorted(missing_columns))}")

        result = df.copy()
        result['date'] = pd.to_datetime(result['date'], errors='coerce')
        if result['date'].isna().any():
            raise ValueError("股票数据包含无法解析的日期")

        for column in self.NUMERIC_COLUMNS:
            result[column] = pd.to_numeric(result[column], errors='coerce')

        required_price_columns = ["open", "high", "low", "close", "volume"]
        if result[required_price_columns].isna().any().any():
            raise ValueError("股票数据包含无法解析的价格/成交量字段")

        if (result[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("股票数据包含非正价格")

        if (result["volume"] < 0).any():
            raise ValueError("股票数据包含负成交量")

        result["amount"] = result["amount"].fillna(0)
        result["turnover"] = result["turnover"].fillna(0)
        result["market_cap"] = result["market_cap"].fillna(0)
        return result
    
    def write_stock(self, stock_code, df, write_guard=None, _process_locked=False):
        """写入股票数据（自动去重排序，原子写入）"""
        path = self.get_stock_path(stock_code)
        process_lock = (
            self._process_lock_for_path(path)
            if not _process_locked
            else nullcontext()
        )
        with self._lock_for_path(path), process_lock:
            if write_guard and not write_guard():
                raise InterruptedError(f"{stock_code} 写入已取消")
            df = self._validate_stock_dataframe(df)

            # 去重：按日期去重，保留最后出现的
            df = df.drop_duplicates(subset=['date'], keep='last')

            # 按日期倒序排列（最新在前）
            df = df.sort_values('date', ascending=False)

            # 确保目录存在
            path.parent.mkdir(parents=True, exist_ok=True)

            # 原子写入：先写临时文件，再 os.replace
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix='.csv', prefix=f'{stock_code}_', dir=str(path.parent)
            )
            try:
                os.close(tmp_fd)
                with open(tmp_path, "w", encoding="utf-8", newline="") as handle:
                    df.to_csv(handle, index=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                if write_guard and not write_guard():
                    raise InterruptedError(f"{stock_code} 写入已取消")
                os.replace(tmp_path, str(path))
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
            return path

    @staticmethod
    def _preserve_existing_metrics(existing_df, new_df):
        """
        在增量更新时，若新数据缺少辅助字段，则尽量保留旧值。
        主要保护 amount / turnover / market_cap，避免降级接口缺字段时把旧值
        覆盖成空值或 0。
        """
        if existing_df.empty or new_df.empty:
            return new_df

        result = new_df.copy()
        existing_by_date = existing_df.drop_duplicates(subset=['date'], keep='last').set_index('date')

        for column in ['amount', 'turnover', 'market_cap']:
            if column not in result.columns or column not in existing_by_date.columns:
                continue

            # 增量数据里这两列有时是 int，有时是 float。先统一转成浮点，
            # 避免回填旧值（常见为小数）时触发 pandas 的 dtype 冲突。
            result[column] = pd.to_numeric(result[column], errors='coerce').astype(float)
            fallback_values = pd.to_numeric(
                result['date'].map(existing_by_date[column]),
                errors='coerce'
            ).astype(float)
            current_values = result[column]
            invalid_mask = current_values.isna() | (current_values <= 0)
            if invalid_mask.any():
                result.loc[invalid_mask, column] = fallback_values.loc[invalid_mask]

        return result
    
    def update_stock(self, stock_code, new_df, write_guard=None):
        """增量更新股票数据"""
        path = self.get_stock_path(stock_code)
        with self._lock_for_path(path), self._process_lock_for_path(path):
            if write_guard and not write_guard():
                raise InterruptedError(f"{stock_code} 写入已取消")
            existing_df = self.read_stock(stock_code)

            if existing_df.empty:
                return self.write_stock(
                    stock_code,
                    new_df,
                    write_guard=write_guard,
                    _process_locked=True,
                )

            new_df = self._preserve_existing_metrics(existing_df, new_df)

            # 合并数据
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            return self.write_stock(
                stock_code,
                combined,
                write_guard=write_guard,
                _process_locked=True,
            )

    def merge_stock(self, stock_code, new_df, validator=None, write_guard=None):
        """Atomically validate and merge an incremental frame with current data.

        ``validator`` runs while both in-process and cross-process locks are
        held, so callers never validate one warehouse revision and overwrite a
        newer revision read by ``update_stock`` later.
        """
        path = self.get_stock_path(stock_code)
        with self._lock_for_path(path), self._process_lock_for_path(path):
            if write_guard and not write_guard():
                raise InterruptedError(f"{stock_code} 写入已取消")
            existing_df = self.read_stock(stock_code)
            prepared_new_df = self._preserve_existing_metrics(existing_df, new_df)
            combined = pd.concat([existing_df, prepared_new_df], ignore_index=True)
            rejection = validator(existing_df, prepared_new_df, combined) if validator else None
            if rejection:
                return {"written": False, "rejection": rejection}
            written_path = self.write_stock(
                stock_code,
                combined,
                write_guard=write_guard,
                _process_locked=True,
            )
            return {"written": True, "path": written_path, "existing_rows": len(existing_df)}
    
    def list_all_stocks(self):
        """列出所有已保存的股票代码"""
        stocks = []
        for csv_file in self.data_dir.glob("[0-9][0-9]/*.csv"):
            stock_code = csv_file.stem
            if self.STOCK_CODE_PATTERN.match(stock_code):
                stocks.append(stock_code)
        return sorted(stocks)
    
    def get_stock_count(self):
        """获取已保存的股票数量"""
        return len(self.list_all_stocks())
    
    def stock_exists(self, stock_code):
        """检查股票数据是否存在"""
        return self.get_stock_path(stock_code).exists()
