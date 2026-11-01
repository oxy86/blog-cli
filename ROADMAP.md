# ROADMAP

`[x]` done and verified · `[~]` written, not yet exercised on a real site · `[ ]` to do.

## Foundation
- [x] Config + `blog init`, site profiles, `--site`
- [x] Local and SSH transports (SSH multiplexing, safe quoting); `local` verified by running on the server itself
- [x] WP-CLI wrapper (noise filtering, JSON, structured errors, audit log)
- [x] `status`, `doctor` (incl. uploads-group check), `wp --` passthrough, `sql` (read-only guard)
- [~] `sh`, `eval`, `eval-file`, `get`, `put`, `doctor --fix` (installs wp-cli on the target)
- [ ] `pip install -e .` / put `blog` on PATH

## Editorial
- [x] Markdown → Gutenberg converter; `new`, `edit`, `ls`, `show`, `publish`
- [x] `unpublish`, `trash`, `new --schedule`, `edit --schedule`, `edit` via `$EDITOR`, `edit -f`, `media --attach --featured` — exercised on throwaway drafts and cleaned up (found and fixed a bug: `edit --schedule` published the post instead of scheduling it)
- [~] `media` from a URL, and `publish` on a scheduled post, not exercised
- [x] Front matter: `featured_image`, `excerpt`, `date`; `blog new --image URL|PATH`
- [ ] Nested lists and tables; Enlighter-style code blocks from fenced Markdown (`new` currently emits plain `wp:code`)
- [x] `blog quote` shortcut ("paste a quote + source link" workflow)
- [ ] Pages in `new`/`edit`; `blog comments ls|approve|spam|delete`
- [~] Post-publish live check in `publish` / `new --publish` (check function verified on a live page; the publish wiring itself has only run for real on the next post)
- [x] Transliterated slugs for Greek titles (`--translit`, opt-in)

## Maintenance
- [x] `backup`, `backups`, `clean-backups`, `restore` (path-level restore verified; full DB/files restore not run on a real site)
- [x] `update`: plan by default, `--yes` → pre-flight health check, backup, one item at a time, cache-bypassing health check, auto-rollback of plugins/themes, perms re-hardened, targeted page-cache purge
- [x] `auto-update` (non-major only, mails only when needed) and `deploy` (to run it from cron on the server) — decision logic unit-tested; apply path not yet exercised live
- [x] `cache purge` (this site's entries only in a shared nginx FastCGI cache)
- [ ] Install/uninstall helper for the cron job (`blog auto-update --install-cron`, with a dry run and an explicit confirmation)
- [x] `security`: core + plugin checksums, admin users, file perms, code in uploads, debug flag, pending updates, plugins closed/abandoned on wordpress.org, TLS/disk/backup age (read-only; exit 1 on FAIL)
- [ ] `security`: real vulnerability-feed lookup (Wordfence/Patchstack/WPScan all need an API key — decide on one)
- [x] `prune-spam-users` (never-active subscribers only; dry run by default; needs a recent backup; batched) — trial-tested on 100 accounts
- [ ] Plugin/theme cleanup helpers (list unused, delete with confirmation)
- [ ] `db`: size, optimize, `search-replace --dry-run`, autoload report; `log tail`; `cron` health
- [x] TLS/disk/stale-backup warnings in `status` and in the `auto-update` mail (mailed only when a warning is new)
- [ ] certbot-specific check (why renewal failed), not just "days left"
- [ ] Backup: scheduled runs and an off-server copy

## Agent polish
- [x] `--json` everywhere, `agent-help`, AGENTS.md
- [x] `blog snapshot take|diff|ls`: gentle (1 req/s) multi-page comparison of status, title, text hash, images, scripts, math images, error text
- [ ] snapshot: menus/widgets/headings detail; `update --yes` taking a snapshot automatically
- [~] Plugin-aware smoke tests: `update --check URL=expected-text` done (verified read-only against the math page); *remembering* checks per plugin in config still open
- [x] `update --pause` (interactive, tty only; agents update one item by name instead)
- [ ] Consistent JSON error schema and exit codes; mocked-transport tests for editorial/ops; integration tests against a throwaway WordPress
- [ ] `status` speed (batch wp-cli calls); say "major only" when only a major core release is pending
- [ ] `doctor`: report active firewall-plugin blocks and whether the caller's IP is among them

## Later
- [ ] REST API transport (application password) for posting without SSH
- [ ] Multiple sites per config (profiles exist; needs testing)
- [ ] Share-sheet / mobile shortcut calling `blog new`
