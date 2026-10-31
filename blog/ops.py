"""Maintenance + swiss-army commands: status, doctor, wp, sql, sh, eval, get, put, init."""
import json
import re
import socket
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone

from .config import write_config
from .errors import BlogError

READ_ONLY_SQL = re.compile(r"^\s*(select|show|describe|desc|explain)\b", re.I)
WP_CLI_PHAR = "https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"


# ------------------------------------------------------------------ passthrough tools
def cmd_wp(ctx, a):
    args = [x for x in a.wp_args if x != "--"] if a.wp_args and a.wp_args[0] == "--" else a.wp_args
    stdin = sys.stdin.buffer.read() if a.stdin else None
    mutating = bool(args) and args[0] not in ("help", "--info", "cli") and not re.match(r"^(list|get|check|version|status|info|is-installed)", (args + [""])[1] if len(args) > 1 else "")
    r = ctx.wp.run(*args, stdin=stdin, check=False, mutating=mutating)
    sys.stdout.write(r.out)
    sys.stderr.write(r.err + ("\n" if r.err and not r.err.endswith("\n") else ""))
    return r.code


def cmd_sql(ctx, a):
    stmts = [s for s in a.query.split(";") if s.strip()]
    if not a.write and not all(READ_ONLY_SQL.match(s) for s in stmts):
        raise BlogError("read-only by default: only SELECT/SHOW/DESCRIBE/EXPLAIN allowed",
                        hint="re-run with --write if you really mean to change data (take `blog backup` first)")
    args = ["db", "query", a.query]
    if not a.write:
        r = ctx.wp.run(*args)
        sys.stdout.write(r.out)
        return
    ctx.wp.run(*args, mutating=True)


def cmd_sh(ctx, a):
    r = ctx.t.sh(a.script)
    audit_needed = True
    if audit_needed:
        from .wp import audit
        audit(ctx.site.name, ["sh", a.script])
    sys.stdout.write(r.out)
    sys.stderr.write(r.err)
    return r.code


def cmd_eval(ctx, a):
    r = ctx.wp.run("eval", a.code, mutating=True, check=False)
    sys.stdout.write(r.out)
    sys.stderr.write(r.err)
    return r.code


def cmd_eval_file(ctx, a):
    with open(a.file, "rb") as f:
        data = f.read()
    tmp = ctx.t.sh("mktemp --suffix=.php", check=True).out.strip()
    try:
        ctx.t.put(data, tmp)
        r = ctx.wp.run("eval-file", tmp, *a.args, mutating=True, check=False)
    finally:
        ctx.t.run(["rm", "-f", tmp])
    sys.stdout.write(r.out)
    sys.stderr.write(r.err)
    return r.code


def cmd_get(ctx, a):
    data = ctx.t.get(a.remote)
    if a.local == "-":
        sys.stdout.buffer.write(data)
    else:
        with open(a.local, "wb") as f:
            f.write(data)
        ctx.info(f"{a.remote} -> {a.local} ({len(data)} bytes)")


def cmd_put(ctx, a):
    with open(a.local, "rb") as f:
        data = f.read()
    ctx.t.put(data, a.remote)
    from .wp import audit
    audit(ctx.site.name, ["put", a.local, a.remote])
    ctx.info(f"{a.local} -> {a.remote} ({len(data)} bytes)")


# ------------------------------------------------------------------ status
def _http_check(url):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "blog-cli/0.1"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"status": resp.status, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "error": str(e)}


