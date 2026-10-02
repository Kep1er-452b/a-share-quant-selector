"""Date slices for incremental Tushare updates; separate from extension stores.

An accepted slice means its returned rows passed validation. It is not a claim
that every listed security traded. Missing security/dates use the stock endpoint.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from utils.atomic_io import atomic_write_json
from utils.csv_manager import CSVManager


FIELDS = {
    "daily": "ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
    "daily_basic": "ts_code,trade_date,turnover_rate,total_mv",
    "adj_factor": "ts_code,trade_date,adj_factor",
}


class BatchSliceError(ValueError):
    pass


def validate_slice(frame, dataset, date):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise BatchSliceError(f"{dataset}/{date}: empty response")
    required = set(FIELDS[dataset].split(","))
    if required - set(frame.columns):
        raise BatchSliceError(f"{dataset}/{date}: missing fields {sorted(required - set(frame.columns))}")
    frame = frame[list(FIELDS[dataset].split(","))].copy()
    frame["trade_date"] = frame.trade_date.astype(str)
    frame["ts_code"] = frame.ts_code.astype(str)
    if not frame.trade_date.eq(date).all():
        raise BatchSliceError(f"{dataset}/{date}: date mismatch")
    if not frame.ts_code.str.fullmatch(r"\d{6}\.(SZ|SH|BJ)").all():
        raise BatchSliceError(f"{dataset}/{date}: invalid security identity")
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise BatchSliceError(f"{dataset}/{date}: duplicate rows or repeated page")
    numeric = list(required - {"ts_code", "trade_date"})
    for field in numeric:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if not np.isfinite(frame[field].to_numpy(dtype=float)).all():
            raise BatchSliceError(f"{dataset}/{date}: invalid {field}")
    if dataset == "daily":
        if (frame[["open", "high", "low", "close", "pre_close"]] <= 0).any().any():
            raise BatchSliceError(f"daily/{date}: nonpositive price")
        if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (frame.low > frame[["open", "close"]].min(axis=1)).any():
            raise BatchSliceError(f"daily/{date}: invalid OHLC")
        if (frame[["vol", "amount"]] < 0).any().any():
            raise BatchSliceError(f"daily/{date}: negative volume/amount")
    elif (frame[numeric] < 0).any().any() or (dataset == "adj_factor" and frame.adj_factor.le(0).any()):
        raise BatchSliceError(f"{dataset}/{date}: negative metric or nonpositive factor")
    return frame.sort_values("ts_code").reset_index(drop=True)


class BatchSliceStore:
    """Hash-checked immutable responses plus an atomic retry ledger."""

    def __init__(self, root):
        self.root = Path(root)
        self.state_path = self.root / "state.json"

    def state(self):
        if not self.state_path.exists():
            return {"version": 1, "slices": {}}
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("slices"), dict):
            raise BatchSliceError("invalid batch ledger")
        return payload

    def record(self, dataset, date, **fields):
        key = f"{dataset}/{date}"
        with CSVManager._lock_for_path(self.state_path), CSVManager._process_lock_for_path(self.state_path):
            state = self.state()
            previous = state["slices"].get(key, {})
            state["slices"][key] = {**previous, **fields, "updated_at": datetime.now(timezone.utc).isoformat()}
            atomic_write_json(self.state_path, state)

    def record_plan(self, plan_id, **fields):
        with CSVManager._lock_for_path(self.state_path), CSVManager._process_lock_for_path(self.state_path):
            state=self.state()
            plans=state.setdefault('plans',{})
            plans[plan_id]={**plans.get(plan_id,{}),**fields,'updated_at':datetime.now(timezone.utc).isoformat()}
            state['active_plan_id']=plan_id
            # Keep unfinished plans as retry evidence; only completed planning
            # records can age out. Input slices/revisions are retained separately.
            finished=[key for key,value in plans.items() if value.get('status') in ('prepared','resumed')]
            for key in finished[:-10]:
                plans.pop(key)
            atomic_write_json(self.state_path,state)

    def save(self, dataset, date, frame):
        frame = validate_slice(frame, dataset, date)
        content = frame.to_json(orient="split", index=False, double_precision=15)
        digest = hashlib.sha256(content.encode()).hexdigest()
        path = self.root / "slices" / f"{dataset}-{date}-{digest}.json"
        atomic_write_json(path, {"data": content, "checksum": digest})
        self.record(dataset, date, status="validated", checksum=digest, rows=len(frame), error=None)
        return frame

    def load(self, dataset, date):
        state = self.state()["slices"].get(f"{dataset}/{date}", {})
        digest = state.get("checksum", "")
        if state.get("status") != "validated" or not re.fullmatch(r"[a-f0-9]{64}", digest):
            return None
        path = self.root / "slices" / f"{dataset}-{date}-{digest}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if hashlib.sha256(payload["data"].encode()).hexdigest() != digest:
            raise BatchSliceError("batch checksum mismatch")
        data = json.loads(payload["data"])
        frame = pd.DataFrame(data["data"], columns=data["columns"])
        return validate_slice(frame, dataset, date)


def fetch_date_slice(call, dataset, date, *, halt_checker=None, page_size=6000, max_pages=10):
    frames, seen = [], set()
    for page in range(max_pages):
        if halt_checker and halt_checker():
            raise InterruptedError("系统已急停或任务已取消")
        result = call(dataset, trade_date=date, fields=FIELDS[dataset], limit=page_size, offset=page * page_size)
        if not isinstance(result, pd.DataFrame):
            raise BatchSliceError(f"{dataset}/{date}: invalid response")
        if result.empty:
            if not frames:
                raise BatchSliceError(f"{dataset}/{date}: empty response")
            break
        frame = validate_slice(result, dataset, date)
        keys = set(frame.ts_code)
        if keys & seen:
            raise BatchSliceError(f"{dataset}/{date}: pagination made no progress")
        seen.update(keys)
        frames.append(frame)
        if len(frame) < page_size:
            break
    else:
        raise BatchSliceError(f"{dataset}/{date}: pagination limit reached")
    return pd.concat(frames, ignore_index=True)


def qfq_stock_frame(price, factors):
    """Match installed pro_bar: latest factor anchor and %.2f price formatting."""
    factors = factors.sort_values("trade_date", ascending=False)
    merged = price.merge(factors[["trade_date", "adj_factor"]], on="trade_date", how="left", validate="one_to_one")
    if merged.adj_factor.isna().any():
        raise BatchSliceError("price date missing adj_factor")
    anchor = float(factors.iloc[0].adj_factor)
    for field in ("open", "high", "low", "close", "pre_close"):
        merged[field] = (merged[field] * merged.adj_factor / anchor).map(lambda value: float("%.2f" % value))
    return merged.sort_values("trade_date", ascending=False).reset_index(drop=True)
