"""Maintenance: backup, backups, restore, update (with health check + rollback)."""
import json
import os
import re
import shlex
import shutil
import sys
import time
from datetime import datetime, timedelta
import urllib.error
import urllib.parse
import urllib.request

from .ctx import table
from .errors import BlogError
from .wp import audit

q = shlex.quote
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")   # the site 403s curl's default UA


# ====================================================================== backups
def _home(ctx):
    return ctx.t.sh('printf %s "$HOME"', check=True).out.strip()


def backup_root(ctx):
    bd = ctx.site.backup_dir
    return _home(ctx) + bd[1:] if bd.startswith("~") else bd


def _split_root(ctx):
    parent, base = os.path.split(ctx.site.path.rstrip("/"))
    return parent, base


def make_backup(ctx, label="", db=True, files=True):
    ts = time.strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^A-Za-z0-9_-]", "", label or "")
    name = f"{ctx.site.name}-{ts}" + (f"-{label}" if label else "")
    d = f"{backup_root(ctx)}/{name}"
    ctx.t.sh(f"umask 077; mkdir -p {q(d)}", check=True)       # 700: wp-config.php (DB password) is inside
    parts = []
    if db:
        ctx.wp.run("db", "export", f"{d}/db.sql", "--single-transaction", timeout=1800)
        ctx.t.sh(f"set -e; cd {q(d)}; gzip -f db.sql; gzip -t db.sql.gz; "
                 f"zcat db.sql.gz | tail -n 5 | grep -q 'Dump completed'", check=True, timeout=1800)
        parts.append("db.sql.gz")
    if files:
        parent, base = _split_root(ctx)
        ctx.t.sh(f"set -e; umask 077; tar -czf {q(d)}/files.tar.gz -C {q(parent)} {q(base)} || [ $? -eq 1 ]; "
                 f"tar -tzf {q(d)}/files.tar.gz >/dev/null", check=True, timeout=1800)
        parts.append("files.tar.gz")
    sizes = {}
    for line in ctx.t.sh(f"cd {q(d)} && stat -c '%s %n' {' '.join(map(q, parts))}", check=True).out.splitlines():
        n, f = line.split(" ", 1)
        sizes[f] = int(n)
    manifest = {
        "name": name, "dir": d, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "site": ctx.site.name,
        "wordpress": ctx.wp.text("core", "version"), "label": label, "parts": parts, "sizes": sizes,
        "plugins": ctx.wp.json("plugin", "list", "--fields=name,status,version"),
        "themes": ctx.wp.json("theme", "list", "--fields=name,status,version"),
    }
    ctx.t.put(json.dumps(manifest, ensure_ascii=False).encode(), f"{d}/manifest.json")
    audit(ctx.site.name, ["backup", name])
    return manifest


def _mb(n):
    return f"{n / 1048576:.1f} MB"


def cmd_backup(ctx, a):
    db, files = (a.db or not a.files), (a.files or not a.db)
    m = make_backup(ctx, a.label, db=db, files=files)
    if a.pull:
        dest = os.path.join(os.path.expanduser(a.pull), m["name"])
        os.makedirs(dest, mode=0o700, exist_ok=True)
        for f in [*m["parts"], "manifest.json"]:
            path = os.path.join(dest, f)
            with open(path, "wb") as fh:
                fh.write(ctx.t.get(f"{m['dir']}/{f}"))
            os.chmod(path, 0o600)
        m["pulled_to"] = dest

    def human():
        print(f"backup {m['name']}  (WordPress {m['wordpress']})")
        for f, n in m["sizes"].items():
            print(f"  {f:<16} {_mb(n)}")
        print(f"  on server: {m['dir']}" + (f"\n  copied to: {m['pulled_to']}" if a.pull else ""))
        print(f"restore: blog restore {m['name']} --db --files --yes")
    ctx.emit(m, human)


def list_backups(ctx):
    root = backup_root(ctx)
    out = ctx.t.sh(f'cd {q(root)} 2>/dev/null && for d in */; do cat "$d/manifest.json" 2>/dev/null; echo; done').out
    rows = []
    for line in out.splitlines():
        if line.strip().startswith("{"):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return sorted(rows, key=lambda r: r["name"])


