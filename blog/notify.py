"""`blog notify`: read-only watch. Never changes the site; sends one e-mail when something NEW needs the owner's attention."""
import fcntl
import json
import re
import time
from pathlib import Path

from .errors import BlogError

STATE = Path.home() / ".local" / "state" / "blog" / "notify"
QUIET_CHECKS = {"pending updates", "host"}      # reported through their own, finer-grained items below


def _norm(text):
    return re.sub(r"\d+", "N", text)           # "expires in 12 days" and "in 11 days" are the same warning


def collect(ctx, offline=False):
    """-> [{key, section, text}]. Keys are stable across runs so that standing items are mailed once, not daily."""
    from .maint import build_plan
    from .security import host_warnings_for, security_rows
    items = []
    for it in build_plan(ctx, [], True, True):
        major = " (MAJOR)" if it["major"] else ""
        extra = f" - {it['reason']}" if it["action"] == "hold" and it["reason"] else ""
        items.append({"key": f"update:{it['kind']}:{it['name']}:{it['to']}", "section": "Updates waiting",
                      "text": f"{it['kind']} {it['name']} {it['from']} -> {it['to']}{major} [{it['status']}]{extra}"})
    for r in security_rows(ctx, offline):
        if r["level"] in ("warn", "fail") and r["check"] not in QUIET_CHECKS:
            items.append({"key": f"security:{r['check']}:{r['level']}", "section": "Security review",
                          "text": f"{r['level'].upper()} {r['check']}: {r['detail']}"})
    for w in host_warnings_for(ctx):
        items.append({"key": f"host:{_norm(w)}", "section": "Server", "text": w})
    return items


def decide(items, state, now, heartbeat_days=30):
    """Pure decision. -> (send, new_items, standing_items, reason)."""
    seen = set(state.get("keys", []))
    new = [i for i in items if i["key"] not in seen]
    standing = [i for i in items if i["key"] in seen]
    if new:
        return True, new, standing, "new"
    last = state.get("last_sent")
    if heartbeat_days and (last is None or now - last > heartbeat_days * 86400):
        return True, new, standing, "heartbeat"
    return False, new, standing, "quiet"


def render(site, new, standing, reason, host_kind="server"):
    if reason == "heartbeat" and not new:
        subject = f"[blog] {site}: all quiet" if not standing else f"[blog] {site}: {len(standing)} open item(s), nothing new"
    else:
        subject = f"[blog] {site}: {len(new)} new item(s) need attention"
    lines = []

    def block(title, items):
        if not items:
            return
        lines.append(title)
        section = None
        for i in items:
            if i["section"] != section:
                section = i["section"]
                lines.append(f"  {section}:")
            lines.append(f"    - {i['text']}")
        lines.append("")
    block("NEW since the last notice", new)
    block("Still open (already reported before)", standing)
    if reason == "heartbeat" and not new and not standing:
        lines += ["Nothing needs attention. (Monthly heartbeat: this mail proves the watcher is still running.)", ""]
    lines += ["Nothing was changed on the site. To act:",
              "  blog status               overview", "  blog update               show the update plan (add --yes to apply)",
              "  blog security             full read-only review", ""]
    return subject, "\n".join(lines)


def cmd_notify(ctx, a):
    from .maint import _mail
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another notify run is in progress; exiting")
        return 0
    to = a.to or ctx.site.notify_email
    if not to and not a.dry_run:
        raise BlogError("no recipient", hint="blog notify --to you@example.org   (or notify_email in the config)")
    sfile = STATE / f"{ctx.site.name}.json"
    state = json.loads(sfile.read_text()) if sfile.exists() else {}
    items = collect(ctx, a.offline)
    send, new, standing, reason = decide(items, state, time.time(), a.heartbeat_days)
    subject, body = render(ctx.site.name, new, standing, reason)
    mailed = False
    if send and not a.dry_run:
        mailed = _mail(ctx, to, subject, body)
        if not mailed:
            raise BlogError(f"could not send the mail to {to}", hint="state not updated, will retry on the next run")
    if not a.dry_run:     # remember what we have seen (only after a successful send, or when there was nothing to send)
        state.update(keys=[i["key"] for i in items], last_run=time.time())
        if mailed:
            state["last_sent"] = time.time()
        sfile.write_text(json.dumps(state, indent=1))
    res = {"items": len(items), "new": len(new), "standing": len(standing), "reason": reason,
           "would_send": send, "mailed": mailed, "to": to, "subject": subject if send else None}

    def human():
        if send:
            print(("[dry run] would mail " if a.dry_run else "mailed ") + f"{to or '(no recipient)'}\nSubject: {subject}\n\n{body}")
        else:
            print(f"quiet: {len(items)} open item(s), none new; no mail ({'dry run' if a.dry_run else 'state saved'})")
    ctx.emit(res, human)
    return 0
