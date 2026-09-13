"""Single-flight background synchronization and private immutable read models."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event, RLock, Thread
import uuid

from utils.atomic_io import atomic_write_json
from utils.server_results.contract import (
    RELEASE, SHA, digest, read_json, rows, safe_path, validate_manifest, validate_package,
)
from utils.server_results.transport import SSHTransport, ServerReadError
from utils.strategy_labels import strategy_ui_metadata


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def cache_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("另一个应用实例正在同步服务器结果") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("另一个应用实例正在同步服务器结果") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Cancelled(Exception):
    pass


class ServerResultService:
    def __init__(self, root, config_path, transport_factory=SSHTransport):
        self.root = Path(root)
        self.config_path = Path(config_path)
        self.transport_factory = transport_factory
        self.lock = RLock()
        self.cancel_event = Event()
        self.job = None

    def configuration(self):
        if not self.config_path.is_file():
            return None
        value = read_json(self.config_path)
        if not isinstance(value, dict):
            raise ValueError("服务器结果配置必须是 JSON 对象")
        return value if value.get("enabled", True) else None

    def snapshot(self):
        with self.lock:
            return [deepcopy(self.job)] if self.job else []

    def status(self):
        configured = False
        config_error = None
        try:
            configured = bool(self.configuration())
        except (ValueError, OSError):
            config_error = "服务器连接配置无法读取"
        current = read_json(self.root / "current.json") if (self.root / "current.json").is_file() else None
        return {"configured": configured, "config_error": config_error, "current": current,
                "job": next(iter(self.snapshot()), None)}

    def cancel(self, job_id=None):
        with self.lock:
            if job_id and (not self.job or self.job["job_id"] != job_id):
                raise KeyError("unknown sync job")
            if self.job and self.job["status"] == "running":
                self.cancel_event.set()
                self.job["current_step"] = "正在取消"
            return deepcopy(self.job)

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def update(self, **values):
        with self.lock:
            self.job.update(values, updated_at=now())

    def start(self):
        with self.lock:
            if self.job and self.job["status"] == "running":
                return deepcopy(self.job)
            config = self.configuration()
            if not config:
                raise ValueError("尚未配置服务器结果连接")
            transport = self.transport_factory(config)
            self.cancel_event = Event()
            self.job = {"job_id": uuid.uuid4().hex, "status": "running", "created_at": now(),
                        "updated_at": now(), "current_step": "读取发布索引", "progress_pct": 0}
            try:
                Thread(target=self._run, args=(transport,), daemon=True, name="server-result-sync").start()
            except Exception:
                self.job.update(status="failed", error="无法启动同步任务", finished_at=now())
                raise
            return deepcopy(self.job)

    def _run(self, transport):
        try:
            with cache_lock(self.root):
                self._synchronize(transport)
            self.update(status="completed", progress_pct=100, finished_at=now())
        except Cancelled:
            self.update(status="cancelled", current_step="已取消，保留已验证缓存", finished_at=now())
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, ConnectionError, TimeoutError, RuntimeError)) else "同步失败，已保留最近有效缓存"
            step = self.snapshot()[0].get('current_step', '')
            self.update(status="failed", error=message[:400], failed_step=step,
                        error_code=getattr(exc, 'code', 'SYNC_FAILED'), auth_url=getattr(exc, 'auth_url', None),
                        current_step="同步失败", finished_at=now())

    def _synchronize(self, transport):
        staging_root = self.root / "staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        # A held cross-process lock makes abandoned temporary downloads disposable.
        for old in staging_root.glob("download-*"):
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
        with tempfile.TemporaryDirectory(prefix="download-", dir=staging_root) as temporary:
            stage = Path(temporary)
            transport.fetch({"op": "index"}, stage / "index.json", 8 * 1024 * 1024, self.check_cancel)
            index = read_json(stage / "index.json")
            if not isinstance(index, dict) or index.get("schema_version") != "1.0.0":
                raise ValueError("不支持的发布索引")
            entries = index.get("releases")
            if not isinstance(entries, list) or len(entries) > 20000:
                raise ValueError("发布索引超出限制")
            published, identities, sequences = [], set(), set()
            for entry in entries:
                rid = entry.get("release_id", "")
                seq, revision = entry.get("publication_seq"), entry.get("revision")
                if (not isinstance(rid, str) or not RELEASE.fullmatch(rid) or rid in identities
                        or type(seq) is not int or seq < 1 or seq in sequences
                        or type(revision) is not int or revision < 1):
                    raise ValueError("发布身份或序号无效")
                identities.add(rid)
                sequences.add(seq)
                if entry.get("status") == "published":
                    if not SHA.fullmatch(str(entry.get("manifest_sha256", ""))):
                        raise ValueError("发布清单缺少校验值")
                    # Reject malformed date strings before choosing a candidate.
                    date.fromisoformat(entry["trade_date"])
                    published.append(entry)
            if not published:
                self.update(current_step="服务器尚未发布可用结果")
                return
            entry = max(published, key=lambda e: (e["trade_date"], e["revision"], e["publication_seq"]))
            rid = entry["release_id"]
            current = self.status()["current"]
            if current and (current["trade_date"], current["revision"]) > (entry["trade_date"], entry["revision"]):
                self.update(current_step="服务器索引落后于本机缓存，保留较新结果")
                return
            final = self.root / "releases" / rid
            warnings = []
            if final.exists():
                if final.is_symlink() or digest(final / "manifest.json") != entry["manifest_sha256"]:
                    raise ValueError("不可变发布发生变化，拒绝覆盖")
                manifest = read_json(final / "manifest.json")
                validate_manifest(manifest, entry)
                validate_package(final, manifest, self.check_cancel)
                # Fill missing optional files without changing already-verified bytes.
                for artifact in manifest['artifacts']:
                    destination = safe_path(final, artifact['path'])
                    if destination.exists():
                        continue
                    scratch = stage / 'optional'
                    try:
                        transport.fetch({'op': 'artifact', 'release_id': rid, 'artifact_id': artifact['artifact_id']},
                                        scratch, artifact['bytes'], self.check_cancel)
                        if scratch.stat().st_size != artifact['bytes'] or digest(scratch) != artifact['sha256']:
                            raise ValueError('optional artifact integrity mismatch')
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        scratch.replace(destination)
                    except (ConnectionError, TimeoutError) as exc:
                        if isinstance(exc, ServerReadError) and exc.code == 'SSH_AUTH_REQUIRED':
                            raise
                        warnings.append(artifact['artifact_id'])
            else:
                # Stable staging survives cancellation/disconnection. Only files
                # matching this pinned manifest can be reused on the next attempt.
                package = safe_path(staging_root, rid)
                package.mkdir(exist_ok=True)
                for old in staging_root.iterdir():
                    if old.name != rid and RELEASE.fullmatch(old.name) and old.is_dir() and not old.is_symlink():
                        shutil.rmtree(old)
                transport.fetch({"op": "manifest", "release_id": rid}, package / "manifest.json", 2 * 1024 * 1024, self.check_cancel)
                if digest(package / "manifest.json") != entry["manifest_sha256"]:
                    raise ValueError("manifest SHA-256 校验失败")
                manifest = read_json(package / "manifest.json")
                validate_manifest(manifest, entry)
                for i, artifact in enumerate(manifest["artifacts"]):
                    self.check_cancel()
                    self.update(current_step=f"下载 {artifact['artifact_id']}", progress_pct=5 + int(80 * i / len(manifest["artifacts"])))
                    destination = safe_path(package, artifact["path"])
                    if destination.is_file() and destination.stat().st_size == artifact['bytes'] and digest(destination) == artifact['sha256']:
                        continue
                    try:
                        transport.fetch({"op": "artifact", "release_id": rid, "artifact_id": artifact["artifact_id"]},
                                        destination, artifact["bytes"], self.check_cancel)
                    except (ConnectionError, TimeoutError) as exc:
                        if artifact["required"] or (isinstance(exc, ServerReadError) and exc.code == 'SSH_AUTH_REQUIRED'):
                            raise
                        destination.unlink(missing_ok=True)
                        warnings.append(artifact["artifact_id"])
                self.update(current_step="校验数据与关联关系", progress_pct=90)
                validate_package(package, manifest, self.check_cancel)
                self.check_cancel()
                final.parent.mkdir(parents=True, exist_ok=True)
                package.rename(final)
            if warnings:
                self.update(warning="可选文件暂不可用：" + ", ".join(warnings))
            self.check_cancel()
            atomic_write_json(self.root / "current.json", {**entry, "synced_at": now()})
            self.update(current_step="已同步服务器结果", release_id=rid)
            self._prune(rid)

    def _prune(self, current_id):
        releases = self.releases()
        for item in releases[20:]:
            if item["release_id"] != current_id:
                shutil.rmtree(self.root / "releases" / item["release_id"])

    def releases(self):
        result = []
        for path in (self.root / "releases").glob("*/manifest.json"):
            if RELEASE.fullmatch(path.parent.name) and not path.parent.is_symlink():
                m = read_json(path)
                result.append({key: m[key] for key in ("release_id", "trade_date", "revision", "published_at")})
        return sorted(result, key=lambda m: (m["trade_date"], m["revision"]), reverse=True)

    def manifest(self, release_id=None):
        if not release_id:
            current = self.status()["current"]
            if not current:
                raise FileNotFoundError("尚无已验证的服务器结果")
            release_id = current["release_id"]
        if not RELEASE.fullmatch(str(release_id)):
            raise ValueError("invalid release_id")
        return read_json(safe_path(self.root, f"releases/{release_id}/manifest.json"))

    def artifact(self, release_id, artifact_id):
        manifest = self.manifest(release_id)
        artifact = next((a for a in manifest["artifacts"] if a["artifact_id"] == artifact_id), None)
        if not artifact:
            raise FileNotFoundError("结果文件不存在")
        path = safe_path(self.root / "releases" / manifest["release_id"], artifact["path"])
        if not path.is_file() or path.stat().st_size != artifact["bytes"] or digest(path) != artifact["sha256"]:
            raise FileNotFoundError("结果文件缺失或校验失败")
        return path, artifact

    def summary(self, release_id=None):
        manifest = self.manifest(release_id)
        return {**manifest, "strategy_labels": {s: strategy_ui_metadata(s).get("label", s)
                                               for s in manifest["coverage"]["strategies"]}}

    def dataset(self, release_id, dataset, offset=0, limit=100, strategy=None):
        allowed = {"selection-items", "tracking-daily", "selection-tracking", "watchlist-snapshot"}
        if dataset not in allowed or not 0 <= offset <= 200000 or not 1 <= limit <= 300:
            raise ValueError("invalid dataset page")
        path, _ = self.artifact(release_id, dataset)
        source = read_json(path).get("items", []) if dataset == "watchlist-snapshot" else rows(path)
        items, total = [], 0
        for row in source:
            if strategy and row.get("strategy_id") != strategy:
                continue
            if offset <= total < offset + limit:
                items.append(row)
            total += 1
        return {"items": items, "total": total, "offset": offset, "limit": limit}
