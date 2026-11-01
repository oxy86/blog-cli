import unittest

from blog import md
from blog.errors import BlogError
from blog.maint import bust, is_major
from blog.ops import READ_ONLY_SQL
from blog.transport import SSHTransport


class MarkdownTests(unittest.TestCase):
    def test_paragraph_and_inline(self):
        out = md.convert("Hello **bold** *it* `c<d>` snake_case_word")
        self.assertIn("<strong>bold</strong>", out)
        self.assertIn("<em>it</em>", out)
        self.assertIn("<code>c&lt;d&gt;</code>", out)
        self.assertIn("snake_case_word", out)

    def test_html_is_escaped(self):
        self.assertIn("&lt;script&gt;", md.convert("<script>alert(1)</script>"))

    def test_links_and_bare_urls(self):
        out = md.convert("[a](https://x.org/?a=1&b=2) and https://y.org/p_q.")
        self.assertIn('<a href="https://x.org/?a=1&amp;b=2">a</a>', out)
        self.assertIn('<a href="https://y.org/p_q">https://y.org/p_q</a>.', out)

    def test_quote_with_citation(self):
        out = md.convert("> text\n> — Slashdot")
        self.assertIn("<cite>Slashdot</cite>", out)
        self.assertIn("wp:quote", out)

    def test_lists_code_hr_embed(self):
        out = md.convert("- a\n- b\n\n1. x\n\n---\n\n```py\nx<1\n```\n\nhttps://youtu.be/abc")
        self.assertIn("wp-block-list", out)
        self.assertIn('{"ordered":true}', out)
        self.assertIn("x&lt;1", out)
        self.assertIn("wp:separator", out)
        self.assertIn("wp:embed", out)

    def test_heading_levels(self):
        self.assertIn('{"level":3}', md.convert("### h"))
        self.assertNotIn('{"level"', md.convert("## h"))

    def test_front_matter(self):
        meta, body = md.split_frontmatter("---\ntitle: T\ntags: a, b\n---\n\nbody")
        self.assertEqual(meta, {"title": "T", "tags": "a, b"})
        self.assertEqual(body, "body")

    def test_greek(self):
        self.assertIn("Καλημέρα", md.convert("Καλημέρα **κόσμε**"))


class SafetyTests(unittest.TestCase):
    def test_read_only_sql(self):
        for ok in ("SELECT 1", "  show tables", "DESCRIBE wp_posts"):
            self.assertTrue(READ_ONLY_SQL.match(ok))
        for bad in ("DELETE FROM x", "UPDATE x SET a=1", "DROP TABLE x", "INSERT INTO x"):
            self.assertFalse(READ_ONLY_SQL.match(bad))

    def test_ssh_quoting(self):
        t = SSHTransport("h", "/var/www/my site")
        cmd = t.remote_command(["wp", "post", "create", "--post_title=It's; rm -rf /"])
        self.assertTrue(cmd.startswith("cd '/var/www/my site' && wp post create "))
        self.assertIn("'\"'\"'", cmd)          # the apostrophe is safely quoted
        self.assertNotIn("; rm -rf / ", cmd.replace("'", ""), "no unquoted injection")


class HealthTests(unittest.TestCase):
    def test_bust_bypasses_cache(self):
        self.assertRegex(bust("https://x.org/a/"), r"^https://x\.org/a/\?blogcheck=\d+$")
        self.assertRegex(bust("https://x.org/?p=1"), r"^https://x\.org/\?p=1&blogcheck=\d+$")


class VersionTests(unittest.TestCase):
    def test_major_detection(self):
        self.assertTrue(is_major("2.26.27", "3.3.10"))
        self.assertTrue(is_major("2.1.0", "3.0.0"))
        self.assertTrue(is_major("1.31", "2.1.2"))
        self.assertFalse(is_major("5.6", "5.7.2"))
        self.assertFalse(is_major("2.6.0", "2.7.11.1"))
        self.assertFalse(is_major("4.1.22", "4.1.25"))
        self.assertFalse(is_major("6.9", "6.9.11"))

    def test_date_versions_are_routine(self):
        self.assertFalse(is_major("20251120", "20260810"))


