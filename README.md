# blog

A command-line tool for running a WordPress blog — **writing posts** and **maintaining the engine** —
either from your laptop over SSH or directly on the server. Nothing in it is specific to one site except the config file.

It is meant to be driven by a person *or* by an AI agent (Claude, Codex): one stable command surface,
JSON output, safe defaults, and escape hatches so nobody has to write throwaway scripts.

## What it does

**Editorial** — pipe in Markdown, get a post:

```bash
blog new "A headline" -t "tag1, tag2" -c "Category" <<'MD'
> A quoted sentence you want to share.
> — Some Source, via [Example](https://example.org/story)
MD
blog ls                      # recent posts
blog show 42 [--raw]
blog edit 42 --add-tag news
blog publish 42              # new posts are drafts until you say so
blog media photo.jpg --alt "..." --attach 42 --featured
```

Markdown becomes Gutenberg blocks (headings, quotes with citations, lists, code, links, images, YouTube/Vimeo embeds),
matching how the existing posts are stored. Categories are validated against the ones that exist; tags are matched
case-insensitively.

**Maintenance** — see the state of the site and poke at anything:

```bash
blog status [--deep]         # WP/PHP versions, pending updates, HTTP + TLS, disk, post/user counts
blog doctor [--fix]          # can I reach and drive the site? --fix installs wp-cli on the target
blog wp -- plugin list       # any wp-cli command
blog backup [--pull DIR]     # DB + all files, stored on the server (mode 700), optional local copy
blog backups                 # list;  blog restore NAME --db --files --yes
blog update                  # show the update plan (changes nothing)
blog update --yes            # backup -> update one at a time -> health check -> auto-rollback of plugins/themes
blog auto-update [--dry-run] # unattended-safe: non-major updates only, backup + health check + rollback, mails only if needed
blog deploy                  # copy the tool to the server so cron can run it there
blog sql "SELECT ..."        # read-only unless --write
blog sh "du -sh wp-content"  # shell on the target, in the WordPress root
blog eval 'echo get_option("blogname");'     blog eval-file script.php
blog get REMOTE [LOCAL]      blog put LOCAL REMOTE
```

## How it works

```
blog (python, stdlib only)
  ├─ Transport ── LocalTransport   runs commands directly (when used on the server)
  │             └ SSHTransport     ssh with connection multiplexing (default from a laptop)
  ├─ WP wrapper ─ runs wp-cli on the target, parses JSON, filters PHP noise, writes the audit log
  ├─ editorial.py ─ new / edit / ls / show / publish / media / tags / cats
  ├─ ops.py ─ status / doctor / wp / sql / sh / eval / get / put / init
  └─ md.py ─ Markdown -> Gutenberg blocks
```

The real work is done by [WP-CLI](https://wp-cli.org/) on the server; this tool decides *where* it runs, adds the
editorial workflow on top, and adds guard rails. With `transport = "auto"` (default) it runs locally when the WordPress
path exists on the current machine and over SSH otherwise — same config, same commands.

## Setup

Requirements: Python >= 3.11 on your machine; SSH key access and `php` + `mysql` on the server. WP-CLI is installed
for you by `blog doctor --fix`.

```bash
./bin/blog init --name mysite --host myserver.example.org \
    --path /var/www/example.org/web --wp-bin /home/me/.local/bin/wp --author myuser
./bin/blog doctor
```

Config lives in `~/.config/blog/config.toml` (`--config`/`$BLOG_CONFIG` override; several `[sites.*]` profiles
and `--site NAME` are supported). Run it from the checkout as `./bin/blog`, or `pip install -e .` for a `blog` command.

## Safety

- New posts are **drafts**; publishing is explicit.
- Destructive operations need `--yes`; SQL is read-only unless `--write`.
- Updates: the plan is shown first; major versions, inactive plugins and plugins with web-generated data are held back;
  a backup is taken, each item is health-checked (HTTP 200 + full page, WordPress boots) and plugins/themes that break the
  site are rolled back automatically. File ownership is restored to the hardened layout afterwards.
- Every state-changing command is logged to `~/.local/state/blog/audit.log`.
- Shell arguments are quoted for SSH; post bodies travel on stdin, never on the command line.

## Development

```bash
python3 -m unittest discover -s tests
```

See `AGENTS.md` (rules + gotchas for agents) and `ROADMAP.md` (what's next). Keep site-specific notes in `STATUS.md` / `*.local.md` — they are git-ignored.
