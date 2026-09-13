"""Bounded, cancellable SSH reads; authentication stays outside the browser."""
from __future__ import annotations

import json
import gzip
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

REMOTE_COMMAND = "sudo -n -u quantserver /opt/a-share-quant-server/.venv/bin/python /opt/a-share-quant-server/quant_server/result_reader.py"


class ServerReadError(ConnectionError):
    def __init__(self, message, code, auth_url=None):
        super().__init__(message)
        self.code = code
        self.auth_url = auth_url


def diagnose_ssh_error(stderr, returncode):
    """Expose actionable categories, never arbitrary SSH output or credentials."""
    if 'Tailscale SSH requires an additional check' in stderr:
        match = re.search(r'https://login\.tailscale\.com/a/[A-Za-z0-9]+(?=\s|$)', stderr)
        return ServerReadError('Tailscale SSH 需要重新验证登录，完成验证后重试',
                               'SSH_AUTH_REQUIRED', match.group(0) if match else None)
    if 'Permission denied' in stderr or 'sudo:' in stderr:
        return ServerReadError('SSH 身份或服务器只读组件执行权限不足', 'SSH_PERMISSION_DENIED')
    if 'result export unavailable or invalid request' in stderr:
        return ServerReadError('服务器发布文件不可用，请核验该制品是否已过期', 'EXPORT_UNAVAILABLE')
    if returncode == 255:
        return ServerReadError('SSH 连接中断，请检查 Tailscale 网络后重试', 'SSH_CONNECTION_FAILED')
    return ServerReadError('服务器读取组件执行失败', 'READER_FAILED')


class SSHTransport:
    def __init__(self, config):
        self.config = dict(config)
        host, user = config.get("host", ""), config.get("user", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
            raise ValueError("invalid SSH host")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", user):
            raise ValueError("invalid SSH user")
        port = config.get("port", 22)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid SSH port")

    def command(self):
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("OpenSSH client unavailable")
        args = [ssh, "-T", "-C", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5",
                "-o", "ServerAliveCountMax=2", "-o", "ForwardAgent=no",
                "-o", "ClearAllForwardings=yes", "-p", str(self.config.get("port", 22))]
        key = self.config.get("identity_file")
        if key:
            args += ["-i", str(Path(key).expanduser()), "-o", "IdentitiesOnly=yes"]
        if self.config.get("tailscale", True):
            app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
            executable = str(app) if app.is_file() else shutil.which("tailscale")
            if not executable:
                raise RuntimeError("Tailscale client unavailable")
            args += ["-o", f"ProxyCommand={shlex.quote(executable)} nc %h %p"]
        args += [f"{self.config['user']}@{self.config['host']}", REMOTE_COMMAND]
        return args

    def fetch(self, request, destination, max_bytes, cancel):
        """Use application gzip even when Tailscale SSH negotiates no compression."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.wire-', dir=destination.parent) as temporary:
            wire = Path(temporary) / 'payload.gz'
            self._fetch_wire({**request, "encoding": "gzip"}, wire, max_bytes + 65536, cancel)
            with gzip.open(wire, "rb") as source, destination.open("wb") as output:
                count = 0
                while chunk := source.read(262144):
                    cancel()
                    count += len(chunk)
                    if count > max_bytes:
                        raise ValueError("decompressed file exceeds declared size")
                    output.write(chunk)
        cancel()

    def _fetch_wire(self, request, destination, max_bytes, cancel):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=output, stderr=errors,
                                       start_new_session=os.name != "nt")
            try:
                process.stdin.write((json.dumps(request) + "\n").encode())
                process.stdin.close()
                deadline = time.monotonic() + 600
                last_progress, previous_size = time.monotonic(), 0
                while process.poll() is None:
                    cancel()
                    size = os.fstat(output.fileno()).st_size
                    if size != previous_size:
                        last_progress, previous_size = time.monotonic(), size
                    if size > max_bytes:
                        raise ValueError("remote file exceeds declared size")
                    if time.monotonic() > deadline or time.monotonic() - last_progress > 60:
                        raise TimeoutError("server result read timed out")
                    time.sleep(0.1)
                cancel()
                if process.returncode:
                    errors.seek(0)
                    raise diagnose_ssh_error(errors.read(65536).decode('utf-8', errors='replace'), process.returncode)
                if os.fstat(output.fileno()).st_size > max_bytes:
                    raise ValueError("remote file exceeds declared size")
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        process.kill()
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                process.wait()
