from copy import deepcopy
import json
from pathlib import Path
import shutil
import time

from flask import Flask
import pytest

from utils.server_results.contract import digest, validate_manifest
from utils.server_results.service import ServerResultService
from utils.server_results.transport import SSHTransport
from web_api.server_results import create_server_results_blueprint


def publish(root, *, revision=1, trade_date="2026-09-11", seq=1, matched=True):
    rid = trade_date.replace('-', '') + f"-r{revision}-0123456789ab"
    folder = root / "releases" / rid
    folder.mkdir(parents=True)
    selection = {"code": "000001", "name": "平安银行", "strategy_id": "B1V242BStrategy",
                 "trigger": {"code": "000001", "signals": [{"close": 12, "J": None}]}}
    evaluation = {"code": "000001", "strategy_id": "B1V242BStrategy", "status": "matched" if matched else "not_matched"}
    files = {"market-summary": {}, "selection-items": [selection], "evaluation-status": [evaluation],
             "indicator-values": [], "tracking-daily": [], "watchlist-snapshot": {"items": []}, "metrics": {}, "daily-report": "日报"}
    artifacts = []
    for aid, value in files.items():
        suffix = ".jsonl" if isinstance(value, list) else ".md" if isinstance(value, str) else ".json"
        relative = f"datasets/{aid}{suffix}"
        path = folder / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(''.join(json.dumps(r) + '\n' for r in value) if isinstance(value, list) else value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        artifact = {"artifact_id": aid, "path": relative, "required": True,
                    "bytes": path.stat().st_size, "sha256": digest(path), "media_type": "application/json"}
        if isinstance(value, list): artifact["rows"] = len(value)
        artifacts.append(artifact)
    m = {"schema_version": "1.0.0", "release_id": rid, "trade_date": trade_date, "revision": revision,
         "publication_seq": seq, "published_at": trade_date + "T17:20:00+08:00", "timezone": "Asia/Shanghai",
         "source": {"provider": "tushare"}, "producer": {"service": "a-share-quant-server"},
         "quality": {"core_status": "completed", "integrity": {"selection_allowed": True}},
         "coverage": {"strategies": ["B1V242BStrategy"], "selected_records": 1,
                      "selection_evaluations": 1, "tracking_members": 0,
                      "selection_evaluation_status_counts": {evaluation["status"]: 1},
                      "indicator_values": {"status": "not_computed"}}, "artifacts": artifacts}
    (folder / "manifest.json").write_text(json.dumps(m))
    entry = {k: m[k] for k in ("release_id", "trade_date", "revision", "publication_seq", "published_at")}
    entry.update(status="published", manifest_sha256=digest(folder / "manifest.json"))
    index_path = root / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"schema_version": "1.0.0", "releases": []}
    index["releases"].append(entry)
    index_path.write_text(json.dumps(index))
    return entry, m


