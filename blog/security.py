"""Read-only health tooling: host warnings, `blog security`, `blog snapshot`."""
import hashlib
import json
import re
import shlex
import time
import urllib.error
import urllib.parse
from datetime import datetime
from html import unescape
from pathlib import Path

from .ctx import table
from .errors import BlogError

q = shlex.quote
DISK_WARN_PCT = 85
TLS_WARN_DAYS = 21          # Let's Encrypt renews at 30 days left; under 21 means renewal is failing
BACKUP_WARN_DAYS = 30
ABANDONED_DAYS = 730


# ------------------------------------------------------------------ host warnings (status + auto-update mail)
def host_warnings(st):
    """Pure function: status-like dict -> list of human warnings."""
    w = []
    http = st.get("http")
    if http is not None and http.get("status") != 200:
        w.append(f"site returned HTTP {http.get('status')}")
    days = st.get("tls_days_left")
    if days is not None and days < TLS_WARN_DAYS:
        w.append(f"TLS certificate expires in {days} days (automatic renewal looks broken)")
    disk = st.get("disk")
    if disk:
        try:
            pct = int(str(disk[3]).rstrip("%"))
            if pct >= DISK_WARN_PCT:
                w.append(f"disk is {pct}% full ({disk[2]} available)")
        except ValueError:
            pass
    if "last_backup_days" in st:
        age = st["last_backup_days"]
        if age is None:
            w.append("no backups exist on the server")
        elif age > BACKUP_WARN_DAYS:
            w.append(f"newest backup is {age} days old (run `blog backup`)")
    return w


def backup_age_days(ctx):
    from .maint import list_backups
    rows = list_backups(ctx)
    if not rows:
        return None
    newest = max(datetime.fromisoformat(r["created"][:19]) for r in rows)
    return max(0, (datetime.now() - newest).days)


def host_warnings_for(ctx):
    """The cheap subset used by the unattended updater (no WP-CLI round trips except the backup list)."""
    from .ops import _tls_days
    url = ctx.site.url or ctx.wp.text("option", "get", "siteurl")
    df = ctx.t.run(["df", "-h", "--output=size,used,avail,pcent", ctx.site.path]).out.strip().splitlines()
    st = {"tls_days_left": _tls_days(url), "disk": df[-1].split() if len(df) > 1 else None,
          "last_backup_days": backup_age_days(ctx)}
    return host_warnings(st)


# ------------------------------------------------------------------ blog security
def _row(level, name, detail):
    return {"level": level, "check": name, "detail": detail}   # level: ok | info | warn | fail


MISMATCH_THRESHOLD = 20   # a plugin with this many "added" files is a different build, not a planted file


def parse_checksum_output(text):
    """`wp ... verify-checksums` table -> (modified files, added php files, added other files, notes).
    Files that a plugin generates at run time show up as 'added'; only code is interesting."""
    modified, added_php, added_other, unverifiable = [], [], [], []
    for line in text.splitlines():
        parts = line.split("\t")
        if line.startswith("Warning:"):
            unverifiable.append(line[len("Warning:"):].strip())
        elif len(parts) >= 3 and parts[0] != "plugin_name":
            name, path, why = parts[0], parts[1], parts[2]
            if "added" in why.lower():
                generated = re.search(r"(^|/)cache/", path)    # e.g. enlighter/cache/enlighterjs.min.js, built on purpose by `blog highlight-cache`
                code = re.search(r"\.(php|phtml|phar|js)$", path, re.I) and not generated
                (added_php if code else added_other).append(f"{name}/{path}")
            else:
                modified.append(f"{name}/{path}: {why}")
    return modified, added_php, added_other, unverifiable


def _top(path):
    parts = path.lstrip("./").split("/")
    return "/".join(parts[:3]) if parts[0] == "wp-content" else "/".join(parts[:1])


