"""`blog migrate-code`: rewrite wp-syntax `<pre lang="..">` blocks as Enlighter `<pre class="EnlighterJSRAW" ..>` blocks."""
import html
import json
import re
import time
from pathlib import Path

from .ctx import table
from .errors import BlogError

# wp-syntax / GeSHi language -> Enlighter language (see enlighterjs: shell, cpp, php, javascript, xml, sql, mysql, matlab, generic)
LANGS = {
    "bash": "shell", "sh": "shell", "shell": "shell", "zsh": "shell",
    "cpp": "cpp", "c": "cpp", "c++": "cpp", "h": "cpp",
    "php": "php", "javascript": "javascript", "js": "javascript",
    "xml": "xml", "html": "xml", "html4strict": "xml", "html5": "xml",
    "sql": "sql", "mysql": "mysql", "matlab": "matlab",
    "css": "css", "python": "python", "java": "java", "perl": "generic", "ruby": "ruby",
}
# exactly what wp-syntax itself accepts: <pre> whose only attributes are lang / line / escaped
BLOCK = re.compile(r"""<pre((?:\s+(?:lang|line|escaped)\s*=\s*(?:"[^"]*"|'[^']*'))+)\s*>(.*?)</pre>""", re.I | re.S)
ATTR = re.compile(r"""(lang|line|escaped)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#039;", "'"), ("&#39;", "'"), ("&amp;", "&"))   # &amp; last
STATE = Path.home() / ".local" / "state" / "blog" / "migrate-code"

PHP_WRITE = r"""
global $wpdb;
$d = json_decode(stream_get_contents(STDIN), true);
if (!is_array($d)) { WP_CLI::error("bad payload"); }
$n = 0;
foreach ($d as $id => $content) {
    $r = $wpdb->update($wpdb->posts, array('post_content' => $content), array('ID' => (int) $id));
    if ($r === false) { WP_CLI::error("update failed for post $id: " . $wpdb->last_error); }
    clean_post_cache((int) $id);
    $n++;
}
echo "updated $n\n";
"""


def _trim(code):
    """What wp-syntax does before highlighting: drop a leading blank line, rtrim."""
    return re.sub(r"^\s*?\n", "", code, count=1).rstrip()


def _escape(code):
    return code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _decode(code):
    for ent, ch in ENTITIES:
        code = code.replace(ent, ch)
    return code


def convert_block(m, decode_entities=False, stats=None):
    attrs = {k.lower(): (a or b) for k, a, b in ATTR.findall(m.group(1))}
    lang = (attrs.get("lang") or "").strip().lower()
    if not lang:
        return m.group(0)                       # wp-syntax would not highlight it either: leave alone
    code = _trim(m.group(2))
    escaped = attrs.get("escaped", "").lower() == "true"
    had_entities = bool(re.search(r"&(?:lt|gt|amp|quot|#0?39);", code))
    if not escaped:
        if decode_entities:
            code = _decode(code)
        code = _escape(code)
    target = LANGS.get(lang, "generic")
    tag = f'<pre class="EnlighterJSRAW" data-enlighter-language="{target}"'
    line = (attrs.get("line") or "").strip()
    if line.isdigit() and int(line) > 0:
        tag += ' data-enlighter-linenumbers="true"'
        if int(line) > 1:
            tag += f' data-enlighter-lineoffset="{int(line)}"'
    else:
        tag += ' data-enlighter-linenumbers="false"'
    if stats is not None:
        stats["blocks"] += 1
        stats["langs"][lang] = stats["langs"].get(lang, 0) + 1
        stats["generic"] += lang not in LANGS
        stats["entities"] += had_entities and not escaped
    return f"{tag}>{code}</pre>"


def convert(content, decode_entities=False):
    stats = {"blocks": 0, "langs": {}, "generic": 0, "entities": 0}
    new = BLOCK.sub(lambda m: convert_block(m, decode_entities, stats), content)
    return new, stats


def same_text(a, b):
    """Read-back comparison that ignores newline style: the SSH transport turns \r\n into \n, and 23 of the 24
    real posts use \r\n (a byte-for-byte compare reported false mismatches)."""
    norm = lambda s: s.replace("\r\n", "\n").replace("\r", "\n").strip()
    return norm(a) == norm(b)


def _posts(ctx, only=None):
    rows = ctx.wp.json("post", "list", "--post_type=post,page", "--post_status=publish,draft,private,pending,future",
                       "--posts_per_page=-1", "--fields=ID,post_title,post_status,post_content")
    out = []
    for r in rows:
        if only and str(r["ID"]) not in only:
            continue
        if re.search(r"<pre[^>]*\blang\s*=", r["post_content"], re.I):
            out.append(r)
    return out


def _write(ctx, payload):
    r = ctx.wp.run("eval", PHP_WRITE, stdin=json.dumps(payload), mutating=True, timeout=600)
    return r.out.strip()


def cmd_migrate_code(ctx, a):
    from .maint import _recent_backup, purge_page_cache
    if a.restore:
        manifest = Path(a.restore).expanduser() / "originals.json"
        if not manifest.exists():
            raise BlogError(f"no originals.json in {a.restore}")
        originals = json.loads(manifest.read_text())
        if not a.yes:
            ctx.emit({"would_restore": list(originals)}, lambda: print(f"would restore {len(originals)} post(s): {', '.join(originals)}\nrun again with --yes"))
            return 0
        msg = _write(ctx, originals)
        purged = purge_page_cache(ctx)
        ctx.emit({"restored": list(originals), "purged": purged}, lambda: print(f"{msg}; purged {purged} cached page(s)"))
        return 0

    only = {x.strip() for x in (a.ids or "").split(",") if x.strip()} or None
    posts = _posts(ctx, only)
    plan, payload, originals, rows = [], {}, {}, []
    for p in posts:
        new, st = convert(p["post_content"], a.decode_entities)
        if new == p["post_content"]:
            continue
        payload[str(p["ID"])], originals[str(p["ID"])] = new, p["post_content"]
        rows.append({"id": p["ID"], "status": p["post_status"], "title": p["post_title"][:42], "blocks": st["blocks"],
                     "langs": ",".join(f"{k}:{v}" for k, v in sorted(st["langs"].items())), "entities": st["entities"], "generic": st["generic"]})
    total = sum(r["blocks"] for r in rows)

    def dry():
        print(table(rows, ["id", "status", "title", "blocks", "langs", "entities"]) if rows else "(nothing to migrate)")
        print(f"\n{len(rows)} post(s), {total} block(s) to rewrite.")
        ent = sum(r["entities"] for r in rows)
        if ent:
            print(f"{ent} block(s) already contain HTML entities (e.g. &amp;&amp;) that wp-syntax shows literally today; "
                  "they are kept as they display now. Add --decode-entities to show them as the code they were meant to be.")
        gen = sum(r["generic"] for r in rows)
        if gen:
            print(f"{gen} block(s) use a language Enlighter lacks (e.g. pascal, fortran, tcl): they get the 'generic' highlighter.")
        print("\ndry run — nothing changed. Apply with: blog migrate-code --yes   (needs a backup from the last hour)")
    if not a.yes or not rows:
        ctx.emit({"posts": rows, "blocks": total, "applied": False}, dry)
        return 0
    if not _recent_backup(ctx):
        raise BlogError("no backup from the last hour", hint="run: blog backup --label pre-code-migration")

    STATE.mkdir(parents=True, exist_ok=True)
    folder = STATE / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir()
    (folder / "originals.json").write_text(json.dumps(originals))
    (folder / "new.json").write_text(json.dumps(payload))
    ctx.info(f"originals saved to {folder} (undo: blog migrate-code --restore {folder} --yes)")
    msg = _write(ctx, payload)
    bad = []
    for pid, new in payload.items():                      # read back and compare
        got = ctx.wp.text("post", "get", pid, "--field=post_content")
        if same_text(got, new) is False:
            bad.append(pid)
    purged = purge_page_cache(ctx)
    res = {"applied": True, "posts": rows, "blocks": total, "verified": not bad, "mismatch": bad, "saved": str(folder), "purged": purged}

    def done():
        print(f"{msg}: {len(rows)} post(s), {total} block(s). Read-back check: {'all match' if not bad else 'MISMATCH in ' + ', '.join(bad)}.")
        print(f"purged {purged} cached page(s). Undo: blog migrate-code --restore {folder} --yes")
        print("Next: activate Enlighter and deactivate wp-syntax (blog wp -- plugin activate enlighter / deactivate wp-syntax).")
    ctx.emit(res, done)
    return 1 if bad else 0


# ------------------------------------------------------------------ Enlighter assets
PHP_BUILD_ASSETS = r"""
global $post, $wp_query;
$id = (int) %d;
$post = get_post($id); setup_postdata($post);
$wp_query->is_single = true; $wp_query->is_singular = true; $wp_query->queried_object = $post; $wp_query->queried_object_id = $id;
do_action('wp');
apply_filters('the_content', $post->post_content);
do_action('wp_enqueue_scripts');
echo "ok\n";
"""


def build_highlighter_assets(ctx):
    """Enlighter writes its JS/CSS into plugins/enlighter/cache on the first page view, but the web user may not write
    there (hardened layout). Build the files once as the deploy user, then make them read-only for the web group.
    Must be repeated after every Enlighter update (an update replaces the directory)."""
    row = ctx.wp.json("post", "list", "--post_type=post", "--post_status=publish", "--s=EnlighterJSRAW", "--posts_per_page=1", "--fields=ID")
    if not row:
        raise BlogError("no published post uses Enlighter blocks", hint="nothing to build the highlighter assets from")
    cache = ctx.site.path.rstrip("/") + "/wp-content/plugins/enlighter/cache"
    ctx.t.sh(f"rm -f {cache}/enlighterjs.min.js {cache}/enlighterjs.min.css", check=True)
    ctx.wp.run("eval", PHP_BUILD_ASSETS % int(row[0]["ID"]), mutating=True)
    ctx.t.sh(f"cd {cache} && chgrp {ctx.site.web_group} enlighterjs.min.* && chmod 640 enlighterjs.min.*", check=True)
    files = ctx.t.sh(f"ls {cache}/enlighterjs.min.*", check=False).out.split()
    if len(files) != 2:
        raise BlogError("Enlighter did not generate its JS/CSS files", hint=f"look in {cache}")
    return [f.rsplit("/", 1)[-1] for f in files]


def cmd_highlight_cache(ctx, a):
    from .maint import purge_page_cache
    files = build_highlighter_assets(ctx)
    purged = purge_page_cache(ctx)
    ctx.emit({"built": files, "purged": purged}, lambda: print(f"built {', '.join(files)} (read-only for the web group); purged {purged} cached page(s)"))
