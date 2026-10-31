"""Editorial commands: new, edit, ls, show, publish, unpublish, trash, media, tags, cats."""
import datetime as dt
import os
import subprocess
import sys
import tempfile

from . import md
from .ctx import table
from .errors import BlogError

STATUSES = ("draft", "publish", "pending", "private", "future")


# ------------------------------------------------------------------ helpers
def _csv(values):
    out = []
    for v in values or []:
        out += [x.strip() for x in v.split(",") if x.strip()]
    return out


def _editor(initial, suffix):
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w+", suffix=suffix, delete=False, encoding="utf-8") as f:
        f.write(initial)
        path = f.name
    try:
        subprocess.run([*editor.split(), path], check=True)
        return open(path, encoding="utf-8").read()
    finally:
        os.unlink(path)


def _read_source(a, allow_editor=True, template=""):
    """body from --body, --file (or '-'), piped stdin, or $EDITOR."""
    if a.file:
        if a.file == "-":
            return sys.stdin.read()
        with open(os.path.expanduser(a.file), encoding="utf-8") as f:
            return f.read()
    if a.body is not None:
        return a.body
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    if allow_editor and sys.stdin.isatty():
        text = _editor(template, ".md")
        if text.strip() == template.strip():
            raise BlogError("nothing written, aborted")
        return text
    return None


def _match_terms(wanted, existing, kind, create_ok):
    """Map user-typed names onto existing terms (case-insensitive name or slug)."""
    by = {}
    for t in existing:
        by[t["name"].lower()] = t["name"]
        by[t["slug"].lower()] = t["name"]
    out, unknown = [], []
    for w in wanted:
        hit = by.get(w.lower())
        if hit:
            out.append(hit)
        elif create_ok:
            out.append(w)
        else:
            unknown.append(w)
    if unknown:
        names = ", ".join(sorted({t["name"] for t in existing}))
        plural = "categories" if kind == "category" else kind + "s"
        raise BlogError(f"unknown {kind}: {', '.join(unknown)}", hint=f"existing {plural}: {names}")
    return out


def _set_terms(ctx, pid, taxonomy, names, append=False):
    if names:
        verb = "add" if append else "set"
        ctx.wp.run("post", "term", verb, pid, taxonomy, *names, mutating=True)


def _status(a, ctx, meta):
    if a.publish:
        return "publish"
    if a.schedule:
        return "future"
    return a.status or meta.get("status") or ctx.site.default_status


def _parse_date(s):
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("T", " "))
    except ValueError:
        raise BlogError(f"bad date {s!r}", hint='use "YYYY-MM-DD HH:MM" in the blog\'s timezone')
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _summary(ctx, pid, note=None):
    info = ctx.wp.post_info(pid)
    info["terms"] = {"tags": ctx.wp.text("post", "term", "list", pid, "post_tag", "--field=name").splitlines(),
                     "categories": ctx.wp.text("post", "term", "list", pid, "category", "--field=name").splitlines()}

    def human():
        if note:
            print(note)
        print(f"  id      {info['ID']}\n  title   {info['post_title']}\n  status  {info['post_status']}\n"
              f"  tags    {', '.join(info['terms']['tags']) or '-'}\n  cats    {', '.join(info['terms']['categories']) or '-'}\n"
              f"  url     {info['url']}")
    ctx.emit(info, human)