class AutoUpdateTests(unittest.TestCase):
    """Decision logic of `blog auto-update` with the real updater mocked out."""

    def setUp(self):
        import argparse, os, tempfile
        from pathlib import Path
        from unittest import mock
        from blog import maint
        self.maint, self.mock, self.argparse, self.Path = maint, mock, argparse, Path
        self.tmp = tempfile.mkdtemp()
        self.mails, self.pruned = [], []
        site = argparse.Namespace(name="s", notify_email="me@example.org", keep_backups=3)
        self.ctx = argparse.Namespace(site=site, t=argparse.Namespace(kind="local"), json=False, emit=lambda d, h=None: None)

    def run_auto(self, result, dry=False, raises=None):
        import contextlib, io
        maint, mock = self.maint, self.mock

        def fake_run_update(ctx, ns):
            if raises:
                raise raises
            return result, None
        with mock.patch.object(maint, "run_update", fake_run_update), \
             mock.patch.object(maint, "prune_server_backups", lambda c, k: self.pruned.append(k) or []), \
             mock.patch.object(maint, "_mail", lambda c, to, s, b: self.mails.append((to, s, b)) or True), \
             mock.patch("pathlib.Path.home", return_value=self.Path(self.tmp)):
            a = self.argparse.Namespace(notify=None, keep_backups=0, dry_run=dry)
            with contextlib.redirect_stdout(io.StringIO()):
                return maint.cmd_auto_update(self.ctx, a)

    def plan(self, action="hold", name="some-plugin", to="3.0"):
        return {"kind": "plugin", "name": name, "from": "2.0", "to": to, "major": True, "status": "active", "action": action, "reason": ""}

    def test_nothing_to_do_is_silent(self):
        self.assertEqual(self.run_auto({"plan": [], "results": [], "applied": False}), 0)
        self.assertEqual(self.mails, [])

    def test_success_mails_and_prunes(self):
        res = {"plan": [self.plan("update", "akismet")], "applied": True, "ok": True,
               "results": [{"kind": "plugin", "name": "akismet", "from": "5.6", "to": "5.7", "now": "5.7", "ok": True, "problems": []}]}
        self.assertEqual(self.run_auto(res), 0)
        self.assertEqual(len(self.mails), 1)
        self.assertIn("1 updated", self.mails[0][1])
        self.assertEqual(self.pruned, [3])

    def test_failure_mails_and_exits_nonzero_without_pruning(self):
        res = {"plan": [self.plan("update", "x")], "applied": True, "ok": False, "backup": "b1",
               "results": [{"kind": "plugin", "name": "x", "from": "1", "to": "2", "now": None, "ok": False,
                            "problems": ["home -> HTTP 500"], "rolled_back": True}]}
        self.assertEqual(self.run_auto(res), 1)
        self.assertIn("FAILED", self.mails[0][1])
        self.assertIn("rolled back automatically", self.mails[0][2])
        self.assertEqual(self.pruned, [])

    def test_preflight_error_mails(self):
        from blog.errors import BlogError
        self.assertEqual(self.run_auto(None, raises=BlogError("site is unhealthy BEFORE updating", hint="x -> HTTP 500")), 1)
        self.assertIn("FAILED", self.mails[0][1])

    def test_new_held_mails_once_then_silent(self):
        res = {"plan": [self.plan("hold")], "results": [], "applied": False}
        self.run_auto(res)
        self.assertEqual(len(self.mails), 1)
        self.assertIn("needs a decision", self.mails[0][1])
        self.run_auto(res)                       # same held item next run: no repeat mail
        self.assertEqual(len(self.mails), 1)
        self.run_auto({"plan": [self.plan("hold", "other", "9.0")], "results": [], "applied": False})
        self.assertEqual(len(self.mails), 2)     # a different held item: mail again

    def test_dry_run_never_mails_or_writes_state(self):
        import os
        self.run_auto({"plan": [self.plan("hold")], "results": [], "applied": False}, dry=True)
        self.assertEqual(self.mails, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".local", "state", "blog", "auto-update", "last.json")))


if __name__ == "__main__":
    unittest.main()


class EditorialTests(unittest.TestCase):
    class FakeWP:
        def __init__(self, gmt):
            self.gmt = gmt

        def text(self, *a, **k):
            return self.gmt + "\n"

    class FakeCtx:
        def __init__(self, gmt):
            self.wp = EditorialTests.FakeWP(gmt)

    def test_schedule_sends_gmt_date(self):
        # without post_date_gmt WordPress silently turned a scheduled draft into a published post
        from blog.editorial import _schedule_fields
        f = _schedule_fields(self.FakeCtx("2099-01-01 07:00:00"), "2099-01-01 09:00")
        self.assertEqual(f, ["--post_date=2099-01-01 09:00:00", "--post_date_gmt=2099-01-01 07:00:00"])

    def test_schedule_in_the_past_is_refused(self):
        from blog.editorial import _schedule_fields
        with self.assertRaises(BlogError):
            _schedule_fields(self.FakeCtx("2020-01-01 07:00:00"), "2020-01-01 09:00")

    def test_schedule_bad_gmt_is_refused(self):
        from blog.editorial import _schedule_fields
        with self.assertRaises(BlogError):
            _schedule_fields(self.FakeCtx("Warning: something"), "2099-01-01 09:00")

    def test_slugify_greek_and_latin(self):
        self.assertEqual(md.slugify("AI: Get onboard or get crushed!"), "ai-get-onboard-or-get-crushed")
        self.assertEqual(md.slugify("Τι Είναι και Πώς Λειτουργεί"), "ti-einai-kai-pos-leitourgei")
        self.assertEqual(md.slugify("Μπάλα Ουρανός"), "bala-ouranos")

    def test_title_match_ignores_typography(self):
        from blog.editorial import _norm
        self.assertIn(_norm("It's a test - ok"), _norm("<h1>It&#8217;s a test &#8211; ok</h1>"))


class UpdateCheckTests(unittest.TestCase):
    def test_parse_checks(self):
        from blog.maint import parse_checks
        self.assertEqual(parse_checks("https://x.org/", ["/a/b/=hello=world", "https://y.org/c=T"]),
                         [("https://x.org/a/b/", "hello=world"), ("https://y.org/c", "T")])
        self.assertEqual(parse_checks("https://x.org", None), [])

    def test_parse_checks_rejects_malformed(self):
        from blog.maint import parse_checks
        for bad in ("/a/", "=text", "/a/="):
            with self.assertRaises(BlogError):
                parse_checks("https://x.org", [bad])
