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
- [~] `unpublish`, `trash`, `new --schedule`, `edit` via `$EDITOR`, `edit -f`, `media` upload
- [ ] Front matter: `featured_image`, `excerpt`, `date`; `blog new --image URL|PATH`
- [ ] Nested lists and tables; wp-syntax-style code blocks (`<pre lang=…>`) option
- [ ] `blog quote` shortcut ("paste a quote + source link" workflow)
- [ ] Pages in `new`/`edit`; `blog comments ls|approve|spam|delete`
- [ ] Post-publish check built into `publish` (fetch the URL with a browser UA, confirm the title)
- [ ] Transliterated slugs for non-Latin titles

## Maintenance
- [x] `backup`, `backups`, `clean-backups`, `restore` (path-level restore verified; full DB/files restore not run on a real site)
- [x] `update`: plan by default, `--yes` → pre-flight health check, backup, one item at a time, cache-bypassing health check, auto-rollback of plugins/themes, perms re-hardened, targeted page-cache purge
- [x] `auto-update` (non-major only, mails only when needed) and `deploy` (to run it from cron on the server) — decision logic unit-tested; apply path not yet exercised live
- [x] `cache purge` (this site's entries only in a shared nginx FastCGI cache)
- [ ] Install/uninstall helper for the cron job (`blog auto-update --install-cron`, with a dry run and an explicit confirmation)
- [ ] `security`: core + plugin checksums, admin users, file perms, debug flags, vulnerability-feed lookup (WPScan/Patchstack)
- [x] `prune-spam-users` (never-active subscribers only; dry run by default; needs a recent backup; batched) — trial-tested on 100 accounts
- [ ] Plugin/theme cleanup helpers (list unused, delete with confirmation)
- [ ] `db`: size, optimize, `search-replace --dry-run`, autoload report; `log tail`; `cron` health
- [ ] TLS/certbot/disk warnings in `status` and in the `auto-update` mail
- [ ] Backup: scheduled runs and an off-server copy

## Agent polish
- [x] `--json` everywhere, `agent-help`, AGENTS.md
- [ ] `blog snapshot before|after`: rate-limited multi-page structural diff (menu, widgets, headings, assets, error text)
- [ ] Plugin-aware smoke tests: `update --check URL=expected-text`, remembered per plugin
- [ ] `update --pause` stop after each major item and print what to inspect
- [ ] Consistent JSON error schema and exit codes; mocked-transport tests for editorial/ops; integration tests against a throwaway WordPress
- [ ] `status` speed (batch wp-cli calls); say "major only" when only a major core release is pending
- [ ] `doctor`: report active firewall-plugin blocks and whether the caller's IP is among them

## Later
- [ ] REST API transport (application password) for posting without SSH
- [ ] Multiple sites per config (profiles exist; needs testing)
- [ ] Share-sheet / mobile shortcut calling `blog new`