# ------------------------------------------------------------------ new
def cmd_new(ctx, a):
    template = "# Title\n\nWrite here in Markdown. Delete this line.\n"
    text = _read_source(a, template=template)
    if text is None:
        raise BlogError("no body", hint="use --body, --file, pipe text in, or run in a terminal to open $EDITOR")
    meta, body = md.split_frontmatter(text)
    title = a.title or meta.get("title")
    if not title:  # first "# Heading" becomes the title
        lines = body.lstrip("\n").split("\n")
        if lines and lines[0].startswith("# "):
            title, body = lines[0][2:].strip(), "\n".join(lines[1:]).lstrip("\n")
    if not title:
        raise BlogError("no title", hint='give it as the first argument, `title:` front matter, or a leading "# Heading"')

    tags = _csv(a.tags) or _csv([meta.get("tags", "")])
    cats = _csv(a.cat) or (_csv([meta.get("category") or meta.get("categories", "")]))
    status = _status(a, ctx, meta)
    if status not in STATUSES:
        raise BlogError(f"bad status {status!r}", hint=f"one of: {', '.join(STATUSES)}")
    date = _parse_date(a.schedule or meta["date"]) if (a.schedule or meta.get("date")) else None
    content = body if a.html else md.convert(body, breaks=a.breaks)
    slug = a.slug or meta.get("slug")

    if cats:
        cats = _match_terms(cats, ctx.wp.terms("category"), "category", create_ok=False)
    if tags:
        tags = _match_terms(tags, ctx.wp.terms("post_tag"), "tag", create_ok=True)

    if a.dry_run:
        ctx.emit({"title": title, "status": status, "slug": slug, "date": date, "tags": tags,
                  "categories": cats, "content": content},
                 lambda: print(f"title:  {title}\nstatus: {status}  date: {date or '-'}  slug: {slug or '-'}\n"
                               f"tags:   {', '.join(tags) or '-'}\ncats:   {', '.join(cats) or '-'}\n\n{content}"))
        return

    args = ["post", "create", "-", "--porcelain", f"--post_title={title}", f"--post_status={status}",
            f"--post_author={ctx.wp.author_id()}"]
    if slug:
        args.append(f"--post_name={slug}")
    if date:
        args.append(f"--post_date={date}")
    pid = int(ctx.wp.text(*args, stdin=content, mutating=True))
    _set_terms(ctx, pid, "post_tag", tags)
    _set_terms(ctx, pid, "category", cats)
    _summary(ctx, pid, note=f"created {status} post")
    if status == "draft":
        ctx.info("\n(draft — not public. Publish with: blog publish %d)" % pid)


# ------------------------------------------------------------------ edit
def cmd_edit(ctx, a):
    pid = ctx.wp.resolve_post(a.post)
    changed = False
    fields = []
    if a.title:
        fields.append(f"--post_title={a.title}")
    if a.slug:
        fields.append(f"--post_name={a.slug}")
    if a.status:
        fields.append(f"--post_status={a.status}")
    if a.schedule:
        fields += [f"--post_date={_parse_date(a.schedule)}", "--post_status=future"]

    content = None
    if a.file or a.body is not None or not sys.stdin.isatty():
        text = _read_source(a, allow_editor=False)
        if text is not None:
            _, body = md.split_frontmatter(text)
            content = body if a.html else md.convert(body, breaks=a.breaks)
    elif not (fields or a.add_tag or a.remove_tag or a.add_cat or a.remove_cat or a.tags or a.cat):
        old = ctx.wp.text("post", "get", pid, "--field=post_content")
        new = _editor(old + "\n", ".html")
        if new.strip() != old.strip():
            content = new

    if fields or content is not None:
        ctx.wp.run("post", "update", pid, *fields, *(["-"] if content is not None else []),
                   stdin=content, mutating=True)
        changed = True
    if a.tags:
        _set_terms(ctx, pid, "post_tag", _match_terms(_csv(a.tags), ctx.wp.terms("post_tag"), "tag", True))
        changed = True
    if a.add_tag:
        _set_terms(ctx, pid, "post_tag", _match_terms(_csv(a.add_tag), ctx.wp.terms("post_tag"), "tag", True), append=True)
        changed = True
    if a.remove_tag:
        ctx.wp.run("post", "term", "remove", pid, "post_tag", *_csv(a.remove_tag), mutating=True)
        changed = True
    if a.cat:
        _set_terms(ctx, pid, "category", _match_terms(_csv(a.cat), ctx.wp.terms("category"), "category", False))
        changed = True
    if a.add_cat:
        _set_terms(ctx, pid, "category", _match_terms(_csv(a.add_cat), ctx.wp.terms("category"), "category", False), append=True)
        changed = True
    if a.remove_cat:
        ctx.wp.run("post", "term", "remove", pid, "category", *_csv(a.remove_cat), mutating=True)
        changed = True
    _summary(ctx, pid, note="updated" if changed else "no changes")


