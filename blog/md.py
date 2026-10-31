"""Markdown -> WordPress (Gutenberg) block markup. Stdlib only.

Supported: paragraphs, #..###### headings, **bold**, *italic*, ~~strike~~, `code`,
links, images, > quotes (a trailing "— Source" line becomes the citation),
bullet/numbered lists (flat), fenced code, --- rules, and bare YouTube/Vimeo
URLs on their own line (become embeds, like your existing posts).
Not supported: tables, nested lists, raw HTML passthrough (HTML is escaped).
"""
import html
import json
import re

FENCE = re.compile(r"^(```|~~~)\s*([\w+-]*)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
IMAGE_LINE = re.compile(r'^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)\s*$')
EMBED_LINE = re.compile(r"^<?(https?://(?:www\.)?(?:youtube\.com/watch\?\S+|youtu\.be/\S+|vimeo\.com/\d+\S*))>?\s*$")
URLPART = r"((?:[^()\s]|\([^()\s]*\))+)"


def esc(s):
    return html.escape(s, quote=False)


def _attr(s):
    return html.escape(s, quote=True)


# ---------------------------------------------------------------- inline
def inline(text, breaks=False):
    stash = []

    def hold(markup):
        stash.append(markup)
        return f"\x00{len(stash) - 1}\x00"

    # backslash escapes and code spans first, so nothing inside them is touched
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|])", lambda m: hold(esc(m.group(1))), text)
    text = re.sub(r"(`+)(.+?)\1", lambda m: hold(f"<code>{esc(m.group(2).strip())}</code>"), text)
    text = esc(text)

    def image(m):
        t = f' title="{_attr(m.group(4))}"' if m.group(4) else ""
        return hold(f'<img src="{_attr(html.unescape(m.group(2)))}" alt="{_attr(html.unescape(m.group(1)))}"{t}/>')

    def link(m):
        t = f' title="{_attr(m.group(4))}"' if m.group(4) else ""
        return hold(f'<a href="{_attr(html.unescape(m.group(2)))}"{t}>{emphasis(m.group(1))}</a>')

    text = re.sub(r'!\[([^\]]*)\]\(' + URLPART + r'(?:\s+"([^"]*)")?\)', lambda m: image(_g(m)), text)
    text = re.sub(r'\[([^\]]+)\]\(' + URLPART + r'(?:\s+"([^"]*)")?\)', lambda m: link(_g(m)), text)
    text = re.sub(r"&lt;(https?://[^\s&]+?)&gt;",
                  lambda m: hold(f'<a href="{_attr(m.group(1))}">{m.group(1)}</a>'), text)

    def bare(m):
        url, tail = m.group(0), ""
        while url and url[-1] in ".,;:!?)'\"":
            if url[-1] == ")" and url.count("(") >= url.count(")"):
                break
            tail, url = url[-1] + tail, url[:-1]
        return hold(f'<a href="{_attr(html.unescape(url))}">{url}</a>') + tail

    text = re.sub(r"(?<![\w\"=/>])https?://[^\s<\x00]+", bare, text)
    text = emphasis(text)
    text = re.sub(r"(?: {2,}|\\)\n", "<br />\n", text)
    if breaks:
        text = text.replace("\n", "<br />\n")
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text


def _g(m):
    """Normalise a match so image()/link() always see groups 1..4."""
    class G:
        def group(self, i):
            try:
                return m.group(i)
            except IndexError:
                return None
    return G()