def cmd_backups(ctx, a):
    rows = list_backups(ctx)
    flat = [{"name": r["name"], "created": r["created"][:16].replace("T", " "), "wp": r["wordpress"],
             "size": _mb(sum(r["sizes"].values())), "parts": ",".join(p.split(".")[0] for p in r["parts"])} for r in rows]
    ctx.emit(rows, lambda: print(table(flat, ["name", "created", "wp", "size", "parts"]) if flat else "(no backups)"))


def _stamp(name):
    m = re.search(r"-(\d{8})-(\d{6})", name)
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def _local_backups(directory):
    out = []
    if os.path.isdir(directory):
        for n in sorted(os.listdir(directory)):
            mf = os.path.join(directory, n, "manifest.json")
            if os.path.isfile(mf):
                with open(mf) as f:
                    m = json.load(f)
                m["dir"] = os.path.join(directory, n)
                out.append(m)
    return out


def _victims(rows, keep, older_than):
    """Backups beyond the newest `keep` (and, if given, older than N days). Rows are oldest-first."""
    old = rows[:-keep] if keep < len(rows) else []
    if older_than is not None:
        cutoff = datetime.now() - timedelta(days=older_than)
        old = [r for r in old if _stamp(r["name"]) and _stamp(r["name"]) < cutoff]
    return old


def cmd_clean_backups(ctx, a):
    if a.keep < 1:
        raise BlogError("--keep must be at least 1 (the newest backup is never deleted)")
    plans = [("server", list_backups(ctx))]
    if a.local is not None:
        plans.append(("local", _local_backups(os.path.expanduser(a.local))))
    report, freed = [], 0
    for where, rows in plans:
        gone = _victims(rows, a.keep, a.older_than)
        for r in rows:
            report.append({"where": where, "name": r["name"], "size": sum(r["sizes"].values()),
                           "action": "delete" if r in gone else "keep"})
        for r in gone:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", r["name"]) or not r.get("dir"):
                raise BlogError(f"refusing to delete odd backup name {r.get('name')!r}")
            freed += sum(r["sizes"].values())
            if a.yes:
                if where == "server":
                    ctx.t.run(["rm", "-rf", "--", r["dir"]], check=True)
                else:
                    shutil.rmtree(r["dir"])
                audit(ctx.site.name, ["clean-backups", where, r["name"]])

    def human():
        for r in report:
            print(f"  {r['action'].upper():<6} {r['where']:<6} {r['name']}  {_mb(r['size'])}")
        n = sum(1 for r in report if r["action"] == "delete")
        if not report:
            print("(no backups)")
        elif a.yes:
            print(f"\ndeleted {n} backup(s), freed {_mb(freed)}")
        else:
            print(f"\n{n} backup(s) would be deleted ({_mb(freed)}). dry run — apply with --yes" if n else "\nnothing to delete")
    ctx.emit({"applied": bool(a.yes), "backups": report, "freed_bytes": freed if a.yes else 0}, human)


def _find_backup(ctx, ref):
    rows = list_backups(ctx)
    if not rows:
        raise BlogError("no backups found", hint="create one: blog backup")
    if ref == "latest":
        return rows[-1]
    hit = [r for r in rows if r["name"] == ref]
    if not hit:
        raise BlogError(f"no backup named {ref!r}", hint="see: blog backups")
    return hit[0]


def restore_paths(ctx, m, rels):
    """Put individual paths (relative to the WordPress root) back from a backup's tarball."""
    parent, base = _split_root(ctx)
    for rel in rels:
        full = f"{ctx.site.path.rstrip('/')}/{rel}"
        # -p keeps modes; a non-root tar cannot restore the group, so put the web group back explicitly
        ctx.t.sh(f"set -e; rm -rf {q(full)}; tar -xzpf {q(m['dir'])}/files.tar.gz -C {q(parent)} {q(base + '/' + rel)}; "
                 f"chgrp -R {q(ctx.site.web_group)} {q(full)}", check=True, timeout=1800)


