"""Validate immutable result packages without importing server business code."""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

MAX_FILE = 128 * 1024 * 1024
MAX_PACKAGE = 256 * 1024 * 1024
RELEASE = re.compile(r"[0-9]{8}-r[1-9][0-9]*-[0-9a-f]{12}")
SHA = re.compile(r"[0-9a-f]{64}")
REQUIRED = {"market-summary", "selection-items", "evaluation-status", "indicator-values",
            "tracking-daily", "watchlist-snapshot", "metrics", "daily-report"}


def fail(message):
    raise ValueError(message)


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        fail("non-finite JSON number")
    return number


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=lambda _: fail("non-finite JSON number"), parse_float=finite_float)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(262144), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_path(root, relative):
    if not isinstance(relative, str) or len(relative) > 240 or "\\" in relative:
        fail("invalid artifact path")
    parts = relative.split("/")
    if any(p in {"", ".", ".."} for p in parts) or PurePosixPath(relative).is_absolute():
        fail("unsafe artifact path")
    target = Path(root)
    for part in parts:
        target /= part
        if target.is_symlink():
            fail("symbolic links are not result artifacts")
    return target


def validate_manifest(manifest, entry):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0.0":
        fail("unsupported result schema")
    for key in ("release_id", "trade_date", "revision", "publication_seq"):
        if manifest.get(key) != entry.get(key):
            fail(f"index/manifest mismatch: {key}")
    identity = manifest.get("release_id", "")
    if not isinstance(identity, str) or not RELEASE.fullmatch(identity):
        fail("invalid release_id")
    trade_date = date.fromisoformat(manifest["trade_date"])
    if not identity.startswith(trade_date.strftime("%Y%m%d") + f"-r{manifest['revision']}-"):
        fail("release date/revision mismatch")
    if manifest.get("timezone") != "Asia/Shanghai" or manifest.get("source", {}).get("provider") != "tushare":
        fail("unsupported market source/timezone")
    if manifest.get("producer", {}).get("service") != "a-share-quant-server":
        fail("unexpected result producer")
    quality = manifest.get("quality", {})
    if quality.get("core_status") not in {"completed", "completed_with_warnings"}:
        fail("server core result is not complete")
    if quality.get("integrity", {}).get("selection_allowed") is not True:
        fail("server did not authorize selection data")
    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict) or not isinstance(coverage.get("strategies"), list):
        fail("missing strategy coverage")
    strategies = coverage["strategies"]
    if not strategies or len(strategies) > 100 or any(not isinstance(s, str) or len(s) > 128 for s in strategies):
        fail("invalid strategy coverage")
    if len(set(strategies)) != len(strategies):
        fail("duplicate strategies")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 128:
        fail("invalid artifact list")
    ids, paths, total = set(), set(), 0
    for artifact in artifacts:
        aid, relative = artifact.get("artifact_id"), artifact.get("path")
        if not isinstance(aid, str) or not re.fullmatch(r"[a-z0-9-]{1,128}", aid) or aid in ids:
            fail("invalid or duplicate artifact identity")
        safe_path(Path("."), relative)
        if relative in paths or not SHA.fullmatch(str(artifact.get("sha256", ""))):
            fail("invalid artifact path/hash")
        size = artifact.get("bytes")
        if type(size) is not int or not 0 <= size <= MAX_FILE or type(artifact.get("required")) is not bool:
            fail("invalid artifact size/requirement")
        total += size
        ids.add(aid)
        paths.add(relative)
    if total > MAX_PACKAGE or not REQUIRED <= ids:
        fail("incomplete or oversized result package")
    if any(a["artifact_id"] in REQUIRED and not a["required"] for a in artifacts):
        fail("core artifacts must be required")


def rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index >= 200000 or len(line) > 1024 * 1024:
                fail("dataset exceeds row limits")
            row = json.loads(line, parse_constant=lambda _: fail("non-finite JSON number"), parse_float=finite_float)
            if not isinstance(row, dict):
                fail("dataset row must be an object")
            yield row


def validate_package(root, manifest, cancel=lambda: None):
    counts, evaluations, selections = {}, {}, set()
    status_counts = Counter()
    strategies = set(manifest["coverage"]["strategies"])
    for artifact in manifest["artifacts"]:
        cancel()
        path = safe_path(root, artifact["path"])
        if not path.is_file():
            if artifact["required"]:
                fail("missing required artifact")
            continue
        if path.stat().st_size != artifact["bytes"] or digest(path) != artifact["sha256"]:
            fail(f"artifact integrity mismatch: {artifact['artifact_id']}")
        aid = artifact["artifact_id"]
        if path.suffix == ".json":
            if not isinstance(read_json(path), dict):
                fail("dataset must be a JSON object")
        elif path.suffix == ".jsonl":
            seen = set()
            count = 0
            for row in rows(path):
                count += 1
                if count % 500 == 0:
                    cancel()
                code = row.get("code")
                if aid in {"selection-items", "evaluation-status", "tracking-daily", "selection-tracking"}:
                    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
                        fail("invalid A-share code")
                if aid in {"selection-items", "evaluation-status"}:
                    strategy = row.get("strategy_id")
                    if strategy not in strategies:
                        fail("unknown strategy")
                    key = (code, strategy)
                    if key in seen:
                        fail("duplicate selection/evaluation key")
                    seen.add(key)
                    if aid == "selection-items":
                        trigger = row.get("trigger")
                        if not isinstance(trigger, dict) or trigger.get("code") != code:
                            fail("selection trigger identity mismatch")
                        signals = trigger.get("signals", [])
                        if not isinstance(signals, list) or any(not isinstance(s, dict) for s in signals):
                            fail("invalid selection signals")
                        selections.add(key)
                    else:
                        evaluations[key] = row.get("status")
                        status_counts[row.get("status")] += 1
                if aid in {"tracking-daily", "selection-tracking"}:
                    if row.get("trade_date") != manifest["trade_date"]:
                        fail("tracking trade date mismatch")
                    key = (row.get('interval_id') if aid == 'selection-tracking' else code, row['trade_date'])
                    if not key[0] or key in seen:
                        fail('duplicate or missing tracking identity')
                    seen.add(key)
                    if row.get('batch_id') not in manifest.get('tracking_batch_ids', []):
                        fail('tracking batch mismatch')
            if artifact.get("rows") != count:
                fail("artifact row count mismatch")
            counts[aid] = count
    coverage = manifest["coverage"]
    for aid, field in {"selection-items": "selected_records", "evaluation-status": "selection_evaluations",
                       "tracking-daily": "tracking_members", "selection-tracking": "selection_tracking_intervals"}.items():
        if aid in counts and counts[aid] != coverage.get(field):
            fail("coverage count mismatch")
    if any(evaluations.get(key) != "matched" for key in selections):
        fail("selection has no matching evaluation")
    if {key for key, value in evaluations.items() if value == "matched"} != selections:
        fail("matched evaluation has no selection")
    if dict(status_counts) != coverage.get("selection_evaluation_status_counts"):
        fail("evaluation status coverage mismatch")
    if counts.get("indicator-values") == 0 and coverage.get("indicator_values", {}).get("status") != "not_computed":
        fail("empty indicator dataset must declare not_computed")