def emphasis(s):
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])", r"<strong>\1</strong>", s)
    s = re.sub(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"<em>\1</em>", s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", s)
    return s


# ---------------------------------------------------------------- blocks
def block(name, inner, attrs=None):
    a = " " + json.dumps(attrs, separators=(",", ":"), ensure_ascii=False) if attrs else ""
    return f"<!-- wp:{name}{a} -->\n{inner}\n<!-- /wp:{name} -->"


def convert(md, breaks=False):
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n\n".join(_blocks(lines, breaks)) + "\n"


def _starts_block(line):
    return bool(FENCE.match(line) or HEADING.match(line) or HR.match(line)
                or line.lstrip().startswith(">") or LIST_ITEM.match(line)
                or IMAGE_LINE.match(line.strip()) or EMBED_LINE.match(line.strip()))


def _blocks(lines, breaks):
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = FENCE.match(line)
        if m:
            fence, i, code = m.group(1), i + 1, []
            while i < n and not lines[i].startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(block("code", f'<pre class="wp-block-code"><code>{esc(chr(10).join(code))}</code></pre>'))
            continue

        m = HEADING.match(line)
        if m:
            lvl = len(m.group(1))
            attrs = {"level": lvl} if lvl != 2 else None
            out.append(block("heading", f'<h{lvl} class="wp-block-heading">{inline(m.group(2), breaks)}</h{lvl}>', attrs))
            i += 1
            continue

        if HR.match(line):
            out.append(block("separator", '<hr class="wp-block-separator has-alpha-channel-opacity"/>'))
            i += 1
            continue

        if line.lstrip().startswith(">"):
            q = []
            while i < n and lines[i].lstrip().startswith(">"):
                q.append(re.sub(r"^\s*> ?", "", lines[i]))
                i += 1
            out.append(_quote(q, breaks))
            continue

        m = LIST_ITEM.match(line)
        if m:
            ordered = m.group(2)[0].isdigit()
            items = []
            while i < n:
                mi = LIST_ITEM.match(lines[i])
                if mi and mi.group(2)[0].isdigit() == ordered:
                    items.append(mi.group(3))
                elif lines[i].strip() and lines[i].startswith((" ", "\t")) and items and not mi:
                    items[-1] += " " + lines[i].strip()
                else:
                    break
                i += 1
            tag = "ol" if ordered else "ul"
            lis = "".join(block("list-item", f"<li>{inline(t, breaks)}</li>") for t in items)
            out.append(block("list", f'<{tag} class="wp-block-list">{lis}</{tag}>', {"ordered": True} if ordered else None))
            continue

        m = IMAGE_LINE.match(line.strip())
        if m:
            cap = f"<figcaption class=\"wp-element-caption\">{inline(m.group(3))}</figcaption>" if m.group(3) else ""
            out.append(block("image", f'<figure class="wp-block-image"><img src="{_attr(m.group(2))}" alt="{_attr(m.group(1))}"/>{cap}</figure>'))
            i += 1
            continue

        m = EMBED_LINE.match(line.strip())
        if m:
            out.append(_embed(m.group(1)))
            i += 1
            continue

        para = [line.rstrip("\n")]
        i += 1
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(block("paragraph", f"<p>{inline(chr(10).join(p.strip() if k < len(para) - 1 and not p.endswith('  ') else p for k, p in enumerate(para)).strip(), breaks)}</p>"))
    return out


def _quote(q, breaks):
    cite = ""
    body = list(q)
    # a final "— Source" / "-- Source" line becomes the <cite>, with or without a blank line before it
    while body and not body[-1].strip():
        body.pop()
    if len(body) > 1:
        m = re.match(r"^\s*(?:—|–|--)\s*(.+?)\s*$", body[-1])
        if m:
            cite = f"<cite>{inline(m.group(1))}</cite>"
            body = body[:-1]
    inner = "\n\n".join(_blocks(body, breaks))
    return block("quote", f'<blockquote class="wp-block-quote">{inner}{cite}</blockquote>')


def _embed(url):
    provider = "youtube" if ("youtu" in url) else "vimeo"
    attrs = {"url": url, "type": "video", "providerNameSlug": provider, "responsive": True,
             "className": "wp-embed-aspect-16-9 wp-has-aspect-ratio"}
    figure = (f'<figure class="wp-block-embed is-type-video is-provider-{provider} wp-block-embed-{provider} '
              f'wp-embed-aspect-16-9 wp-has-aspect-ratio"><div class="wp-block-embed__wrapper">\n{url}\n</div></figure>')
    return block("embed", figure, attrs)


# ---------------------------------------------------------------- front matter
def split_frontmatter(text):
    """Tiny `key: value` front matter. Returns (meta dict, body)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("\"'")
    rest = text[end + 4:]
    return meta, rest.lstrip("\n")
