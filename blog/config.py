"""Config loading. ~/.config/blog/config.toml, with named site profiles."""
import os
import shlex
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import BlogError

DEFAULT_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blog" / "config.toml"


@dataclass
class Site:
    name: str
    path: str                      # WordPress root (where wp-config.php lives)
    transport: str = "auto"        # auto | local | ssh
    host: str = ""                 # ssh host (alias from ~/.ssh/config works)
    wp_bin: str = "wp"             # command used to run wp-cli on the target
    author: str = ""               # login that new posts are attributed to
    default_status: str = "draft"  # draft | publish | pending | private
    url: str = ""                  # public URL (for health checks); autodetected if empty
    backup_dir: str = "~/blog-backups"
    cache_dir: str = "/run/nginx_fastcgi_cache"   # nginx fastcgi_cache_path (shared by every site on the server)
    notify_email: str = ""         # where `blog auto-update` mails problems/summaries (empty = no mail)
    keep_backups: int = 3          # backups kept by `blog auto-update` after a successful run
    external_backup_dir: str = ""  # a root-owned directory where the host's own (daily) backups of this site land; read with `sudo -n`, read-only
    accept_closed: tuple = ()      # plugin slugs closed on wordpress.org that the owner knowingly runs (vendor ships its own updates)
    web_group: str = "www-data"    # group that must keep read-only access to WordPress files
    ssh_options: list = field(default_factory=list)

    def resolved_transport(self):
        if self.transport == "auto":
            return "local" if os.path.exists(os.path.join(self.path, "wp-config.php")) else "ssh"
        return self.transport

    @property
    def wp_argv(self):
        return shlex.split(self.wp_bin)


def config_path(override=None):
    return Path(override or os.environ.get("BLOG_CONFIG") or DEFAULT_CONFIG).expanduser()


def load_site(site=None, config=None):
    p = config_path(config)
    if not p.exists():
        raise BlogError(f"no config at {p}", hint="run: blog init --host HOST --path /path/to/wordpress")
    with open(p, "rb") as f:
        data = tomllib.load(f)
    sites = data.get("sites", {})
    name = site or os.environ.get("BLOG_SITE") or data.get("default")
    if not name and len(sites) == 1:
        name = next(iter(sites))
    if not name or name not in sites:
        raise BlogError(f"unknown site {name!r}", hint=f"configured sites: {', '.join(sites) or '(none)'}")
    s = dict(sites[name])
    if "path" not in s:
        raise BlogError(f"site {name!r} has no 'path'")
    known = {k: v for k, v in s.items() if k in Site.__dataclass_fields__ and k != "name"}
    if "accept_closed" in known:
        known["accept_closed"] = tuple(known["accept_closed"])
    return Site(name=name, **known)


def write_config(path, name, host, wp_path, wp_bin, author, force=False):
    p = config_path(path)
    if p.exists() and not force:
        raise BlogError(f"{p} already exists", hint="use --force to overwrite")
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'default = "{name}"', "", f"[sites.{name}]", 'transport = "auto"  # auto = local if the path exists here, else ssh']
    if host:
        lines.append(f'host = "{host}"')
    lines += [f'path = "{wp_path}"', f'wp_bin = "{wp_bin}"']
    if author:
        lines.append(f'author = "{author}"')
    lines.append('default_status = "draft"')
    p.write_text("\n".join(lines) + "\n")
    return p