def cmd_security(ctx, a):
    rows = []
    add = lambda level, name, detail: rows.append(_row(level, name, detail))   # noqa: E731
    wp, root = ctx.wp, ctx.site.path
    group = ctx.site.web_group

    r = wp.run("core", "verify-checksums", check=False)
    add("ok" if r.ok else "fail", "core files", "match wordpress.org checksums" if r.ok else (r.err or r.out).strip()[-300:])

    r = wp.run("plugin", "verify-checksums", "--all", check=False)
    modified, added_php, added_other, unverifiable = parse_checksum_output(r.out + "\n" + r.err)
    from collections import Counter
    added_n = Counter(p.split("/")[0] for p in added_php + added_other)
    mismatch = sorted(n for n, c in added_n.items() if c > MISMATCH_THRESHOLD and any(p.startswith(n + "/") for p in added_php))
    stray = [p for p in added_php if p.split("/")[0] not in mismatch]
    if modified or stray:
        add("fail", "plugin files", "; ".join((modified + [f"added code: {p}" for p in stray])[:6]))
    else:
        note = f"; {len(added_other)} generated non-code file(s) ignored" if added_other else ""
        add("ok", "plugin files", "no modified or unexpected code files" + note)
    for n in mismatch:
        add("warn", f"plugin {n}", f"installed files differ from the wordpress.org package ({added_n[n]} extra files incl. "
                                   f"{sum(1 for p in added_php if p.startswith(n + '/'))} PHP) — an old/other build, or tampering; "
                                   "worth reinstalling from a trusted copy (ask first)")
    if unverifiable:
        add("info", "plugin checksums", "; ".join(sorted(set(unverifiable)))[:300])

    admins = wp.json("user", "list", "--role=administrator", "--fields=ID,user_login,user_registered")
    names = [u["user_login"] for u in admins]
    if any(n.lower() in ("admin", "administrator", "root") for n in names):
        add("warn", "administrators", f"{', '.join(names)} — a guessable login name is a brute-force target "
                                      "(rename or demote it only with the owner's say-so)")
    else:
        add("ok", "administrators", ", ".join(names))

    cfg = ctx.t.sh(f"cd {q(root)} && stat -c '%a %U:%G' wp-config.php", check=False).out.strip()
    mode = cfg.split()[0] if cfg else ""
    if not mode:
        add("warn", "wp-config.php", "could not read its permissions")
    elif mode[-1] != "0":
        add("fail", "wp-config.php", f"readable by everyone on the server ({cfg})")
    else:
        add("ok", "wp-config.php", cfg)

    dbg = wp.text("eval", "echo (defined('WP_DEBUG')&&WP_DEBUG?'on':'off');", check=False).strip()
    add("warn" if dbg == "on" else "ok", "WP_DEBUG", dbg or "unknown")

    php = ctx.t.sh(f"cd {q(root)} && find wp-content/uploads \\( -iname '*.php' -o -iname '*.phtml' -o -iname '*.phar' \\) | head -5",
                   check=False).out.split()
    add("fail" if php else "ok", "code in uploads", ("PHP files in uploads/: " + ", ".join(php)) if php else "no PHP files in uploads/")

    ww = ctx.t.sh(f"cd {q(root)} && find . ! -type l -perm -o+w | head -5", check=False).out.split()
    add("fail" if ww else "ok", "world-writable", ", ".join(ww) if ww else "nothing is world-writable")

    wrong = ctx.t.sh(f"cd {q(root)} && find . -path ./wp-content/uploads -prune -o ! -group {q(group)} ! -type l -print | head -50",
                     check=False).out.split()
    leftovers = [p for p in wrong if p.startswith("./wp-content/upgrade")]
    other = [p for p in wrong if p not in leftovers]
    if other:
        add("warn", "file group", f"{len(other)} path(s) not in group {group}: " + ", ".join(other[:4]))
    else:
        add("ok", "file group", f"everything outside uploads/ is in group {group}")
    if leftovers:
        add("info", "wp-cli leftovers", "wp-content/upgrade* is owned by the SSH user's group (harmless; WordPress cannot self-update here anyway)")

    gw = ctx.t.sh(f"cd {q(root)} && find . -path ./wp-content/uploads -prune -o ! -type l -perm -g+w -print | head -2000",
                  check=False).out.split()
    tops = sorted({_top(p) for p in gw})
    unexpected = [t for t in tops if not t.startswith("wp-content/plugins/")]
    if unexpected:
        add("warn", "web-writable code", "the web server can write to: " + ", ".join(unexpected))
    elif tops:
        add("info", "web-writable dirs", "plugin data directories the web user writes to: " + ", ".join(tops))
    else:
        add("ok", "web-writable dirs", "none outside uploads/")

    pend = (len(wp.json("core", "check-update")) +
            len(wp.json("plugin", "list", "--update=available", "--fields=name")) +
            len(wp.json("theme", "list", "--update=available", "--fields=name")))
    add("warn" if pend else "ok", "pending updates", f"{pend} update(s) waiting (blog update)" if pend else "everything is current")

    if not a.offline:
        for level, name, detail in _plugin_reputation(wp.json("plugin", "list", "--fields=name,status,version"),
                                                      accepted=getattr(ctx.site, "accept_closed", ())):
            add(level, name, detail)

    from .ops import _tls_days
    url = ctx.site.url or wp.text("option", "get", "siteurl")
    df = ctx.t.run(["df", "-h", "--output=size,used,avail,pcent", root]).out.strip().splitlines()
    host = {"tls_days_left": _tls_days(url), "disk": df[-1].split() if len(df) > 1 else None,
            "last_backup_days": backup_age_days(ctx)}
    hw = host_warnings(host)
    add("warn" if hw else "ok", "host", "; ".join(hw) if hw else
        f"TLS {host['tls_days_left']} days left, disk {host['disk'][3] if host['disk'] else '?'}, newest backup {host['last_backup_days']} day(s) old")

    def human():
        mark = {"ok": "ok  ", "info": "info", "warn": "WARN", "fail": "FAIL"}
        width = max(len(r_["check"]) for r_ in rows)
        for r_ in rows:
            print(f"  {mark[r_['level']]}  {r_['check']:<{width}} {r_['detail']}")
        bad = [r_ for r_ in rows if r_["level"] in ("warn", "fail")]
        print(f"\n{len(bad)} item(s) need attention" if bad else "\nall clear")
    ctx.emit(rows, human)
    return 1 if any(r_["level"] == "fail" for r_ in rows) else 0