def _tls_days(url):
    m = re.match(r"https://([^/:]+)", url)
    if not m:
        return None
    try:
        with socket.create_connection((m.group(1), 443), timeout=10) as s:
            with ssl.create_default_context().wrap_socket(s, server_hostname=m.group(1)) as ss:
                exp = ss.getpeercert()["notAfter"]
        return (datetime.strptime(exp, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
    except Exception:  # noqa: BLE001
        return None


def collect_status(ctx, deep=False):
    wp = ctx.wp
    st = {"site": ctx.site.name, "transport": ctx.t.kind}
    st["url"] = ctx.site.url or wp.text("option", "get", "siteurl")
    st["wordpress"] = wp.text("core", "version")
    updates = wp.json("core", "check-update") if True else []
    st["core_updates"] = [{"version": u["version"], "type": u["update_type"]} for u in updates]
    st["plugin_updates"] = [{"name": p["name"], "from": p["version"], "to": p["update_version"], "status": p["status"]}
                            for p in wp.json("plugin", "list", "--update=available", "--fields=name,status,version,update_version")]
    st["theme_updates"] = [{"name": p["name"], "from": p["version"], "to": p["update_version"], "status": p["status"]}
                           for p in wp.json("theme", "list", "--update=available", "--fields=name,status,version,update_version")]
    st["posts"] = {s: int(wp.text("post", "list", "--post_type=post", f"--post_status={s}", "--format=count"))
                   for s in ("publish", "draft", "future", "pending")}
    st["users"] = {"total": int(wp.text("user", "list", "--format=count")),
                   "subscribers": int(wp.text("user", "list", "--role=subscriber", "--format=count"))}
    st["php"] = wp.text("eval", "echo PHP_VERSION;")
    size = wp.text("db", "size", "--size_format=mb", check=False)
    st["db_size"] = f"{size} MB" if size.isdigit() else (size or None)
    st["cron_events"] = int(wp.text("cron", "event", "list", "--format=count", check=False) or 0)
    df = ctx.t.run(["df", "-h", "--output=size,used,avail,pcent", ctx.site.path]).out.strip().splitlines()
    st["disk"] = df[-1].split() if len(df) > 1 else None
    st["http"] = _http_check(st["url"])
    st["tls_days_left"] = _tls_days(st["url"])
    if deep:
        r = wp.run("core", "verify-checksums", check=False)
        st["core_checksums_ok"] = r.ok
    return st


def cmd_status(ctx, a):
    st = collect_status(ctx, a.deep)

    def human():
        warn = []
        print(f"{st['url']}  [{st['transport']}]")
        print(f"  WordPress   {st['wordpress']}   PHP {st['php']}   DB {st['db_size'] or '?'}   disk used/avail {st['disk'][1]}/{st['disk'][2]} ({st['disk'][3]})" if st["disk"] else "")
        h = st["http"]
        print(f"  HTTP        {h.get('status')} in {h.get('ms', '?')} ms" + (f"  ({h['error']})" if h.get("error") else ""))
        if h.get("status") != 200:
            warn.append("site did not return HTTP 200")
        if st["tls_days_left"] is not None:
            print(f"  TLS         {st['tls_days_left']} days left")
            if st["tls_days_left"] < 14:
                warn.append("TLS certificate expires soon")
        print(f"  Posts       " + ", ".join(f"{k} {v}" for k, v in st["posts"].items() if v or k in ("publish", "draft")))
        print(f"  Users       {st['users']['total']} ({st['users']['subscribers']} subscribers)")
        if st["users"]["subscribers"] > 100:
            warn.append(f"{st['users']['subscribers']} subscriber accounts — likely spam")
        if st["core_updates"]:
            print("  Core        updates: " + ", ".join(f"{u['version']} ({u['type']})" for u in st["core_updates"]))
            warn.append("WordPress core update available")
        else:
            print("  Core        up to date")
        for kind in ("plugin", "theme"):
            ups = st[f"{kind}_updates"]
            if ups:
                print(f"  {kind.title()+'s':<11} {len(ups)} update(s):")
                for u in ups:
                    print(f"      {u['name']:<34} {u['from']} -> {u['to']}  [{u['status']}]")
        if "core_checksums_ok" in st:
            print(f"  Checksums   core {'OK' if st['core_checksums_ok'] else 'MISMATCH'}")
        if warn:
            print("\nattention:\n" + "\n".join(f"  ! {w}" for w in warn))
    ctx.emit(st, human)


# ------------------------------------------------------------------ doctor / init
def cmd_doctor(ctx, a):
    checks = []

    def check(name, fn):
        try:
            detail = fn()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as e:  # noqa: BLE001
            checks.append({"check": name, "ok": False, "detail": str(getattr(e, "message", e))})

    check("transport", lambda: f"{ctx.t.kind}" + (f" -> {ctx.site.host}" if ctx.t.kind == "ssh" else ""))
    check("wordpress root", lambda: ctx.t.run(["test", "-f", "wp-config.php"], check=True) and ctx.site.path)
    wp_ok = {}

    def wp_version():
        v = ctx.wp.text("cli", "version")
        wp_ok["ok"] = True
        return v
    check("wp-cli", wp_version)
    if not wp_ok and a.fix:
        def install():
            dest = ctx.site.wp_bin
            script = (f'set -e; mkdir -p "$(dirname {dest})"; t=$(mktemp -d); cd "$t"; '
                      f'curl -fsSLO {WP_CLI_PHAR}; curl -fsSL {WP_CLI_PHAR}.sha512 -o sum; '
                      f'echo "$(cut -d" " -f1 sum)  wp-cli.phar" | sha512sum -c -; php wp-cli.phar --info >/dev/null; '
                      f'install -m 755 wp-cli.phar {dest}; rm -rf "$t"')
            ctx.t.sh(script, check=True)
            return f"installed {dest}"
        check("install wp-cli", install)
    def db():
        out = [l for l in ctx.wp.text("db", "check", check=True).splitlines() if not l.startswith("Success")]
        bad = [l for l in out if l.strip() and not l.rstrip().endswith("OK")]
        if bad:
            raise BlogError(f"{len(bad)} table(s) not OK", hint=bad[0])
        return f"{len(out)} tables OK"
    check("database", db)
    def uploads():
        from .maint import fix_uploads_group
        n = int(ctx.t.sh(f"find wp-content/uploads ! -group {ctx.site.web_group} | wc -l").out.strip() or 0)
        if n and a.fix:
            fix_uploads_group(ctx)
            return f"{n} path(s) had the wrong group — fixed"
        if n:
            raise BlogError(f"{n} path(s) under uploads/ are not in group {ctx.site.web_group} (PHP cannot write there)",
                            hint="run: blog doctor --fix")
        return f"all in group {ctx.site.web_group}"
    check("uploads group", uploads)
    check("author", lambda: f"{ctx.site.author} (id {ctx.wp.author_id()})")
    check("site url", lambda: ctx.wp.text("option", "get", "siteurl"))

    def human():
        for c in checks:
            print(f"  {'ok ' if c['ok'] else 'FAIL'}  {c['check']:<16} {c['detail']}")
    ctx.emit(checks, human)
    return 0 if all(c["ok"] for c in checks) else 1


def cmd_init(args):
    p = write_config(args.config, args.name, args.host, args.path, args.wp_bin, args.author, args.force)
    print(f"wrote {p}\nnext: blog doctor")
