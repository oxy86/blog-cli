"""Run commands on the target: locally, or over SSH. Same interface for both."""
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass

from .errors import BlogError


@dataclass
class Result:
    code: int
    out: str
    err: str
    raw: bytes = b""

    @property
    def ok(self):
        return self.code == 0


class Transport:
    kind = "?"

    def __init__(self, path, debug=False):
        self.path = path
        self.debug = debug

    def _exec(self, argv, stdin, timeout):
        raise NotImplementedError

    def run(self, argv, stdin=None, check=False, timeout=600):
        """Run argv (a list; never a shell string) with cwd = WordPress root."""
        if isinstance(stdin, str):
            stdin = stdin.encode()
        if self.debug:
            print(f"[{self.kind}] $ {shlex.join(argv)}", file=sys.stderr)
        try:
            p = self._exec(argv, stdin, timeout)
        except subprocess.TimeoutExpired:
            raise BlogError(f"timed out after {timeout}s: {shlex.join(argv)}")
        r = Result(p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), p.stdout)
        if check and not r.ok:
            raise BlogError(f"command failed ({r.code}): {shlex.join(argv)}", hint=_tail(r.err or r.out))
        return r

    def sh(self, script, **kw):
        """Run a shell snippet on the target (cwd = WordPress root)."""
        return self.run(["bash", "-c", script], **kw)

    def put(self, data: bytes, remote_path):
        self.run(["bash", "-c", 'cat > "$1"', "_", remote_path], stdin=data, check=True)

    def get(self, remote_path) -> bytes:
        return self.run(["cat", remote_path], check=True).raw


class LocalTransport(Transport):
    kind = "local"

    def _exec(self, argv, stdin, timeout):
        return subprocess.run(argv, input=stdin if stdin is not None else b"", cwd=self.path,
                              capture_output=True, timeout=timeout)


class SSHTransport(Transport):
    kind = "ssh"

    def __init__(self, host, path, debug=False, ssh_options=None):
        super().__init__(path, debug)
        self.host = host
        self.ssh_options = ssh_options or []

    def ssh_argv(self, remote_cmd):
        ctl = os.path.expanduser("~/.ssh/cm-blog-%C")
        return ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", f"ControlPath={ctl}",
                "-o", "ControlPersist=120", *self.ssh_options, self.host, remote_cmd]

    def remote_command(self, argv):
        return f"cd {shlex.quote(self.path)} && {shlex.join(argv)}"

    def _exec(self, argv, stdin, timeout):
        return subprocess.run(self.ssh_argv(self.remote_command(argv)), input=stdin if stdin is not None else b"",
                              capture_output=True, timeout=timeout)


def make_transport(site, debug=False):
    kind = site.resolved_transport()
    if kind == "local":
        return LocalTransport(site.path, debug)
    if kind == "ssh":
        if not site.host:
            raise BlogError(f"site {site.name!r} uses ssh but has no 'host'")
        return SSHTransport(site.host, site.path, debug, site.ssh_options)
    raise BlogError(f"bad transport {kind!r} (use auto, local or ssh)")


def _tail(text, n=8):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return "\n".join(lines[-n:])