def closed_notice(info):
    """wordpress.org's answer for a closed plugin -> 'closed Sep 22, 2022. Reason: Security Issue' (else None)."""
    if isinstance(info, dict) and info.get("error") == "closed":
        text = re.sub(r"<[^>]+>", "", unescape(info.get("description", "")))
        text = text.replace("This plugin has been ", "").replace(" and is not available for download", "")
        return text.strip().rstrip(".") or "closed"
    return None


def _plugin_reputation(plugins, accepted=()):
    """Ask wordpress.org (the same public API wp-cli uses for updates; only plugin slugs are sent) whether a plugin
    was closed or has gone unmaintained. There is no key-less vulnerability feed, so this is a proxy, not a CVE scan."""
    import urllib.request
    out = []
    for p in plugins:
        slug = p["name"]
        try:
            req = urllib.request.Request(f"https://api.wordpress.org/plugins/info/1.0/{urllib.parse.quote(slug)}.json",
                                         headers={"User-Agent": "blog-cli (plugin age check)"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                info = json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as e:   # closed plugins answer 404 with a JSON body that says why
            try:
                info = json.loads(e.read().decode() or "null")
            except ValueError:
                info = None
            if not isinstance(info, dict):
                out.append(("info", f"plugin {slug}", f"wordpress.org lookup failed (HTTP {e.code})"))
                continue
        except Exception as e:  # noqa: BLE001
            out.append(("info", f"plugin {slug}", f"wordpress.org lookup failed ({e.__class__.__name__})"))
            continue
        time.sleep(0.5)
        closed = closed_notice(info)
        if closed:
            serious = "security" in closed.lower() and p["status"] == "active"
            if slug in accepted:     # owner decision recorded in the config: the vendor ships its own updates
                out.append(("info", f"plugin {slug}", f"closed on wordpress.org ({closed}) — accepted in config; "
                                                      f"updates come from the vendor (installed {p['version']})"))
                continue
            out.append(("fail" if serious else "warn", f"plugin {slug}",
                        f"CLOSED on wordpress.org: {closed} — it receives no updates from there ({p['status']}, installed {p['version']})"))
            continue
        if not isinstance(info, dict) or "last_updated" not in info:
            out.append(("info", f"plugin {slug}", "not in the wordpress.org directory (commercial, custom or removed)"))
            continue
        try:
            age = (datetime.now() - datetime.strptime(info["last_updated"][:10], "%Y-%m-%d")).days
        except ValueError:
            continue
        active = p["status"] == "active"
        if age > ABANDONED_DAYS:
            out.append(("warn" if active else "info", f"plugin {slug}",
                        f"last updated {age // 365} years ago upstream ({p['status']}) — possibly abandoned"))
        else:
            out.append(("ok", f"plugin {slug}", f"maintained upstream (updated {age} days ago)"))
    return out


# ------------------------------------------------------------------ blog snapshot
STATE = Path.home() / ".local" / "state" / "blog" / "snapshots"
_TAGS = {"images": r"<img\b", "scripts": r"<script\b", "stylesheets": r"<link[^>]+stylesheet", "links": r"<a\s"}


def page_metrics(status, body):
    low = body.lower()
    visible = re.sub(r"(?is)<head\b.*?</head>|<(script|style|noscript)\b.*?</\1>|<!--.*?-->|<[^>]+>", " ", body)
    visible = re.sub(r"\s+", " ", unescape(visible)).strip()
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    from .maint import ERROR_MARKERS
    out = {"http": status, "bytes": len(body), "title": unescape(m.group(1)).strip() if m else None,
           "text_sha": hashlib.sha1(visible.encode()).hexdigest()[:12], "text_chars": len(visible),
           "errors": [e for e in ERROR_MARKERS if e in low],
           "math_images": len(re.findall(r"wpmathpub/math-img|phpmathpublisher/img", low))}
    for k, rx in _TAGS.items():
        out[k] = len(re.findall(rx, body, re.I))
    return out


def diff_metrics(a, b):
    """-> list of (url, metric, before, after) for everything that changed."""
    out = []
    for url in sorted(set(a) | set(b)):
        if url not in a or url not in b:
            out.append((url, "page", "present" if url in a else "absent", "present" if url in b else "absent"))
            continue
        for k in sorted(set(a[url]) | set(b[url])):
            if a[url].get(k) != b[url].get(k):
                out.append((url, k, a[url].get(k), b[url].get(k)))
    return out


def cmd_snapshot(ctx, a):
    from .maint import bust, fetch
    STATE.mkdir(parents=True, exist_ok=True)
    if a.action == "ls":
        rows = [{"name": p.stem, "taken": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                 "pages": len(json.loads(p.read_text())["pages"])} for p in sorted(STATE.glob("*.json"))]
        ctx.emit(rows, lambda: print(table(rows, ["name", "taken", "pages"]) if rows else "(no snapshots)"))
        return 0
    if a.action == "take":
        if len(a.names) != 1:
            raise BlogError("usage: blog snapshot take NAME [--url PATH]... [-n 5]")
        name = a.names[0]
        site = (ctx.site.url or ctx.wp.text("option", "get", "siteurl")).rstrip("/")
        posts = ctx.wp.json("post", "list", "--post_status=publish", f"--posts_per_page={a.n}", "--fields=url")
        urls = [site + "/"] + [p["url"] for p in posts] + [(site + u if u.startswith("/") else u) for u in a.url or []]
        urls = list(dict.fromkeys(urls))
        pages = {}
        for u in urls:   # gentle: one request per second, only public content pages, never login/admin/feed or 404 probes
            status, body = fetch(bust(u))
            pages[u] = page_metrics(status, body)
            time.sleep(1)
        snap = {"taken": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "site": site, "pages": pages}
        (STATE / f"{name}.json").write_text(json.dumps(snap, indent=1))
        bad = {u: m for u, m in pages.items() if m["http"] != 200 or m["errors"]}
        ctx.emit(snap, lambda: print(f"snapshot '{name}': {len(pages)} page(s) saved to {STATE / (name + '.json')}"
                                     + ("".join(f"\n  PROBLEM {u}: HTTP {m['http']} {m['errors']}" for u, m in bad.items()))))
        return 1 if bad else 0
    if a.action == "diff":
        if len(a.names) != 2:
            raise BlogError("usage: blog snapshot diff BEFORE AFTER")
        snaps = []
        for n in a.names:
            f = STATE / f"{n}.json"
            if not f.exists():
                raise BlogError(f"no snapshot named {n!r}", hint="blog snapshot ls")
            snaps.append(json.loads(f.read_text())["pages"])
        changes = diff_metrics(*snaps)
        # script/stylesheet counts and bytes legitimately move with plugin updates; content changes are the alarm
        alarm = [c for c in changes if c[1] in ("http", "text_sha", "text_chars", "math_images", "title", "errors", "images", "page")]
        def human():
            if not changes:
                print("identical: same status, text, images and assets on every page")
                return
            for u, k, x, y in changes:
                print(f"  {'!' if (u, k, x, y) in alarm else ' '} {u}  {k}: {x} -> {y}")
            print("\n" + (f"{len(alarm)} content change(s) marked '!' — inspect them" if alarm else "only asset/size changes (normal after updates)"))
        ctx.emit([dict(zip(("url", "metric", "before", "after"), c)) for c in changes], human)
        return 1 if alarm else 0
    raise BlogError("action must be take, diff or ls")