class FileTransport:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.broken = False

    def fetch(self, request, destination, max_bytes, cancel):
        cancel()
        self.calls.append(request)
        if self.broken: raise ConnectionError("offline")
        if request["op"] == "index": path = self.root / "index.json"
        else:
            folder = self.root / "releases" / request["release_id"]
            path = folder / "manifest.json"
            if request["op"] == "artifact":
                m = json.loads(path.read_text())
                path = folder / next(a["path"] for a in m["artifacts"] if a["artifact_id"] == request["artifact_id"])
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def setup(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    transport = FileTransport(remote)
    config = tmp_path / "connection.json"
    config.write_text('{"enabled": true}')
    service = ServerResultService(tmp_path / "cache", config, lambda _: transport)
    return remote, transport, service


def sync(service):
    service.start()
    deadline = time.monotonic() + 5
    while service.snapshot()[0]["status"] == "running" and time.monotonic() < deadline:
        time.sleep(.01)
    return service.snapshot()[0]


def test_real_artifact_import_readback_and_idempotence(tmp_path):
    remote, transport, service = setup(tmp_path)
    entry, _ = publish(remote)
    assert sync(service)["status"] == "completed"
    assert service.status()["current"]["release_id"] == entry["release_id"]
    page = service.dataset(None, "selection-items")
    assert page["items"][0]["trigger"]["signals"][0]["J"] is None
    calls = len(transport.calls)
    assert sync(service)["status"] == "completed"
    assert len(transport.calls) == calls + 1  # Index only; no calculations or redownload.


def test_revisions_corruption_and_offline_keep_prior_cache(tmp_path):
    remote, transport, service = setup(tmp_path)
    first, _ = publish(remote)
    sync(service)
    second, _ = publish(remote, revision=2, seq=2)
    (remote / "releases" / second["release_id"] / "datasets/selection-items.jsonl").write_text('{}\n')
    assert sync(service)["status"] == "failed"
    assert service.status()["current"]["release_id"] == first["release_id"]
    transport.broken = True
    assert sync(service)["status"] == "failed"
    assert service.dataset(None, "selection-items")["total"] == 1


def test_late_historical_publication_does_not_replace_newest_trade_date(tmp_path):
    remote, _, service = setup(tmp_path)
    newest, _ = publish(remote)
    publish(remote, trade_date="2026-09-10", revision=8, seq=2)
    assert sync(service)["status"] == "completed"
    assert service.status()["current"]["release_id"] == newest["release_id"]
    revised, _ = publish(remote, revision=2, seq=3)
    assert sync(service)["status"] == "completed"
    assert service.status()["current"]["release_id"] == revised["release_id"]


def test_semantic_mismatch_with_valid_hashes_is_rejected(tmp_path):
    remote, _, service = setup(tmp_path)
    publish(remote, matched=False)
    assert sync(service)["status"] == "failed"
    assert service.status()["current"] is None


@pytest.mark.parametrize("mutation", ["schema", "path", "oversize", "source"])
def test_manifest_guards(tmp_path, mutation):
    remote, _, _ = setup(tmp_path)
    entry, manifest = publish(remote)
    if mutation == "schema": manifest["schema_version"] = "2.0.0"
    if mutation == "path": manifest["artifacts"][0]["path"] = "../secret"
    if mutation == "oversize": manifest["artifacts"][0]["bytes"] = 999999999
    if mutation == "source": manifest["source"]["provider"] = "hong_kong"
    with pytest.raises(ValueError): validate_manifest(manifest, entry)


def test_cancel_download_and_restart_preserve_cache(tmp_path):
    remote, transport, service = setup(tmp_path)
    entry, _ = publish(remote)
    sync(service)
    publish(remote, revision=2, seq=2)
    original = transport.fetch
    def cancel_during_artifact(request, destination, max_bytes, cancel):
        if request["op"] == "artifact": service.cancel()
        return original(request, destination, max_bytes, cancel)
    transport.fetch = cancel_during_artifact
    assert sync(service)["status"] == "cancelled"
    restarted = ServerResultService(service.root, service.config_path)
    assert restarted.status()["current"]["release_id"] == entry["release_id"]
    assert restarted.dataset(None, "selection-items")["total"] == 1


def test_private_api_pagination_and_command_rejection(tmp_path):
    remote, _, service = setup(tmp_path)
    publish(remote)
    sync(service)
    app = Flask(__name__)
    app.register_blueprint(create_server_results_blueprint(service, "secret"))
    client = app.test_client()
    headers = {"X-Quant-Session": "secret"}
    for path in ["status", "summary", "releases", "data/selection-items", "artifact/daily-report"]:
        assert client.get('/api/server-results/' + path).status_code == 403
        assert client.get('/api/server-results/' + path, headers=headers).status_code == 200
    assert client.get('/api/server-results/data/selection-items?limit=301', headers=headers).status_code == 400
    assert client.post('/api/server-results/sync', json={"command": "rm"}, headers=headers).status_code == 400
    assert client.get('/api/server-results/summary?release_id=../private', headers=headers).status_code == 400


def test_transport_has_fixed_command_and_strict_host_authentication():
    transport = SSHTransport({"host": "server.example", "user": "reader", "tailscale": False})
    command = transport.command()
    assert "StrictHostKeyChecking=yes" in command and "BatchMode=yes" in command
    assert command[-1].endswith('/quant_server/result_reader.py')
    with pytest.raises(ValueError): SSHTransport({"host": "server;rm", "user": "reader"})


def test_transport_decompression_enforces_original_size(tmp_path):
    import gzip
    transport = SSHTransport({"host": "server.example", "user": "reader", "tailscale": False})
    def wire(request, destination, limit, cancel):
        assert request['encoding'] == 'gzip'
        Path(destination).write_bytes(gzip.compress(b'x' * 10000))
    transport._fetch_wire = wire
    with pytest.raises(ValueError, match='decompressed'):
        transport.fetch({'op': 'index'}, tmp_path/'index.json', 10, lambda: None)


def test_download_resumes_only_checksum_verified_files(tmp_path):
    remote, transport, service = setup(tmp_path)
    publish(remote)
    original = transport.fetch
    def interrupted(request, destination, max_bytes, cancel):
        if request.get('artifact_id') == 'evaluation-status':
            raise ConnectionError('interrupted')
        return original(request, destination, max_bytes, cancel)
    transport.fetch = interrupted
    assert sync(service)['status'] == 'failed'
    assert service.status()['current'] is None
    transport.fetch = original
    transport.calls.clear()
    assert sync(service)['status'] == 'completed'
    fetched = {r.get('artifact_id') for r in transport.calls}
    assert 'selection-items' not in fetched and 'market-summary' not in fetched
    assert 'evaluation-status' in fetched


def test_authentication_error_keeps_safe_actionable_link(tmp_path):
    from utils.server_results.transport import diagnose_ssh_error
    remote, transport, service = setup(tmp_path)
    publish(remote)
    def denied(*args):
        raise diagnose_ssh_error('# Tailscale SSH requires an additional check.\nhttps://login.tailscale.com/a/abc123\nprivate-value', 255)
    transport.fetch = denied
    job = sync(service)
    assert job['status'] == 'failed'
    assert job['error_code'] == 'SSH_AUTH_REQUIRED'
    assert job['auth_url'] == 'https://login.tailscale.com/a/abc123'
    assert 'private-value' not in str(job)
    error = diagnose_ssh_error('# Tailscale SSH requires an additional check.\nhttps://attacker.example/a/abc', 255)
    assert error.auth_url is None


def test_missing_optional_artifact_is_retried_without_redownloading_core(tmp_path):
    remote, transport, service = setup(tmp_path)
    entry, manifest = publish(remote)
    folder = remote/'releases'/entry['release_id']
    chart = folder/'chart.png'; chart.write_bytes(b'png-test')
    manifest['artifacts'].append({'artifact_id':'chart-test', 'path':'chart.png', 'bytes':8,
                                  'sha256':digest(chart), 'required':False, 'media_type':'image/png'})
    (folder/'manifest.json').write_text(json.dumps(manifest))
    index = json.loads((remote/'index.json').read_text())
    index['releases'][0]['manifest_sha256'] = digest(folder/'manifest.json')
    (remote/'index.json').write_text(json.dumps(index))
    original = transport.fetch
    def missing(request, destination, max_bytes, cancel):
        if request.get('artifact_id') == 'chart-test': raise ConnectionError('temporarily unavailable')
        return original(request, destination, max_bytes, cancel)
    transport.fetch = missing
    assert sync(service)['status'] == 'completed'
    assert 'chart-test' in service.snapshot()[0]['warning']
    transport.fetch = original; transport.calls.clear()
    assert sync(service)['status'] == 'completed'
    assert service.artifact(None,'chart-test')[0].read_bytes() == b'png-test'
    assert [r['op'] for r in transport.calls] == ['index','artifact']


@pytest.mark.parametrize('number', ['NaN','Infinity','1e999'])
def test_nonfinite_numbers_rejected(tmp_path, number):
    from utils.server_results.contract import read_json
    path = tmp_path/'data.json'; path.write_text('{"value":' + number + '}')
    with pytest.raises(ValueError): read_json(path)
