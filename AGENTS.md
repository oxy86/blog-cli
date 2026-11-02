# AGENTS.md — guidance for AI agents (Claude, Codex, …) using `blog`

`blog` is a CLI for running a WordPress site — editorial work and maintenance — either over SSH or directly on the server.
Use it instead of writing one-off scripts. If something isn't covered, add a command to the tool rather than hacking around it.

> Site-specific facts (hostnames, paths, post IDs, incident history) are **not** in this file or anywhere in the repository.
> A maintainer may keep private notes next to it (`AGENTS.local.md`, `STATUS.md`; both git-ignored on purpose): if they exist, read them first.

Run from a checkout: `./bin/blog …` (Python >= 3.11, standard library only). `./bin/blog agent-help` prints the live reference;
every command takes `--json`, `--site NAME` and `--debug` (prints each command run on the target).

## Rules

- **Drafts first.** `blog new` saves a draft unless `--publish` is given. Show the result; publish only when the owner says so.
- **Don't invent the author's voice.** Post only the text supplied; ask before adding commentary; honour requests to leave
  out names or details.
- **Show the plan, then apply.** `blog update` prints a plan; `--yes` applies it. Major versions, inactive plugins and plugins that hold
  web-generated data are held back on purpose — update those *by name*, one at a time, only after discussing them.
- **Back up before anything risky** (`sql --write`, `search-replace`, deleting users/plugins). `blog backup` takes ~1 min; `blog update --yes` takes one itself.
- **Ask before changing the server** beyond posts/media: installing software (including deploying this tool), cron jobs, deleting plugins or
  users, config edits. Read-only inspection is fine. Approval for one thing does not extend to the next.
- **Never weaken security controls** (e.g. deleting rows from a firewall/blocklist table) without the owner's explicit say-so.
- Destructive commands need `--yes`. Don't work around that. Mutating commands are appended to `~/.local/state/blog/audit.log`.

## Gotchas that apply to most WordPress servers

- **Two different users.** WP-CLI runs as the SSH user; PHP-FPM runs as e.g. `www-data`. In a hardened layout the web user can only *read*
  the code. Anything WP-CLI (or a plugin it loads) creates under `wp-content/uploads/` gets the wrong group and PHP can't write there
  (plugins that keep generated files in `uploads/` then fail). `blog update`/`blog media` fix the group; `blog doctor [--fix]` checks it.
- **Page caches hide breakage.** A plain fetch can return a stale, good copy while visitors get an error. Verify with a cache-busting query
  string (`?blogcheck=123`). The updater's health check does this, requires HTTP 200 + a complete page, and fails on error text
  ("critical error", "not usable", …). It can't know plugin-specific features: after updating a plugin that *renders* something (math,
  galleries, sitemaps, highlighting) look at a page that uses it, uncached.
- **Before/after an update:** `blog snapshot take before --url /a/page/using/the/plugin/`, update, `snapshot take after`, `snapshot diff before after`
  (lines marked `!` are content changes). `blog security` also flags plugins that wordpress.org has *closed* (it only sends plugin slugs).
- **Legacy plugins can break after a PHP upgrade.** Old code may raise a PHP warning per request or per block, or hit a fatal. A flood of warnings can
  overflow nginx's FastCGI header buffer (a 502); a fatal gives a 500. After PHP upgrades, fetch the pages that use legacy plugins *uncached*, and look in
  the vhost error log (e.g. `/var/log/nginx/<site>.error.log`; on Debian-style hosts readable by group `adm`) for `PHP Warning`/`PHP Fatal error` bursts.
  Remember that purging the page cache also removes a good cached copy of a page that is already broken.
- **Newlines:** old posts use `\r\n`; the SSH transport hands them back as `\n`. Compare content modulo newline style (`migrate.same_text`).
- **Don't chain a verification with the next step** (`a && b && c` with `| tail` hides exit codes): check the result, then continue.
- **Replacing a firewall/security plugin directory:** stage inside the same filesystem, then two quick `mv` renames (a missing plugin file silently
  disables a mu-plugin loader). Keep the old copy outside the web root (mode 700), never in `wp-content`.
- **Host backups:** if the host already makes backups of the site (e.g. a root cron job), set `external_backup_dir` in the config: `status`/`security`/`notify`
  then judge backup freshness by that directory (read with `sudo -n find`, metadata only; warns at 3 days, or if it is empty/unreadable) instead of the age of `blog backup` copies.
