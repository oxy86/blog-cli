# blog

A command-line tool for running a WordPress blog: **writing posts** and **keeping the engine healthy**.
It works from your laptop over SSH, or directly on the server, with the same commands and the same config.

```bash
blog new "A headline" -t "tag1, tag2" -c "Category" <<'MD'
Markdown in, Gutenberg blocks out. Saved as a *draft* until you say otherwise.
MD
blog update            # shows what would change
blog update --yes      # backup -> one update at a time -> health check -> automatic rollback
blog security          # read-only review of the site
```

It is built to be driven by a person **or** by an AI agent (Claude, Codex, …): one stable command surface, `--json`
output, safe defaults, and escape hatches (`wp`, `sql`, `sh`, `eval`) so nobody has to write throwaway scripts.
The real work is done by [WP-CLI](https://wp-cli.org/); `blog` decides *where* it runs, adds an editorial workflow and
guard rails on top, and remembers the lessons (see [AGENTS.md](AGENTS.md)).

> **Status.** Written for, and used on, one production site: a Debian server with nginx + PHP-FPM and a hardened file layout.
> Nothing in the code is specific to that site, but it has not been run anywhere else. Read
> [What it assumes about your server](#what-it-assumes-about-your-server) and [Known limitations](#known-limitations) first, and keep backups.
> Several commands change a live site; the ones that can hurt are dry-run by default and need `--yes`.

## Contents

[Features](#features) · [Requirements](#requirements) · [Installation](#installation) · [Configuration](#configuration) ·
[Usage](#usage) · [How updates work](#how-updates-work) · [What it assumes about your server](#what-it-assumes-about-your-server) ·
[Safety and privacy](#safety-and-privacy) · [Troubleshooting](#troubleshooting) · [For AI agents](#for-ai-agents) ·
[Development](#development) · [Known limitations](#known-limitations) · [License](#license)

## Features

- **Editorial:** create, edit, schedule, publish and trash posts from Markdown; quote posts with attribution; featured images; media upload.
- **Updates with a safety net:** pre-flight health check, full backup, one item at a time, uncached health check after each, automatic rollback of plugins/themes, correct file permissions restored afterwards.
- **Backups:** full database + files backups on the server, listing, restore, retention, optional copy to your machine.
- **Watching:** `status`, a read-only `security` review, `snapshot` before/after page comparison, and `notify`, a cron-safe watcher that e-mails only about *new* problems.
- **Housekeeping:** page-cache purge that only touches this site, spam-account pruning, code-highlighter migration.
- **Swiss-army knife:** any WP-CLI command, read-only SQL, shell, PHP eval and file transfer on the target.
- **Local or SSH:** `transport = "auto"` runs locally when the WordPress path exists on the current machine and over SSH otherwise.
- **Zero dependencies:** Python standard library only. Every state-changing command is written to an audit log.

## Requirements

On your machine (or on the server, if you run it there): **Python 3.11+**, plus `ssh` for remote use (and `rsync` only for `blog deploy`).

On the server: **SSH key access**, `php`, a MySQL/MariaDB client (`mysqldump`), `tar`, `gzip`, and
[WP-CLI](https://wp-cli.org/), which `blog doctor --fix` can install for you (user-level, no root needed).
The SSH user should own the WordPress files (or at least be able to write them); the web server may stay read-only.

Optional, each enables one feature (`blog doctor` tells you which are present): `mail` (e-mail from `notify`/`auto-update`),
passwordless `sudo` (`cache purge`, `external_backup_dir`), an nginx FastCGI page cache (`cache purge`).

## Installation

### 1. Get the tool

```bash
git clone https://github.com/oxy86/blog-cli.git
cd blog-cli
./bin/blog --version            # runs straight from the checkout, nothing to build
```

Optional: put a `blog` command on your PATH.

```bash
python3 -m venv ~/.venvs/blog && ~/.venvs/blog/bin/pip install .   # then link ~/.venvs/blog/bin/blog onto your PATH
# or simply:  alias blog="$PWD/bin/blog"
```

### 2. Describe your site

```bash
blog init --name mysite --host myserver.example.org \
          --path /var/www/example.org/web \
          --wp-bin /home/me/.local/bin/wp --author myuser
```

`--host` is an SSH host (an alias from `~/.ssh/config` works); leave it out if you are running on the server itself.
`--path` is the WordPress root (where `wp-config.php` lives). `--wp-bin` is where WP-CLI is, or will be installed.
`--author` is the WordPress login that new posts belong to. This writes `~/.config/blog/config.toml`
(see [Configuration](#configuration) for everything you can set).

### 3. Check it: `blog doctor`

```bash
blog doctor            # can I reach and drive the site?
blog doctor --fix      # also installs WP-CLI on the target if it is missing, and repairs upload permissions
```

```
  ok   transport          ssh -> myserver.example.org
  ok   wordpress root     /var/www/example.org/web
  ok   wp-cli             WP-CLI 2.12.0
  ok   database           27 tables OK
  ok   uploads group      all in group www-data
  ok   author             myuser (id 2)
  ok   site url           https://example.org

optional features (informational; a missing item only disables the feature named):
  ok   php                PHP 8.4.27
  ok   mysqldump          /usr/bin/mysqldump
  ok   mail               /usr/bin/mail
  n/a  passwordless sudo  `sudo -n` is not allowed: `blog cache purge` and external_backup_dir will not work (everything else does)
  ...
ready.
```

Required checks failing makes `doctor` exit non-zero; the optional section never does. Start with `blog status`, then
a first `blog backup`, before you try anything that changes the site.

### 4. (Optional) Run it on the server too

Useful for cron jobs such as `notify`. Either copy the tool, or clone it there and describe the local site:

```bash
blog deploy                                   # rsync the tool to ~/blog-cli on the server and write its config
# or, on the server:
git clone https://github.com/oxy86/blog-cli.git && cd blog-cli && ./bin/blog init --path /var/www/example.org/web --wp-bin ~/.local/bin/wp
```

Run cron jobs as the user that owns the WordPress files and the config, for example a daily watch that mails only new findings:

```cron
15 6 * * *  /home/me/blog-cli/bin/blog notify --to you@example.org >> /home/me/.local/state/blog/notify/cron.log 2>&1
```

The server keeps its own config and state; update it with `git pull` there.

## Configuration

`~/.config/blog/config.toml` (override with `--config FILE` or `$BLOG_CONFIG`; honours `$XDG_CONFIG_HOME`).
One `[sites.NAME]` table per site; `default = "NAME"` picks the one used without `--site NAME` (or `$BLOG_SITE`).

```toml
default = "mysite"

[sites.mysite]
transport = "auto"                       # auto | local | ssh
host = "myserver.example.org"            # omit when running on the server
path = "/var/www/example.org/web"
wp_bin = "/home/me/.local/bin/wp"
author = "myuser"
default_status = "draft"
notify_email = "you@example.org"         # optional: default recipient for notify / auto-update
```

| Key | Default | Meaning |
|---|---|---|
| `path` | *(required)* | WordPress root, where `wp-config.php` lives |
| `transport` | `auto` | `auto` = local if `path` exists on this machine, else SSH; or force `local` / `ssh` |
| `host` | | SSH host (alias from `~/.ssh/config` works) |
| `ssh_options` | `[]` | extra `ssh` arguments, e.g. `["-p", "2222"]` |
| `wp_bin` | `wp` | command that runs WP-CLI on the target (use an absolute path: non-interactive SSH has a short `PATH`) |
| `author` | | WordPress login that new posts are attributed to |
| `default_status` | `draft` | status of new posts: `draft`, `publish`, `pending`, `private` |
| `url` | auto | public URL used by health checks; read from the database if empty |
| `backup_dir` | `~/blog-backups` | where backups live **on the target** (mode 700) |
| `keep_backups` | `3` | backups `auto-update` keeps after a successful run |
| `notify_email` | | default recipient for `notify` and `auto-update` |
| `web_group` | `www-data` | group of the web server; it must keep *read-only* access to the code |
| `cache_dir` | `/run/nginx_fastcgi_cache` | nginx `fastcgi_cache_path` (for `cache purge`) |
| `external_backup_dir` | | a root-owned directory where *your host's own* backups of this site land; `status`/`security`/`notify` then judge backup freshness by it (read with `sudo -n`, file dates only) |
| `accept_closed` | `[]` | plugin slugs closed on wordpress.org that you knowingly run (e.g. the vendor ships its own updates); `security` stops flagging them |

Where it keeps state, on whichever machine runs it: `~/.local/state/blog/` holds `audit.log` and small per-feature folders
(`notify/`, `snapshots/`, `migrate-code/`, `auto-update/`). Nothing there is secret, and none of it is needed to run a command.

## Usage

Every command accepts `--json` (machine-readable), `--site NAME` and `--debug` (prints each command run on the target).
`blog --help` lists everything; `blog agent-help` prints a compact reference.

### Writing

```bash
blog new "Title" -t "linux, tips" -c "Open Source" <<'MD'     # body on stdin: quote the heredoc so apostrophes are safe
Body in **Markdown**, with [links](https://example.org) and lists.
MD
blog new "Title" -f post.md --dry-run                       # preview the blocks, create nothing
blog new "Title" -f post.md --image cover.jpg --publish     # featured image; publish immediately
blog new "Title" -f post.md --schedule '2026-12-01 09:00'   # future-dated (blog time zone); past dates are refused
echo "The quoted text." | blog quote "Title" --source "Jane Doe" --via "Example News" --url https://example.org/story
```

- `new` makes a **draft** unless you pass `--publish`. A file can carry front matter: `title`, `tags`, `category`, `slug`, `date`, `status`, `excerpt`, `featured_image`, `featured_alt`.
- Markdown supports paragraphs, headings, bold/italic/strike/code, links, images, `>` quotes (a final `— Source` line becomes the citation), flat lists, fenced code, `---`, and a bare YouTube/Vimeo URL on its own line (becomes an embed). **Not** supported: tables, nested lists. HTML in the input is escaped (`--html` passes block HTML through).
- Categories must already exist (`blog cats`); tags are matched case-insensitively and created if new. `--translit` builds a Latin slug from a Greek title.
- `publish` and `new --publish` finish with a **live check**: an uncached fetch of the page must return 200, contain the title and show no error text.
- `quote` adds nothing of its own: only your text, the attribution and the link.

### Finding and changing posts

```bash
blog ls -n 10  |  blog ls --status draft -s keyword  |  blog ls --tag linux --cat open-source
blog show 42            # details and the real permalink (never guess URLs);  blog show 42 --raw  for the stored blocks
blog edit 42 --title "New" --add-tag news --remove-tag old --cat "Open Source"
blog edit 42 -f rewritten.md        # replaces the whole body from Markdown
blog edit 42                        # no flags + a terminal: opens the raw block HTML in $EDITOR
blog publish 42 | blog unpublish 42 | blog trash 42        # blog trash 42 --force --yes   deletes permanently
blog media photo.jpg --alt "Description" --attach 42 --featured      # also accepts a URL
blog tags  |  blog cats
```

Don't round-trip an existing post through Markdown unless you mean to rewrite it: `edit 42` (raw blocks) is the safe way to touch old content.

### Looking after the site

```bash
blog status [--deep]     # WordPress/PHP versions, updates waiting, HTTP + TLS, disk, backup ages, post and user counts
blog security            # read-only review (below); exit code 1 on a FAIL
blog snapshot take before --url /a/page/using/a/plugin/ ;  blog update ... ;  blog snapshot take after ;  blog snapshot diff before after
blog cache purge         # drop only this site's cached pages (nginx FastCGI cache shared with other sites is left alone)
```

**`security`** checks, without changing anything: core file checksums, plugin checksums (generated files are ignored, added code is flagged),
administrator accounts, `wp-config.php` permissions, `WP_DEBUG`, PHP files inside `uploads/`, world-writable paths, wrong-group files,
web-writable code directories, pending updates, plugins that wordpress.org has **closed** or that look abandoned
(only plugin slugs are sent to `api.wordpress.org`; skip it with `--offline`), plus TLS expiry, disk usage and backup freshness.

**`snapshot`** fetches a handful of public pages (home, the latest posts, any `--url`), one request per second, and records status,
title, a hash of the visible text, image/script counts and error text. `diff` marks content changes with `!`; asset and size changes are normal after updates.

### Updating

```bash
blog update                          # the plan only: what would be updated, what is held back and why
blog update --yes                    # apply the safe ones (see "How updates work")
blog update akismet --yes            # one named plugin/theme; core, plugins and themes work as targets too
blog update core --allow-major --yes --pause      # a major update, stopping after each step to inspect (interactive)
blog update some-plugin --yes --check "/path/to/page/=text that must appear"   # extra smoke test, run before and after
```

`--check URL=TEXT` (repeatable; a leading `/` means "on this site") lets you verify plugin-specific output that a generic health check cannot know,
such as a gallery, a math renderer or a code highlighter.

### Backups

```bash
blog backup --label before-x         # database + all files, stored on the server (mode 700), about a minute
blog backup --pull ~/Backups         # ...and copy it to this machine
blog backups                         # list
blog restore NAME --db --files --yes     # NAME or "latest"; needs --db and/or --files, and --yes
blog clean-backups --keep 2          # dry run; add --yes to delete (never deletes the newest)
```

### Watching: `notify`

```bash
blog notify --dry-run                # what would be mailed right now
blog notify --to you@example.org     # mail only if something NEW appeared since the last mail
```

`notify` never changes the site. It collects updates waiting, `security` warnings and failures, and TLS/disk/backup warnings, mails you
**only** items it has not told you about before, and sends an "all quiet" heartbeat if nothing was mailed for 30 days (`--heartbeat-days`)
so a dead cron does not look like good news. Put it in cron (see [Installation](#4-optional-run-it-on-the-server-too)).

### Power tools

```bash
blog wp -- plugin list --format=json         # any WP-CLI command
blog sql "SELECT COUNT(*) FROM wp_posts"     # read-only unless --write
blog sh "du -sh wp-content"                  # shell on the target, in the WordPress root
blog eval 'echo get_option("blogname");'     blog eval-file script.php
blog get REMOTE [LOCAL]                      blog put LOCAL REMOTE
```

### One-off helpers

- `blog prune-spam-users [--yes]`: deletes subscriber accounts that never posted, commented or logged in. Dry run by default; needs a backup from the last hour.
- `blog migrate-code [--yes]`: rewrites old `wp-syntax` `<pre lang="…">` blocks as [Enlighter](https://wordpress.org/plugins/enlighter/) blocks (dry run first, originals saved, `--restore` undoes). `--fix-entities` repairs `&amp;` / `&lt;` that an old editor had stored inside code. `blog highlight-cache` rebuilds Enlighter's generated JS/CSS (needed because the web user cannot write there).
- `blog auto-update`: unattended non-major updates (backup, health check, rollback, mail only when needed). Optional. Its health check only loads the home page and the newest post, so it cannot notice plugin-specific breakage; prefer `notify` plus `update --yes` by hand unless you have thought about that.
- `blog deploy`: copy the tool to the server and write its config there.

## How updates work

`blog update --yes`:

1. **Plan.** Safe updates are applied; held back on purpose: **major** versions, **inactive** plugins/themes, and plugins that **hold web-generated data** in their own directory. Update those by name, one at a time, after looking at them.
2. **Pre-flight.** The site must be healthy *before* anything changes, or the run stops.
3. **Backup.** A full backup (database + files).
4. **One item at a time**, then after each: purge this site's page cache, re-apply file ownership (`web_group`, no group-write), fix upload-directory groups, and run an **uncached** health check (HTTP 200, a complete page, WordPress boots, no "critical error"-style text, plus any `--check`).
5. **Rollback.** A plugin or theme that fails the check is put back from the backup automatically. A failed **core** update is not rolled back automatically (the output tells you the exact `restore` command).

A page cache can hide breakage by serving a stale good copy while visitors get an error. That is why every check goes around the cache.

## What it assumes about your server

It was built against a particular, fairly common layout. If yours differs, `blog doctor` shows which optional features will not work; the core
(editorial, `wp`, `status`, `update`, `backup`) needs very little.

- **Two users.** WP-CLI runs as the SSH user; PHP-FPM runs as another (`www-data`). In a hardened layout the web user can only *read* the code,
  so WordPress cannot update itself, and files created by WP-CLI under `uploads/` need the web group (the tool fixes this).
- **nginx FastCGI page cache** for `cache purge`: it removes only cache entries whose key belongs to this site's host and needs passwordless `sudo`. If your key format differs, it simply finds nothing.
- **`sudo -n`** is used for exactly two things: `cache purge` and reading `external_backup_dir`.
- **`mail`** (bsd-mailx or similar) sends `notify`/`auto-update` mail. Beware that servers often alias local users to an external address.
- **640/750 ownership by the SSH user with the web group as group** (`owner:www-data`) is what "hardened" means here; `blog update` restores it after changing files.

## Safety and privacy

- New posts are drafts. Destructive commands are dry runs until `--yes`. `blog sql` is read-only unless `--write` (a convenience guard, **not** a sandbox: `wp`, `sh`, `eval` and `--write` give full control of the site, as intended).
- Every state-changing command is appended to `~/.local/state/blog/audit.log`.
- Post bodies travel on stdin, never on a command line; shell arguments are quoted for SSH. The config contains no secrets (authentication is your SSH key).
- Network use: your own site (health checks, snapshots), SSH to your server, WP-CLI's normal calls to wordpress.org, `security`'s plugin lookups (slugs only, `--offline` to skip), and, only for `doctor --fix`, a download of the WP-CLI phar (checksum verified). No telemetry.
- The agent rules in [AGENTS.md](AGENTS.md) also forbid weakening security controls (for example deleting firewall rows) without the owner's explicit say-so.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `no config at …` | run `blog init` (or pass `--config`) |
| `wp: command not found` over SSH | give `wp_bin` as an absolute path; non-interactive SSH has a short `PATH` |
| PHP can't write to `uploads/` after WP-CLI created a folder | wrong group; `blog doctor --fix` repairs it |
| a plain page fetch looks fine but visitors see an error | the page cache is serving a stale copy; test with `?blogcheck=123` (the tool does) |
| `cache purge` says nothing was removed / `sudo` error | no passwordless `sudo`, or `cache_dir` is not your `fastcgi_cache_path` |
| a firewall plugin blocks you after testing | it counts repeated 404s/failed logins; probe from the server, slowly, and never deliberately fail logins |
| `403` on `curl` | some sites reject curl's default User-Agent; send a browser one |

## For AI agents

[AGENTS.md](AGENTS.md) holds the working rules (drafts first, show the plan then apply, ask before changing the server, never weaken security
controls) and the gotchas learned the hard way, plus the live command reference. `blog agent-help` prints the same reference.
Site-specific facts belong in `AGENTS.local.md` / `STATUS.md`, which are git-ignored on purpose.

## Development

```bash
python3 -m unittest discover -s tests        # standard library only, no network, no server needed
```

```
blog/
  cli.py        argument parsing and the command table          editorial.py  new / edit / ls / show / publish / media / quote
  transport.py  local and SSH execution (connection multiplexing)  maint.py      backup / restore / update / auto-update / deploy / cache
  wp.py         WP-CLI wrapper, JSON, noise filtering, audit log   security.py   security review, host warnings, snapshots
  md.py         Markdown -> Gutenberg blocks                       notify.py     the read-only watcher
  ops.py        status / doctor / wp / sql / sh / eval / get / put  migrate.py    code-block migration, highlighter assets
```

The roadmap is in [ROADMAP.md](ROADMAP.md). Issues and pull requests are welcome, especially for server layouts other than the one above.

## Known limitations

- **Tested on one site.** A full `restore --db --files` has not been exercised on a live site (a path-level restore, used by update rollbacks, has).
- No tables or nested lists in the Markdown converter; code fences become plain WordPress code blocks (no highlighting classes).
- `--check` smoke tests are per command; they are not yet remembered per plugin in the config.
- Update rollback is automatic for plugins and themes only, not for core.
- The `snapshot` metrics count images, scripts, styles and a few known plugin outputs; they are a tripwire, not a visual diff.
- Only tried from macOS and Linux; Windows is untested as the machine running the tool.

## License

[MIT](LICENSE) © 2026 Dimitris Kalamaras.
