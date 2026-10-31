"""Thin wrapper around wp-cli running on the target through a Transport."""
import json
import re
import time
from pathlib import Path

from .errors import BlogError

AUDIT_LOG = Path.home() / ".local" / "state" / "blog" / "audit.log"
NOISE = re.compile(r"^(PHP )?(Deprecated|Notice|Warning): ", re.I)


def audit(site, argv):
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "site": site, "argv": argv}, ensure_ascii=False) + "\n")
    except OSError:
        pass


class WP:
    def __init__(self, site, transport):
        self.site = site
        self.t = transport
        self._author_id = None

    def argv(self, args):
        return [*self.site.wp_argv, f"--path={self.site.path}", *map(str, args)]

    def run(self, *args, stdin=None, mutating=False, check=True, timeout=600):
        if mutating:
            audit(self.site.name, [a for a in args])
        r = self.t.run(self.argv(args), stdin=stdin, timeout=timeout)
        r.err = "\n".join(l for l in r.err.splitlines() if not NOISE.match(l))
        r.out = "\n".join(l for l in r.out.splitlines() if not NOISE.match(l)) + ("\n" if r.out.endswith("\n") else "")
        if check and not r.ok:
            msg = (r.err or r.out).strip().splitlines()
            msg = [m for m in msg if m.strip()]
            raise BlogError(msg[-1].removeprefix("Error: ") if msg else f"wp exited {r.code}",
                            hint=f"wp {' '.join(map(str, args))}")
        return r

    def text(self, *args, **kw):
        return self.run(*args, **kw).out.strip()

    def json(self, *args, **kw):
        out = self.text(*args, "--format=json", **kw)
        if not out:
            return []
        # tolerate stray lines before the JSON document
        start = min([i for i in (out.find("["), out.find("{")) if i >= 0], default=-1)
        if start == -1:  # e.g. "Success: WordPress is at the latest version." -> nothing to list
            return []
        try:
            return json.loads(out[start:] if start > 0 else out)
        except json.JSONDecodeError:
            raise BlogError("could not parse wp-cli JSON output", hint=out[:300])

    # ---- helpers
    def author_id(self):
        if self._author_id is None:
            login = self.site.author
            if not login:
                raise BlogError("no author configured", hint="set `author = \"<wp login>\"` in the site config")
            self._author_id = int(self.text("user", "get", login, "--field=ID"))
        return self._author_id

    def resolve_post(self, ref, post_type="any"):
        ref = str(ref)
        if ref.isdigit():
            return int(ref)
        ids = self.text("post", "list", f"--post_type={post_type}", "--post_status=any", f"--name={ref}", "--field=ID").split()
        if not ids:
            raise BlogError(f"no post with slug {ref!r}")
        return int(ids[0])

    def post_info(self, pid):
        rows = self.json("post", "list", "--post_type=any", "--post_status=any", f"--post__in={pid}",
                         "--fields=ID,post_title,post_name,post_status,post_type,post_date,url")
        if not rows:
            raise BlogError(f"post {pid} not found")
        return rows[0]

    def terms(self, taxonomy):
        return self.json("term", "list", taxonomy, "--fields=term_id,name,slug,count")