def cmd_restore(ctx, a):
    m = _find_backup(ctx, a.name)
    if not (a.db or a.files):
        raise BlogError("say what to restore: --db and/or --files")
    if not a.yes:
        raise BlogError("restore overwrites live data; re-run with --yes",
                        hint=f"would restore {'db ' if a.db else ''}{'files ' if a.files else ''}from {m['name']} ({m['created'][:16]})")
    if a.db and "db.sql.gz" not in m["parts"] or a.files and "files.tar.gz" not in m["parts"]:
        raise BlogError("that backup does not contain the requested part", hint=f"it has: {', '.join(m['parts'])}")
    if not a.no_safety:
        safety = make_backup(ctx, "pre-restore", db=True, files=False)
        ctx.info(f"safety backup of current database: {safety['name']}")
    audit(ctx.site.name, ["restore", m["name"], "db" if a.db else "", "files" if a.files else ""])
    if a.db:
        imp = shlex.join(ctx.wp.argv(["db", "import", "-"]))
        ctx.t.sh(f"set -e; zcat {q(m['dir'])}/db.sql.gz | {imp}", check=True, timeout=1800)
        ctx.info("database restored")
    if a.files:
        parent, _ = _split_root(ctx)
        ctx.t.sh(f"set -e; tar -xzpf {q(m['dir'])}/files.tar.gz -C {q(parent)}; "
                 f"chgrp -R {q(ctx.site.web_group)} {q(ctx.site.path)} || true", check=True, timeout=1800)
        ctx.info("files restored (files added since the backup are left in place)")
    ctx.wp.run("cache", "flush", check=False)
    ctx.emit({"restored": m["name"], "db": a.db, "files": a.files}, None)


# ====================================================================== update
def _nums(v):
    return [int(x) for x in re.findall(r"\d+", v)]


