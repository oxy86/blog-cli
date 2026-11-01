"""blog — editorial + maintenance CLI for WordPress, local or over SSH."""
import argparse
import json
import sys

from . import __version__, editorial, maint, migrate, notify, ops, security
from .ctx import Ctx
from .errors import BlogError


def _globals(p, sup=False):
    d = argparse.SUPPRESS if sup else None
    p.add_argument("--site", default=d, help="site profile name (default: from config)")
    p.add_argument("--config", default=d, help="config file (default ~/.config/blog/config.toml)")
    p.add_argument("--json", action="store_true", default=d if sup else False, help="machine-readable output")
    p.add_argument("--debug", action="store_true", default=d if sup else False, help="print every command that is run on the target")


def build_parser():
    ap = argparse.ArgumentParser(prog="blog", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Agents: run `blog agent-help` for a compact reference.")
    _globals(ap)
    ap.add_argument("--version", action="version", version=f"blog {__version__}")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, fn, help, aliases=()):
        p = sub.add_parser(name, help=help, aliases=list(aliases), description=help)
        _globals(p, sup=True)
        p.set_defaults(fn=fn)
        return p

    def content_opts(p, with_title=True):
        p.add_argument("--body", help="post body (Markdown)")
        p.add_argument("--file", "-f", help="read Markdown from file ('-' = stdin)")
        p.add_argument("--html", action="store_true", help="body is already HTML/blocks; do not convert")
        p.add_argument("--breaks", action="store_true", help="treat single newlines as line breaks")
        p.add_argument("--slug")
        p.add_argument("--status", choices=editorial.STATUSES)
        p.add_argument("--schedule", metavar="'YYYY-MM-DD HH:MM'", help="publish at this time (blog timezone)")

    # ---- editorial
    p = add("new", editorial.cmd_new, "create a post (draft by default)", aliases=["post"])
    p.add_argument("title", nargs="?", help="post title (else front matter / leading '# Heading')")
    content_opts(p)
    p.add_argument("--tags", "-t", action="append", help="comma-separated tags (new tags are created)")
    p.add_argument("--cat", "-c", action="append", help="comma-separated categories (must already exist)")
    p.add_argument("--publish", "-p", action="store_true", help="publish immediately instead of saving a draft")
    p.add_argument("--dry-run", action="store_true", help="show what would be created; change nothing")
    p.add_argument("--image", metavar="PATH_OR_URL", help="upload this image and set it as the featured image")
    p.add_argument("--image-alt", help="alt text for --image (default: the title)")
    p.add_argument("--translit", action="store_true", help="make the slug from the title, transliterating Greek to Latin")

    p = add("quote", editorial.cmd_quote, "post a quote with attribution + source link (nothing else is added)")
    p.add_argument("title", help="post title")
    p.add_argument("--source", help="who said it, e.g. \"Proton's CEO\"")
    p.add_argument("--via", help="where you saw it, e.g. Slashdot (default: the URL's host)")
    p.add_argument("--url", help="link to the original article")
    content_opts(p)
    p.add_argument("--tags", "-t", action="append")
    p.add_argument("--cat", "-c", action="append")
    p.add_argument("--publish", "-p", action="store_true", help="publish immediately instead of saving a draft")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--image", metavar="PATH_OR_URL")
    p.add_argument("--image-alt")
    p.add_argument("--translit", action="store_true")

    p = add("edit", editorial.cmd_edit, "edit a post: flags, --file/--body, or $EDITOR on the raw content")
    p.add_argument("post", help="ID or slug")
    p.add_argument("--title")
    content_opts(p)
    p.add_argument("--tags", action="append", help="replace tags")
    p.add_argument("--add-tag", action="append")
    p.add_argument("--remove-tag", action="append")
    p.add_argument("--cat", action="append", help="replace categories")
    p.add_argument("--add-cat", action="append")
    p.add_argument("--remove-cat", action="append")
    p.set_defaults(publish=False)

    p = add("ls", editorial.cmd_ls, "list posts", aliases=["list"])
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--status", choices=[*editorial.STATUSES, "trash", "any"])
    p.add_argument("--tag")
    p.add_argument("--cat")
    p.add_argument("--search", "-s")
    p.add_argument("--type", default="post", help="post type (post, page, ...)")

    p = add("show", editorial.cmd_show, "show a post (--raw prints its content)")
    p.add_argument("post")
    p.add_argument("--raw", action="store_true")

    for name, fn, h in (("publish", editorial.cmd_publish, "publish a post now"),
                        ("unpublish", editorial.cmd_unpublish, "move a post back to draft")):
        add(name, fn, h).add_argument("post")
    p = add("trash", editorial.cmd_trash, "move a post to trash (--force --yes: delete permanently)")
    p.add_argument("post")
    p.add_argument("--force", action="store_true")
    p.add_argument("--yes", action="store_true")

    add("tags", editorial.cmd_tags, "list tags by usage")
    add("cats", editorial.cmd_cats, "list categories", aliases=["categories"])
    p = add("media", editorial.cmd_media_add, "upload an image/file (path or URL) to the media library", aliases=["upload"])
    p.add_argument("source")
    p.add_argument("--title")
    p.add_argument("--alt")
    p.add_argument("--attach", metavar="POST", help="attach to this post")
    p.add_argument("--featured", action="store_true", help="with --attach: set as featured image")

    # ---- maintenance / swiss army
    p = add("status", ops.cmd_status, "health overview: versions, updates, HTTP, TLS, disk, counts")
    p.add_argument("--deep", action="store_true", help="also verify core checksums (slower)")
    p = add("doctor", ops.cmd_doctor, "check that the tool can reach and drive the site")
    p.add_argument("--fix", action="store_true", help="install wp-cli on the target if missing")

    p = add("backup", maint.cmd_backup, "back up the database and all files (stored on the server, mode 700)")
    p.add_argument("--db", action="store_true", help="database only")
    p.add_argument("--files", action="store_true", help="files only")
    p.add_argument("--label", default="", help="short tag added to the backup name")
    p.add_argument("--pull", metavar="DIR", help="also copy the backup to this local directory")
    add("backups", maint.cmd_backups, "list backups on the server")
    p = add("auto-update", maint.cmd_auto_update, "unattended-safe updates (non-major only; backup, health check, rollback); mails only when needed")
    p.add_argument("--notify", metavar="EMAIL", help="mail problems/summaries here (default: notify_email in the site config)")
    p.add_argument("--keep-backups", type=int, default=0, help="backups to keep after a successful run (default: config keep_backups = 3)")
    p.add_argument("--dry-run", action="store_true", help="show the plan and what would be mailed; change nothing")
    p = add("deploy", maint.cmd_deploy, "copy this tool to the server (so cron can run it locally) and write its config there")
    p.add_argument("--dest", default="~/blog-cli")
    p.add_argument("--write-config", action="store_true", help="overwrite an existing server-side config")
    p = add("prune-spam-users", maint.cmd_prune_spam_users, "delete subscriber accounts that never posted, commented or logged in (dry run unless --yes; needs a fresh backup)")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--keep", type=int, action="append", help="user ID to always keep (repeatable)")
    p.add_argument("--limit", type=int, help="delete at most N (for a trial run)")
    p.add_argument("--batch", type=int, default=200)
    p = add("clean-backups", maint.cmd_clean_backups, "delete old backups, keeping the newest N (dry run unless --yes)", aliases=["prune-backups"])
    p.add_argument("--keep", type=int, default=3, help="how many of the newest backups to keep (default 3)")
    p.add_argument("--older-than", type=int, metavar="DAYS", help="only delete backups older than this many days")
    p.add_argument("--local", nargs="?", const="~/blog-backups", metavar="DIR", help="also clean this local directory (default ~/blog-backups)")
    p.add_argument("--yes", action="store_true")
    p = add("restore", maint.cmd_restore, "restore a backup (name or 'latest'): needs --db and/or --files, and --yes")
    p.add_argument("name")
    p.add_argument("--db", action="store_true")
    p.add_argument("--files", action="store_true")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--no-safety", action="store_true", help="skip the automatic pre-restore DB backup")
    p = add("update", maint.cmd_update, "update core/plugins/themes: shows a plan; --yes backs up, applies, health-checks, rolls back")
    p.add_argument("targets", nargs="*", help="core | plugins | themes | a plugin/theme slug (default: everything safe)")
    p.add_argument("--yes", action="store_true", help="apply the plan (default: just show it)")
    p.add_argument("--allow-major", action="store_true", help="also apply major-version updates")
    p.add_argument("--include-inactive", action="store_true", help="also update inactive plugins/themes")
    p.add_argument("--no-backup", action="store_true", help="skip the pre-update backup (not recommended)")
    p.add_argument("--check", action="append", metavar="URL=TEXT",
                   help="extra smoke test, repeatable: the page (path like /2008/08/x/ or full URL) must contain TEXT, before and after each update")
    p.add_argument("--pause", action="store_true", help="interactive: stop after each update so you can inspect the site")

    p = add("migrate-code", migrate.cmd_migrate_code,
            "rewrite wp-syntax <pre lang=..> blocks as Enlighter blocks (dry run by default; --yes needs a fresh backup; --restore undoes)")
    p.add_argument("--yes", action="store_true", help="apply (default: dry run)")
    p.add_argument("--ids", help="only these post IDs (comma-separated)")
    p.add_argument("--decode-entities", action="store_true",
                   help="show stored entities (&amp; &lt;) as the characters they stand for; default keeps today's display")
    p.add_argument("--fix-entities", action="store_true",
                   help="after migration: inside existing Enlighter blocks show '&' and '<' where the editor had stored &amp; / &lt; (preview first)")
    p.add_argument("--restore", metavar="DIR", help="put back the originals saved by an earlier run")

    add("highlight-cache", migrate.cmd_highlight_cache, "(re)build Enlighter's JS/CSS as the deploy user, read-only for the web group; needed after every Enlighter update")

    p = add("notify", notify.cmd_notify, "read-only watch: e-mails only when something NEW needs attention (updates waiting, security, TLS/disk/backups); changes nothing")
    p.add_argument("--to", metavar="EMAIL", help="recipient (default: notify_email from the config)")
    p.add_argument("--dry-run", action="store_true", help="show what would be mailed; send nothing, save no state")
    p.add_argument("--offline", action="store_true", help="skip the wordpress.org plugin-upkeep lookup")
    p.add_argument("--heartbeat-days", type=int, default=30, help="send an 'all quiet' mail if nothing was mailed for this many days (0 = never)")

    p = add("security", security.cmd_security, "read-only security review: checksums, admins, permissions, code in uploads, plugin upkeep, TLS/disk/backups")
    p.add_argument("--offline", action="store_true", help="skip the wordpress.org lookup of plugin upkeep (only plugin slugs are ever sent)")

    p = add("snapshot", security.cmd_snapshot, "save/compare how key pages render (before/after an update): `snapshot take NAME`, `snapshot diff A B`, `snapshot ls`")
    p.add_argument("action", choices=["take", "diff", "ls"])
    p.add_argument("names", nargs="*")
    p.add_argument("--url", action="append", metavar="PATH", help="extra page to include, e.g. /2008/08/02/prime-numbers-generator/ (repeatable)")
    p.add_argument("-n", type=int, default=5, help="how many latest posts to include (default 5)")

    p = add("cache", None, "page cache: `blog cache purge` removes this site's nginx FastCGI cache entries only")
    p.add_argument("action", choices=["purge"])
    p.set_defaults(fn=lambda ctx, a: maint.cmd_cache_purge(ctx, a))

    p = add("wp", ops.cmd_wp, "run any wp-cli command on the target:  blog wp -- plugin list")
    p.add_argument("--stdin", action="store_true", help="forward this process's stdin")
    p.add_argument("wp_args", nargs=argparse.REMAINDER)
    p = add("sql", ops.cmd_sql, "run SQL (read-only unless --write)")
    p.add_argument("query")
    p.add_argument("--write", action="store_true")
    p = add("sh", ops.cmd_sh, "run a shell command on the target in the WordPress root")
    p.add_argument("script")
    p = add("eval", ops.cmd_eval, "run PHP inside WordPress:  blog eval 'echo get_option(\"blogname\");'")
    p.add_argument("code")
    p = add("eval-file", ops.cmd_eval_file, "run a local PHP file inside WordPress on the target")
    p.add_argument("file")
    p.add_argument("args", nargs="*")
    p = add("get", ops.cmd_get, "download a file from the target ('-' = stdout)")
    p.add_argument("remote")
    p.add_argument("local", nargs="?", default="-")
    p = add("put", ops.cmd_put, "upload a local file to the target")
    p.add_argument("local")
    p.add_argument("remote")

    p = sub.add_parser("init", help="write a config file")
    _globals(p, sup=True)
    p.add_argument("--name", default="main")
    p.add_argument("--host", default="", help="ssh host (omit if running on the server)")
    p.add_argument("--path", required=True, help="WordPress root")
    p.add_argument("--wp-bin", default="wp")
    p.add_argument("--author", default="", help="WordPress login that new posts belong to")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=None, init=True)

    p = sub.add_parser("agent-help", help="compact command reference for AI agents")
    p.set_defaults(fn=None, agent_help=True)
    return ap