- **Shared caches:** if several sites share one nginx FastCGI cache, never wipe it — `blog cache purge` deletes only this site's entries.
- **Restores from tar as a non-root user don't restore group ownership** — re-apply the web group (the tool does).
- **Your own diagnostics can trip a firewall plugin and lock the owner out.** Login-protection plugins treat repeated 404s or failed logins as hostile.
  When you must test an admin-ish endpoint, do it from the server (`curl --resolve host:443:127.0.0.1 …` via `blog sh`), with a few requests spaced
  out, and never deliberately fail a login or add query strings to `wp-login`/`wp-admin`/`feed`.
- **Don't guess URLs/slugs;** use `blog show ID` for the real permalink. Some sites 403 curl's default User-Agent — send a browser UA.
- **Before updating a theme/plugin, diff the installed copy against the wp.org original** (download the zip into a scratch dir) to see
  whether local edits would be lost. After a theme update, purge the page cache.
- **Mail:** servers often alias local users (e.g. `root`, the SSH user) to an external address — a "local" test mail can leave the machine.
  Say so before sending test mail.
- **Quoting:** put post text on stdin with a heredoc (`blog new "Title" <<'EOF' … EOF`) or use `--file`; avoid `--body '…'` with apostrophes.
- **Markdown → Gutenberg blocks.** Supported: paragraphs, headings, bold/italic/strike/code, links, images, `>` quotes (a final `— Source`
  line becomes the citation), flat lists, fenced code, `---`, and a bare YouTube/Vimeo URL on its own line (becomes an embed). Not
  supported: tables, nested lists. HTML in the input is escaped (`--html` passes raw block HTML through). Single newlines join (CommonMark);
  `--breaks` keeps them. Categories must already exist (`blog cats`; create with `blog cats add`); tags are matched case-insensitively and created if new.
- **Scheduling:** WordPress turns a `future` post into `publish` if its GMT date is already past, so the tool always sends
  `post_date_gmt` with `post_date` and refuses past dates. Never set `--post_status=future` via raw `wp post update` without a GMT date.
- **Testing editorial commands:** use throwaway drafts titled `ZZ …` (never `publish` them — use `private`/`future` to stand in),
  then delete posts, attachments and new tags and compare `SELECT COUNT(*)` before/after. Attaching media to a future-dated post
  creates `uploads/<future year>/` — remove the empty directories afterwards.
- **Editing existing posts:** `blog edit ID` with no flags opens the *raw* block HTML; `-f new.md` replaces the whole body with converted
  Markdown. Don't round-trip an old post through Markdown unless the owner wants it rewritten.

## Notes on specific commands

- **`blog migrate-code`** rewrites old `wp-syntax` `<pre lang="…">` blocks as Enlighter blocks. Dry run first; `--yes` needs a backup under an hour old;
  `--restore DIR --yes` undoes it. It escapes `& < >` (wp-syntax stored code as raw text) and writes straight to the database, because a normal
  `wp post update` fires publish hooks (outbound pingbacks to every link in the post) and bumps modified dates. Stored entities keep their current display
  by default; `--fix-entities` repairs them afterwards (preview first).
- **`blog highlight-cache`**: Enlighter builds its JS/CSS into `plugins/enlighter/cache/` on the first page view, and in a hardened layout the web user
  cannot write there, so run this after installing or updating Enlighter (the updater does it for updates). Verify with a page fetch: the page must reference
  `enlighter/cache/enlighterjs.min.js`, and that URL must return 200.
- **`blog notify`** is the safe thing to schedule: it never changes the site and mails only new findings. `blog auto-update` is optional and its health
  check is shallow (home page and newest post), so don't schedule it without smoke checks that cover the plugins that render things.

## Working on this repository

- **Keep site facts out of tracked files:** no real hostnames, e-mail addresses, IPs, post IDs or incident narratives. Put them in the git-ignored
  `AGENTS.local.md` / `STATUS.md`. Never `git add -f` an ignored file; check `git status` and `git diff --cached` before every commit.
- **Commit or push only when the owner asks.** Run `python3 -m unittest discover -s tests` first.
- **The command reference below is generated from `blog agent-help`.** Change the help text in `blog/cli.py`, then paste the new output here;
  a unit test fails if the two differ.

## Command reference (output of `blog agent-help`)