def is_major(old, new):
    o, n = _nums(old), _nums(new)
    if not o or not n:
        return False
    if o[0] > 100000 or n[0] > 100000:      # date-style versions (20251120): treat as routine
        return False
    return o[0] != n[0]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(300000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


ERROR_MARKERS = ("critical error", "fatal error", "parse error", "not usable", "error establishing a database connection")


def bust(url):
    """The site's FastCGI cache serves stale-but-good pages; a query string makes nginx bypass it."""
    return url + ("&" if "?" in url else "?") + f"blogcheck={int(time.time())}"


def parse_checks(site_url, specs):
    """['URL=TEXT', ...] -> [(absolute url, text)]. A leading '/' means 'on this site'."""
    out = []
    for spec in specs or []:
        url, sep, text = spec.partition("=")
        if not sep or not url or not text:
            raise BlogError(f"bad --check {spec!r}", hint="use URL=TEXT, e.g. --check /2008/08/02/prime-numbers-generator/='math-img'")
        if url.startswith("/"):
            url = site_url.rstrip("/") + url
        out.append((url, text))
    return out


def health(ctx, urls, retries=3, checks=()):
    """WordPress boots under wp-cli, and every URL — fetched past the page cache — returns 200 with a complete,
    error-free HTML page. `checks` are extra (url, text) pairs: the page must also contain the text (case-insensitive),
    which is how plugin-specific output (math images, galleries, sitemaps) gets verified."""
    problems = []
    boot = ctx.wp.run("eval", "echo 'wp-ok';", check=False)
    if "wp-ok" not in boot.out:
        problems.append("WordPress fails to load under wp-cli: " + (boot.err or boot.out).strip()[-200:])
    for u in urls:
        for attempt in range(retries):
            status, body = fetch(bust(u))
            low = body.lower()
            found = [m for m in ERROR_MARKERS if m in low]
            if status == 200 and "</html>" in low and not found:
                break
            time.sleep(3)
        else:
            problems.append(f"{u} -> HTTP {status}" + (f", page contains: {', '.join(found)}" if found else ""))
    for u, text in checks:
        for attempt in range(retries):
            status, body = fetch(bust(u))
            if status == 200 and text.lower() in body.lower():
                break
            time.sleep(3)
        else:
            problems.append(f"{u} -> expected text {text!r} " + ("is missing" if status == 200 else f"(HTTP {status})"))
    return problems


def _web_writable(ctx, rel):
    r = ctx.t.sh(f"find {q(ctx.site.path.rstrip('/') + '/' + rel)} -perm -g+w -print -quit 2>/dev/null")
    return bool(r.out.strip())


def build_plan(ctx, targets, allow_major, include_inactive):
    kinds = {"core", "plugins", "themes", "plugin", "theme"}
    names = {t for t in targets if t not in kinds}
    def wanted(kind, name):
        return not targets or kind in targets or kind + "s" in targets or name in names
    plan = []

    if wanted("core", "core"):
        for u in ctx.wp.json("core", "check-update"):
            major = u["update_type"] == "major"
            it = {"kind": "core", "name": "core", "from": ctx.wp.text("core", "version"), "to": u["version"],
                  "major": major, "status": "active", "action": "update", "reason": ""}
            if major and not allow_major:
                it.update(action="hold", reason="major WordPress release: use --allow-major once a backup/test plan exists")
            plan.append(it)
        # one minor + one major at most: keep the highest of each
        best = {}
        for it in plan:
            k = it["major"]
            if k not in best or _nums(it["to"]) > _nums(best[k]["to"]):
                best[k] = it
        plan = list(best.values())

    for kind in ("plugin", "theme"):
        rows = ctx.wp.json(kind, "list", "--update=available", "--fields=name,status,version,update_version")
        for r in rows:
            if not wanted(kind, r["name"]):
                continue
            explicit = r["name"] in names
            it = {"kind": kind, "name": r["name"], "from": r["version"], "to": r["update_version"],
                  "major": is_major(r["version"], r["update_version"]), "status": r["status"], "action": "update", "reason": ""}
            rel = f"wp-content/{kind}s/{r['name']}"
            if r["status"].startswith("inactive") and not include_inactive and not explicit:
                it.update(action="skip", reason="inactive — consider deleting instead of updating")
            elif it["major"] and not (allow_major or explicit):
                it.update(action="hold", reason="major version jump — update by name once you have checked it")
            elif kind == "plugin" and not explicit and _web_writable(ctx, rel):
                it.update(action="hold", reason="folder holds web-writable data that an update would wipe")
            plan.append(it)
    return plan


def _plan_human(ctx, plan):
    print(f"Update plan for {ctx.site.name}:")
    if not plan:
        print("  nothing to update")
    for it in plan:
        tag = it["action"].upper()
        major = " (major)" if it["major"] else ""
        line = f"  {tag:<6} {it['kind']:<7} {it['name']:<32} {it['from']} -> {it['to']}{major}  [{it['status']}]"
        print(line + (f"\n         {it['reason']}" if it["reason"] else ""))


def _harden(ctx, rel_paths):
    """Original layout: group = web group, no group-write, no world access."""
    web = ctx.site.path.rstrip("/")
    cmds = [f"chgrp -R {q(ctx.site.web_group)} {q(web + '/' + rel)} && chmod -R g-w,o-rwx {q(web + '/' + rel)}"
            for rel in rel_paths]
    ctx.t.sh("; ".join(cmds), check=False)


def purge_page_cache(ctx):
    """Delete only THIS site's entries from the shared nginx FastCGI cache (needs sudo). Returns the count removed."""
    host = urllib.parse.urlparse(ctx.site.url or ctx.wp.text("option", "get", "siteurl")).netloc
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise BlogError(f"odd host name {host!r}; refusing to purge")
    pat = "^KEY: https?(GET|HEAD)" + host.replace(".", "\\.") + "/"
    script = ('files=$(grep -rlaE -- "$1" "$2" 2>/dev/null)\n'
              'n=$(printf "%s\\n" "$files" | grep -c .)\n'
              '[ "$n" -gt 0 ] && printf "%s\\n" "$files" | xargs -d "\\n" rm -f --\n'
              'echo "$n"\n')
    r = ctx.t.run(["sudo", "-n", "bash", "-s", "--", pat, ctx.site.cache_dir], stdin=script)
    if not r.ok:
        raise BlogError("cannot purge the page cache (needs passwordless sudo on the cache directory)", hint=r.err.strip()[-200:])
    return int((r.out.strip().splitlines() or ["0"])[-1] or 0)


def cmd_cache_purge(ctx, a):
    n = purge_page_cache(ctx)
    audit(ctx.site.name, ["cache-purge", n])
    ctx.emit({"purged": n}, f"purged {n} cached page(s) of {ctx.site.name} (other sites' cache untouched)")


def fix_uploads_group(ctx):
    """wp-cli runs as `debian`, so files/dirs it (or a plugin it loads) creates under uploads/ get group `debian`;
    PHP-FPM (www-data) then cannot write there. Put them in the web group, group-writable like the rest of uploads/."""
    up = q(ctx.site.path.rstrip("/") + "/wp-content/uploads")
    g = q(ctx.site.web_group)
    r = ctx.t.sh(f"find {up} ! -group {g} -print -exec chgrp {g} {{}} + 2>/dev/null | wc -l; "
                 f"find {up} -type d ! -perm -g+w -exec chmod g+w {{}} + 2>/dev/null", check=False)
    return int((r.out.strip().splitlines() or ["0"])[0] or 0)


def _writable_dirs(ctx, rel):
    """Topmost directories under `rel` that the web process can write to (runtime-generated data)."""
    out = ctx.t.sh(f"cd {q(ctx.site.path)} && find {q(rel)} -type d -perm -g+w 2>/dev/null").out.split("\n")
    dirs = sorted(d for d in out if d.strip())
    return [d for d in dirs if not any(d != o and d.startswith(o + "/") for o in dirs)]


def _restore_data_dirs(ctx, m, dirs):
    """Put web-generated data back from the backup (merge: does not delete anything new), keep it web-writable."""
    parent, base = _split_root(ctx)
    for d in dirs:
        full = f"{ctx.site.path.rstrip('/')}/{d}"
        ctx.t.sh(f"set -e; mkdir -p {q(full)}; tar -xzpf {q(m['dir'])}/files.tar.gz -C {q(parent)} {q(base + '/' + d)}; "
                 f"chgrp -R {q(ctx.site.web_group)} {q(full)}; chmod -R g+w {q(full)}", check=True, timeout=900)


def _paths_for(it):
    if it["kind"] == "core":
        return ["wp-admin", "wp-includes"]
    return [f"wp-content/{it['kind']}s/{it['name']}"]


def run_update(ctx, a):
    """Plan (and with a.yes apply) updates. Returns (result dict, human-printer)."""
    plan = build_plan(ctx, a.targets, a.allow_major, a.include_inactive)
    todo = [p for p in plan if p["action"] == "update"]
    result = {"plan": plan, "applied": False, "results": []}
    if not a.yes or not todo:
        return result, (lambda: (_plan_human(ctx, plan), print("\ndry run — nothing changed. Apply with: blog update --yes" if todo else "")))
    pause = getattr(a, "pause", False)
    if pause and (ctx.json or not sys.stdin.isatty()):
        raise BlogError("--pause needs an interactive terminal", hint="agents: update one item at a time by name instead (blog update SLUG --yes)")
    if not ctx.json:
        _plan_human(ctx, plan)

    posts = ctx.wp.json("post", "list", "--post_status=publish", "--posts_per_page=1", "--fields=url")
    site_url = ctx.site.url or ctx.wp.text("option", "get", "siteurl")
    urls = [site_url] + [p["url"] for p in posts]
    checks = parse_checks(site_url, getattr(a, "check", None))
    pre = health(ctx, urls, retries=1, checks=checks)
    if pre:
        raise BlogError("site is unhealthy BEFORE updating — refusing to continue", hint="; ".join(pre))

    backup = None
    if not a.no_backup:
        ctx.info("\nbacking up (database + all files)…")
        backup = make_backup(ctx, "pre-update")
        ctx.info(f"  {backup['name']}  {_mb(sum(backup['sizes'].values()))}")
        result["backup"] = backup["name"]

    failed = False
    for it in todo:
        label = f"{it['kind']} {it['name']} {it['from']} -> {it['to']}"
        ctx.info(f"updating {label} …")
        keep_dirs = _writable_dirs(ctx, _paths_for(it)[0]) if it["kind"] != "core" else []
        if keep_dirs and not backup:
            raise BlogError(f"{it['name']} holds web-generated data ({', '.join(keep_dirs)}); it can only be updated with a backup",
                            hint="drop --no-backup")
        try:
            if it["kind"] == "core":
                ctx.wp.run("core", "update", f"--version={it['to']}", mutating=True, timeout=1800)
                ctx.wp.run("core", "update-db", mutating=True)
                _harden(ctx, ["wp-admin", "wp-includes", "wp-content/languages"])
                ctx.t.sh(f"for f in *.php *.html *.txt; do chgrp {q(ctx.site.web_group)} \"$f\"; chmod g-w,o-rwx \"$f\"; done", check=False)
            else:
                ctx.wp.run(it["kind"], "update", it["name"], mutating=True, timeout=900)
                _harden(ctx, _paths_for(it))
                if keep_dirs:
                    ctx.info(f"  restoring web-generated data: {', '.join(keep_dirs)}")
                    _restore_data_dirs(ctx, backup, keep_dirs)
            if it["kind"] == "plugin" and it["name"] == "enlighter":    # the update wiped its generated JS/CSS
                from .migrate import build_highlighter_assets
                ctx.info(f"  rebuilt highlighter assets: {', '.join(build_highlighter_assets(ctx))}")
            ctx.wp.run("cache", "flush", check=False)
            purged = purge_page_cache(ctx)
            ctx.info(f"  purged {purged} cached page(s)")
            fixed = fix_uploads_group(ctx)
            if fixed:
                ctx.info(f"  fixed group on {fixed} new path(s) under uploads/")
            new = ctx.wp.text("core", "version") if it["kind"] == "core" else ctx.wp.text(it["kind"], "get", it["name"], "--field=version")
            problems = health(ctx, urls, checks=checks)
            err = None
        except BlogError as e:
            new, problems, err = None, [e.message], e
        entry = {**it, "now": new, "ok": not problems, "problems": problems}
        if problems:
            failed = True
            if it["kind"] != "core" and backup:
                ctx.info(f"  FAILED: {'; '.join(problems)}\n  rolling back {it['name']} from {backup['name']} …")
                restore_paths(ctx, backup, _paths_for(it))
                ctx.wp.run("cache", "flush", check=False)
                again = health(ctx, urls, checks=checks)
                entry["rolled_back"] = True
                entry["healthy_after_rollback"] = not again
                if again:
                    ctx.info(f"  still unhealthy after rollback: {'; '.join(again)}")
            else:
                ctx.info(f"  FAILED: {'; '.join(problems)}")
                if backup:
                    ctx.info(f"  restore everything with: blog restore {backup['name']} --db --files --yes")
            result["results"].append(entry)
            break
        ctx.info(f"  ok ({new})")
        result["results"].append(entry)
        if pause and it is not todo[-1]:
            if input(f"  inspect the site now. Continue with the next update ({len(todo) - len(result['results'])} left)? [y/N] ").strip().lower() != "y":
                ctx.info("  stopped by you; the remaining updates were not applied")
                break

    result["applied"] = True
    result["ok"] = not failed
    if not failed:
        cs = ctx.wp.run("core", "verify-checksums", check=False)
        result["core_checksums_ok"] = cs.ok
    done = [r for r in result["results"] if r["ok"]]

    def human():
        print(f"\n{len(done)}/{len(todo)} update(s) applied" + ("" if not failed else " — STOPPED at the first failure"))
        if "core_checksums_ok" in result:
            print(f"core checksums: {'OK' if result['core_checksums_ok'] else 'MISMATCH'}")
        held = [p for p in plan if p["action"] == "hold"]
        if held:
            print("held back (need a decision): " + ", ".join(f"{p['name']} {p['from']}->{p['to']}" for p in held))
    return result, human


def cmd_update(ctx, a):
    result, human = run_update(ctx, a)
    ctx.emit(result, human)
    return 0 if result.get("ok", True) else 1


# ====================================================================== auto-update
def prune_server_backups(ctx, keep):
    """Delete server backups beyond the newest `keep`. Returns the names removed."""
    gone = _victims(list_backups(ctx), keep, None)
    for r in gone:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", r["name"]) or not r.get("dir"):
            raise BlogError(f"refusing to delete odd backup name {r.get('name')!r}")
        ctx.t.run(["rm", "-rf", "--", r["dir"]], check=True)
        audit(ctx.site.name, ["prune-backup", r["name"]])
    return [r["name"] for r in gone]


def _mail(ctx, to, subject, body):
    r = ctx.t.run(["mail", "-s", subject, to], stdin=body)
    return r.ok


def _held_key(plan):
    return sorted(f"{p['kind']}:{p['name']}:{p['to']}" for p in plan if p["action"] == "hold")


def cmd_auto_update(ctx, a):
    """Unattended-safe updater: applies only non-major updates (backup first, health-checked, auto-rollback),
    prunes old backups, and mails only when something needs attention."""
    import argparse
    import fcntl
    from pathlib import Path
    state = Path.home() / ".local" / "state" / "blog" / "auto-update"
    state.mkdir(parents=True, exist_ok=True)
    lock = open(state / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another auto-update is already running; exiting")
        return 0
    last_file = state / "last.json"
    prev = json.loads(last_file.read_text()) if last_file.exists() else {}
    to = a.notify or ctx.site.notify_email
    keep = a.keep_backups or ctx.site.keep_backups

    ns = argparse.Namespace(targets=[], yes=not a.dry_run, allow_major=False, include_inactive=False, no_backup=False)
    error = None
    try:
        result, human = run_update(ctx, ns)
    except BlogError as e:
        result, human, error = {"plan": [], "results": [], "applied": False, "ok": False}, None, f"{e.message}" + (f" ({e.hint})" if e.hint else "")
    plan = result["plan"]
    applied = [r for r in result["results"] if r.get("ok")]
    failed = [r for r in result["results"] if not r.get("ok")]
    pruned = []
    if result.get("applied") and result.get("ok") and not a.dry_run:
        pruned = prune_server_backups(ctx, keep)
    held = _held_key(plan)
    new_held = [h for h in held if h not in prev.get("held", [])]
    from . import security
    try:
        warnings = security.host_warnings_for(ctx)
    except BlogError:
        warnings = []
    new_warnings = [w for w in warnings if w not in prev.get("warnings", [])]

    lines = []
    def L(x=""):
        lines.append(x)
    if error:
        L(f"auto-update could not run: {error}")
    for r in failed:
        L(f"FAILED: {r['kind']} {r['name']} {r['from']} -> {r['to']}: {'; '.join(r['problems'])}")
        L("  rolled back automatically." if r.get("rolled_back") else f"  NOT rolled back (core?) — restore with: blog restore {result.get('backup', '<backup>')} --db --files --yes")
    for r in applied:
        L(f"updated: {r['kind']} {r['name']} {r['from']} -> {r['now'] or r['to']}")
    if held:
        L("waiting for your decision (held back on purpose): " + ", ".join(f"{p['name']} {p['from']}->{p['to']}" for p in plan if p["action"] == "hold"))
    if pruned:
        L(f"old backups removed: {', '.join(pruned)}")
    for w in warnings:
        L(("NEW WARNING: " if w in new_warnings else "warning (unchanged): ") + w)
    if not lines:
        L("nothing to do")

    # when to mail: anything that failed, anything applied, or newly held items; silence otherwise
    reasons = []
    if error or failed:
        reasons.append("FAILED")
    if applied:
        reasons.append(f"{len(applied)} updated")
    if new_held:
        reasons.append("needs a decision")
    if new_warnings:
        reasons.append("warning")
    subject = f"[blog] {ctx.site.name}: " + (", ".join(reasons) if reasons else "no change")
    body = "\n".join(lines) + f"\n\n-- blog auto-update on {ctx.t.kind} at {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
    mailed = False
    if reasons and to and not a.dry_run:
        mailed = _mail(ctx, to, subject, body)
    report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "site": ctx.site.name, "ok": not (error or failed), "error": error,
              "applied": [f"{r['kind']}:{r['name']}:{r['to']}" for r in applied], "failed": [f"{r['kind']}:{r['name']}" for r in failed],
              "held": held, "warnings": warnings, "pruned": pruned, "mailed": mailed, "subject": subject}
    if not a.dry_run:
        (state / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(report, indent=1))
        last_file.write_text(json.dumps(report, indent=1))

    def out():
        if a.dry_run:
            if human:
                human()
            print(f"\n[dry run] would mail: {bool(reasons and to)} — subject: {subject!r}" + ("" if to else " (no recipient configured)"))
        else:
            print(subject + "\n" + "\n".join(lines))
    ctx.emit(report, out)
    return 0 if report["ok"] else 1


# ====================================================================== deploy
def cmd_deploy(ctx, a):
    """Copy this tool to the server so it can run there (transport = local), and write the server-side config."""
    import subprocess
    from pathlib import Path
    from .transport import SSHTransport
    if not isinstance(ctx.t, SSHTransport):
        raise BlogError("already running on the target; nothing to deploy")
    home = _home(ctx)
    dest = a.dest.replace("~", home, 1) if a.dest.startswith("~") else a.dest
    repo = Path(__file__).resolve().parent.parent
    ssh_cmd = shlex.join(ctx.t.ssh_argv("x")[:-2])
    ctx.t.sh(f"mkdir -p {q(dest)}", check=True)
    r = subprocess.run(["rsync", "-az", "--delete", "--exclude", ".git", "--exclude", "__pycache__", "--exclude", "*.pyc",
                        "-e", ssh_cmd, f"{repo}/", f"{ctx.t.host}:{dest}/"], capture_output=True, text=True)
    if r.returncode:
        raise BlogError("rsync failed", hint=r.stderr.strip()[-300:])
    cfg_path = f"{home}/.config/blog/config.toml"
    existing = ctx.t.sh(f"test -f {q(cfg_path)} && echo yes || echo no").out.strip() == "yes"
    wrote = False
    if not existing or a.write_config:
        s = ctx.site
        lines = [f'default = "{s.name}"', "", f"[sites.{s.name}]", 'transport = "auto"', f'path = "{s.path}"', f'wp_bin = "{s.wp_bin}"',
                 f'author = "{s.author}"', f'default_status = "{s.default_status}"', f'keep_backups = {s.keep_backups}']
        if s.notify_email:
            lines.append(f'notify_email = "{s.notify_email}"')
        ctx.t.sh(f"mkdir -p {q(home + '/.config/blog')}", check=True)
        ctx.t.put(("\n".join(lines) + "\n").encode(), cfg_path)
        wrote = True
    audit(ctx.site.name, ["deploy", dest])
    out = {"dest": dest, "config_written": wrote, "config": cfg_path, "run": f"{dest}/bin/blog"}
    ctx.emit(out, f"deployed to {ctx.t.host}:{dest}\n  config: {cfg_path} ({'written' if wrote else 'kept existing'})\n  run on the server: {dest}/bin/blog status")


# ====================================================================== spam users
def spam_candidates(ctx, keep=()):
    """Subscriber-only accounts that have NEVER done anything: no posts, no comments, no login session."""
    p = ctx.wp.text("db", "prefix")
    sql = (f"SELECT u.ID FROM {p}users u "
           f"JOIN {p}usermeta c ON c.user_id=u.ID AND c.meta_key='{p}capabilities' AND c.meta_value='a:1:{{s:10:\"subscriber\";b:1;}}' "
           f"WHERE NOT EXISTS (SELECT 1 FROM {p}comments cm WHERE cm.user_id=u.ID) "
           f"AND NOT EXISTS (SELECT 1 FROM {p}posts po WHERE po.post_author=u.ID) "
           f"AND NOT EXISTS (SELECT 1 FROM {p}usermeta s WHERE s.user_id=u.ID AND s.meta_key='session_tokens' AND s.meta_value<>'') "
           f"ORDER BY u.ID")
    ids = [int(x) for x in ctx.wp.text("db", "query", sql, "--skip-column-names").split() if x.strip().isdigit()]
    return [i for i in ids if i not in set(keep)], p


def _recent_backup(ctx, minutes=60):
    rows = [r for r in list_backups(ctx) if "db.sql.gz" in r["parts"]]
    if not rows:
        return None
    st = _stamp(rows[-1]["name"])
    return rows[-1] if st and (datetime.now() - st) < timedelta(minutes=minutes) else None


def cmd_prune_spam_users(ctx, a):
    ids, p = spam_candidates(ctx, keep=a.keep or [])
    total_sub = int(ctx.wp.text("user", "list", "--role=subscriber", "--format=count"))
    kept = total_sub - len(ids)
    sample = ctx.wp.json("user", "list", f"--include={','.join(map(str, ids[:6]))}", "--fields=ID,user_login,user_email,user_registered") if ids else []
    todo = ids[:a.limit] if a.limit else ids
    info = {"subscribers": total_sub, "will_delete": len(todo), "kept": total_sub - len(todo), "applied": False}

    if not a.yes:
        def human():
            print(f"{total_sub} subscriber accounts; {len(ids)} have never posted, commented or logged in.")
            print(f"  would delete {len(todo)}; the other {total_sub - len(todo)} stay (they have activity"
                  + (f", or are in --keep" if a.keep else "") + (", or are beyond --limit" if a.limit and len(ids) > a.limit else "") + ").")
            for u in sample:
                print(f"    e.g. #{u['ID']} {u['user_login']} <{u['user_email']}> registered {u['user_registered'][:10]}")
            print("\ndry run — apply with: blog prune-spam-users --yes   (requires a backup from the last hour)")
        ctx.emit(info, human)
        return 0

    if not _recent_backup(ctx):
        raise BlogError("no database backup from the last hour", hint="run: blog backup --label pre-cleanup")
    done = 0
    t0 = time.time()
    for i in range(0, len(todo), a.batch):
        chunk = todo[i:i + a.batch]
        ctx.wp.run("user", "delete", *chunk, "--yes", mutating=True, timeout=1800)
        done += len(chunk)
        ctx.info(f"  deleted {done}/{len(todo)}  ({time.time() - t0:.0f}s)")
    info.update(applied=True, deleted=done)
    ctx.wp.run("cache", "flush", check=False)
    remaining = int(ctx.wp.text("user", "list", "--role=subscriber", "--format=count"))
    info["subscribers_remaining"] = remaining
    ctx.emit(info, f"deleted {done} spam accounts; {remaining} subscriber account(s) remain")