AGENT_HELP = """\
blog — WordPress CLI (local or ssh). All commands accept --json, --site NAME, --debug.
Posts default to DRAFT; nothing goes live without --publish / `blog publish`.

EDITORIAL
  blog new "Title" [--tags a,b] [--cat Name] [--body MD | -f file.md | <stdin>] [--publish|--schedule 'YYYY-MM-DD HH:MM'] [--slug s] [--dry-run]
      Body is Markdown, converted to Gutenberg blocks (quotes, lists, code, links, bare YouTube URLs -> embeds).
      File may carry front matter (title/tags/category/slug/date/status) or start with '# Title'.
      Categories must exist (`blog cats`); tags are matched case-insensitively and created if new.
  blog quote "Title" --source "Who" --via Slashdot --url URL [--tags ..] [--cat ..] [--publish]   (quote on stdin; adds nothing else)
  blog new ... [--image PATH_OR_URL [--image-alt ..]] [--translit]   front matter also: excerpt, featured_image, featured_alt
      publish / new --publish end with a live check (cache-bypassing fetch: HTTP 200, title present, no error text).
  blog edit ID|slug [--title ..] [--add-tag ..] [--remove-tag ..] [--cat ..] [--status ..] [-f new.md] (no flags + tty = $EDITOR on raw blocks)
  blog ls [-n 20] [--status draft] [--tag x] [--cat slug] [-s text]   |   blog show ID [--raw]
  blog publish ID | blog unpublish ID | blog trash ID [--force --yes]
  blog media PATH_OR_URL [--alt ..] [--attach ID --featured]   -> prints attachment URL + markdown
  blog tags | blog cats

MAINTENANCE / SWISS ARMY
  blog backup [--db|--files] [--label x] [--pull DIR]   full backup on the server (mode 700); blog backups; blog restore NAME --db --files --yes
  blog auto-update [--dry-run] [--notify EMAIL]   non-major updates only, mails only if needed (for cron on the server)
  blog deploy [--dest ~/blog-cli]   copy the tool to the server and write its config
  blog prune-spam-users [--yes] [--keep ID] [--limit N]   delete never-active subscriber accounts (dry run unless --yes; needs a backup <1 h old)
  blog migrate-code [--ids ..] [--decode-entities | --fix-entities] [--yes] | --restore DIR --yes   wp-syntax <pre lang> -> Enlighter blocks (direct DB write, no hooks/pings)
  blog highlight-cache        rebuild Enlighter's JS/CSS (read-only for web); `update` does this automatically after an Enlighter update
  blog notify [--to EMAIL] [--dry-run] [--heartbeat-days 30]   read-only watch; mails only NEW items (cron-safe: never changes the site)
  blog security [--offline]   read-only review (checksums, admins, perms, code in uploads, plugin upkeep, TLS/disk/backups); exit 1 on FAIL
  blog snapshot take NAME [--url PATH]... [-n 5] | diff A B | ls   gentle (1 req/s) before/after page comparison
  blog cache purge            drop this site's cached pages (nginx FastCGI cache; other sites untouched)
  blog clean-backups [--keep 3] [--older-than DAYS] [--local [DIR]] [--yes]   dry run unless --yes; never deletes the newest
  blog update [core|plugins|themes|slug...] [--yes] [--allow-major] [--include-inactive] [--check URL=TEXT]... [--pause]
      Without --yes it only prints the plan. Applies safe updates, holds back majors / inactive / web-writable plugins.
      With --yes: health check -> backup -> one update at a time -> health check (browser UA) -> auto-rollback of plugins/themes.
  blog status [--deep]        versions, pending updates, HTTP/TLS, disk, post/user counts
  blog doctor [--fix]         verify connectivity; --fix installs wp-cli on the target
  blog wp -- <any wp-cli args>   e.g. blog wp -- plugin list --format=json
  blog sql "SELECT ..."       read-only unless --write
  blog sh "cmd"               shell on target in WP root     blog eval 'php'     blog eval-file x.php
  blog get REMOTE [LOCAL]     blog put LOCAL REMOTE
Mutating commands are appended to ~/.local/state/blog/audit.log.
"""


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        if getattr(args, "agent_help", False):
            print(AGENT_HELP)
            return 0
        if getattr(args, "init", False):
            ops.cmd_init(args)
            return 0
        if not getattr(args, "fn", None):
            ap.print_help()
            return 0
        ctx = Ctx(args)
        return args.fn(ctx, args) or 0
    except BlogError as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": e.message, "hint": e.hint}), file=sys.stderr)
        else:
            print(f"error: {e.message}" + (f"\n  {e.hint}" if e.hint else ""), file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