```
blog — WordPress CLI (local or ssh). All commands accept --json, --site NAME, --debug.
Posts default to DRAFT; nothing goes live without --publish / `blog publish`.

EDITORIAL
  blog new "Title" [--tags a,b] [--cat Name] [--body MD | -f file.md | <stdin>] [--publish|--schedule 'YYYY-MM-DD HH:MM'] [--slug s] [--dry-run]
      Body is Markdown, converted to Gutenberg blocks (quotes, lists, code, links, bare YouTube URLs -> embeds).
      File may carry front matter (title/tags/category/slug/date/status) or start with '# Title'.
      Categories must exist (`blog cats`); tags are matched case-insensitively and created if new.
  blog quote "Title" --source "Who" --via "Example News" --url URL [--tags ..] [--cat ..] [--publish]   (quote on stdin; adds nothing else)
  blog new ... [--image PATH_OR_URL [--image-alt ..]] [--translit]   front matter also: excerpt, featured_image, featured_alt
      publish / new --publish end with a live check (cache-bypassing fetch: HTTP 200, title present, no error text).
  blog edit ID|slug [--title ..] [--add-tag ..] [--remove-tag ..] [--cat ..] [--status ..] [-f new.md] (no flags + tty = $EDITOR on raw blocks)
  blog ls [-n 20] [--status draft] [--tag x] [--cat slug] [-s text]   |   blog show ID [--raw]
  blog publish ID | blog unpublish ID | blog trash ID [--force --yes]
  blog media PATH_OR_URL [--alt ..] [--attach ID --featured]   -> prints attachment URL + markdown
  blog tags | blog cats
  blog cats add "Name" [--slug s] [--parent NAME|SLUG|ID] [--description ..] [--dry-run]   create a category (refuses duplicates)

MAINTENANCE / SWISS ARMY
  blog backup [--db|--files] [--label x] [--pull DIR]   full backup on the server (mode 700); blog backups; blog restore NAME --db --files --yes
  blog auto-update [--dry-run] [--notify EMAIL]   non-major updates only, mails only if needed (for cron on the server)
  blog deploy [--dest ~/blog-cli]   copy the tool to the server and write its config
  blog prune-spam-users [--yes] [--keep ID] [--limit N]   delete never-active subscriber accounts (dry run unless --yes; needs a backup <1 h old)
  blog migrate-code [--ids ..] [--decode-entities | --fix-entities] [--yes] | --restore DIR --yes   wp-syntax <pre lang> -> Enlighter blocks (direct DB write, no hooks/pings); --fix-entities shows & < > where the editor had stored entities (preview first)
  blog highlight-cache        rebuild Enlighter's JS/CSS (read-only for web); `update` does this automatically after an Enlighter update
  blog notify [--to EMAIL] [--dry-run] [--heartbeat-days 30]   read-only watch; mails only NEW items (cron-safe: never changes the site)
  blog security [--offline]   read-only review (checksums, admins, perms, code in uploads, plugin upkeep, TLS/disk/backups); exit 1 on FAIL
  blog snapshot take NAME [--url PATH]... [-n 5] | diff A B | ls   gentle (1 req/s) before/after page comparison
  blog cache purge            drop this site's cached pages (nginx FastCGI cache; other sites untouched)
  blog clean-backups [--keep 3] [--older-than DAYS] [--local [DIR]] [--yes]   dry run unless --yes; never deletes the newest
  blog update [core|plugins|themes|slug...] [--yes] [--allow-major] [--include-inactive] [--check URL=TEXT]... [--pause]
      Without --yes it only prints the plan. Applies safe updates, holds back majors / inactive / web-writable plugins.
      --check /path/=TEXT adds a plugin-specific smoke test (page must contain TEXT) run before and after every update;
      --pause (humans, tty only) stops after each update for inspection.
      With --yes: health check -> backup -> one update at a time -> health check (browser UA) -> auto-rollback of plugins/themes.
  blog status [--deep]        versions, pending updates, HTTP/TLS, disk, post/user counts
  blog doctor [--fix]         verify connectivity; --fix installs wp-cli on the target; also lists which optional features (mail, sudo, rsync, page cache) work here
  blog wp -- <any wp-cli args>   e.g. blog wp -- plugin list --format=json
  blog sql "SELECT ..."       read-only unless --write
  blog sh "cmd"               shell on target in WP root     blog eval 'php'     blog eval-file x.php
  blog get REMOTE [LOCAL]     blog put LOCAL REMOTE
Mutating commands are appended to ~/.local/state/blog/audit.log.
```
