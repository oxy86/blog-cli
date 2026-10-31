import json
import sys

from .config import load_site
from .transport import make_transport
from .wp import WP


class Ctx:
    def __init__(self, args):
        self.args = args
        self.json = getattr(args, "json", False)
        self.debug = getattr(args, "debug", False)
        self.site = load_site(getattr(args, "site", None), getattr(args, "config", None))
        self.t = make_transport(self.site, self.debug)
        self.wp = WP(self.site, self.t)

    def emit(self, data, human=None):
        """JSON mode prints `data`; otherwise prints the human string/callable."""
        if self.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        elif callable(human):
            human()
        elif human is not None:
            print(human)

    def info(self, msg):
        if not self.json:
            print(msg)

    def warn(self, msg):
        print(f"warning: {msg}", file=sys.stderr)


def table(rows, cols, headers=None):
    headers = headers or cols
    data = [[str(r.get(c, "") if isinstance(r, dict) else r[i]) for i, c in enumerate(cols)] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in data)) if data else len(h) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip()]
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in data]
    return "\n".join(lines)