# ------------------------------------------------------------------ read
def cmd_ls(ctx, a):
    args = ["post", "list", f"--post_type={a.type}", f"--post_status={a.status or 'any'}", f"--posts_per_page={a.n}",
            "--orderby=date", "--order=DESC", "--fields=ID,post_title,post_status,post_date,url"]
    if a.tag:
        args.append(f"--tag={a.tag}")
    if a.cat:
        args.append(f"--category_name={a.cat}")
    if a.search:
        args.append(f"--s={a.search}")
    rows = ctx.wp.json(*args)
    for r in rows:
        r["post_date"] = r["post_date"][:16]
    ctx.emit(rows, lambda: print(table(rows, ["ID", "post_status", "post_date", "post_title"],
                                       ["ID", "STATUS", "DATE", "TITLE"]) if rows else "(none)"))


def cmd_show(ctx, a):
    pid = ctx.wp.resolve_post(a.post)
    if a.raw:
        print(ctx.wp.text("post", "get", pid, "--field=post_content"))
        return
    _summary(ctx, pid)


def _set_status(ctx, a, status, note):
    pid = ctx.wp.resolve_post(a.post)
    ctx.wp.run("post", "update", pid, f"--post_status={status}", mutating=True)
    _summary(ctx, pid, note=note)


def cmd_publish(ctx, a):
    _set_status(ctx, a, "publish", "published")


def cmd_unpublish(ctx, a):
    _set_status(ctx, a, "draft", "moved back to draft")


def cmd_trash(ctx, a):
    pid = ctx.wp.resolve_post(a.post)
    if a.force and not a.yes:
        raise BlogError("permanent delete needs --yes", hint=f"blog trash {a.post} --force --yes")
    ctx.wp.run("post", "delete", pid, *(["--force"] if a.force else []), mutating=True)
    ctx.emit({"id": pid, "deleted": bool(a.force), "trashed": not a.force},
             f"{'deleted permanently' if a.force else 'moved to trash'}: {pid}")


# ------------------------------------------------------------------ taxonomy / media
def cmd_tags(ctx, a):
    rows = sorted(ctx.wp.terms("post_tag"), key=lambda r: -int(r["count"]))
    ctx.emit(rows, lambda: print(table(rows, ["name", "slug", "count"])))


def cmd_cats(ctx, a):
    rows = sorted(ctx.wp.terms("category"), key=lambda r: r["name"].lower())
    ctx.emit(rows, lambda: print(table(rows, ["term_id", "name", "slug", "count"], ["ID", "NAME", "SLUG", "POSTS"])))


def cmd_media_add(ctx, a):
    src = a.source
    remote_tmp = None
    if not src.startswith(("http://", "https://")):
        path = os.path.expanduser(src)
        if not os.path.isfile(path):
            raise BlogError(f"no such file: {src}")
        if ctx.t.kind == "ssh":
            remote_tmp = ctx.t.sh("mktemp -d", check=True).out.strip() + "/" + os.path.basename(path)
            with open(path, "rb") as f:
                ctx.t.put(f.read(), remote_tmp)
            src = remote_tmp
        else:
            src = os.path.abspath(path)
    try:
        args = ["media", "import", src, "--porcelain"]
        if a.title:
            args.append(f"--title={a.title}")
        if a.alt:
            args.append(f"--alt={a.alt}")
        if a.attach:
            args.append(f"--post_id={ctx.wp.resolve_post(a.attach)}")
            if a.featured:
                args.append("--featured_image")
        aid = int(ctx.wp.text(*args, mutating=True))
    finally:
        if remote_tmp:
            ctx.t.run(["rm", "-rf", os.path.dirname(remote_tmp)])
    from .maint import fix_uploads_group   # new upload dirs must belong to the web group
    fix_uploads_group(ctx)
    url = ctx.wp.text("post", "get", aid, "--field=guid")
    ctx.emit({"id": aid, "url": url, "markdown": f"![{a.alt or ''}]({url})"},
             f"uploaded attachment {aid}\n  url  {url}\n  md   ![{a.alt or ''}]({url})")
