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
        self.mails, self.pruned, self.warnings = [], [], []
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
             mock.patch("blog.security.host_warnings_for", lambda c: list(self.warnings)), \
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


class PublishTests(unittest.TestCase):
    class WP:
        def __init__(self, post_gmt, status_after):
            self.post_gmt, self.status_after, self.updates = post_gmt, status_after, []

        def resolve_post(self, ref):
            return 7

        def text(self, *a, **k):
            if a[:2] == ("post", "get"):
                return self.post_gmt
            return "2026-11-01 11:00:00 | 2026-11-01 09:00:00"

        def run(self, *a, **k):
            self.updates.append(a)

        def post_info(self, pid):
            return {"ID": pid, "post_status": self.status_after, "post_title": "t", "url": "u"}

    class Ctx:
        json = True

        def __init__(self, wp):
            self.wp = wp

        def emit(self, *a, **k):
            pass

    def _run(self, post_gmt, status_after):
        import types
        from blog.editorial import _set_status
        wp = self.WP(post_gmt, status_after)
        ctx = self.Ctx(wp)
        wp.terms = lambda *_: []
        try:
            _set_status(ctx, types.SimpleNamespace(post="7"), "publish", "published")
        except Exception:
            raise
        return wp

    def test_publishing_a_scheduled_post_resets_its_date(self):
        # `publish` used to say "published" while the post stayed scheduled
        from unittest import mock
        with mock.patch("blog.editorial._summary"):
            wp = self._run("2030-03-03 10:00:00", "publish")
        args = wp.updates[0]
        self.assertIn("--post_date_gmt=2026-11-01 09:00:00", args)
        self.assertIn("--post_date=2026-11-01 11:00:00", args)

    def test_publishing_a_draft_keeps_its_dates(self):
        from unittest import mock
        with mock.patch("blog.editorial._summary"):
            wp = self._run("2026-10-01 09:00:00", "publish")
        self.assertEqual(wp.updates[0], ("post", "update", 7, "--post_status=publish"))

    def test_never_claims_success_when_status_did_not_change(self):
        from unittest import mock
        with mock.patch("blog.editorial._summary"), self.assertRaises(BlogError):
            self._run("2026-10-01 09:00:00", "future")


class HostWarningTests(unittest.TestCase):
    def test_thresholds(self):
        from blog.security import host_warnings
        ok = {"http": {"status": 200}, "tls_days_left": 60, "disk": ["800G", "200G", "600G", "25%"], "last_backup_days": 2}
        self.assertEqual(host_warnings(ok), [])
        self.assertEqual(len(host_warnings({**ok, "tls_days_left": 10})), 1)
        self.assertEqual(len(host_warnings({**ok, "disk": ["800G", "700G", "100G", "88%"]})), 1)
        self.assertEqual(len(host_warnings({**ok, "last_backup_days": 45})), 1)
        self.assertEqual(len(host_warnings({**ok, "last_backup_days": None})), 1)
        self.assertEqual(len(host_warnings({**ok, "http": {"status": 503}})), 1)
        self.assertEqual(host_warnings({}), [])

    def test_auto_update_mails_only_for_new_warnings(self):
        t = AutoUpdateTests("test_nothing_to_do_is_silent")
        t.setUp()
        quiet = {"plan": [], "results": [], "applied": False}
        t.warnings = ["disk is 90% full (50G available)"]
        t.run_auto(quiet)
        self.assertEqual(len(t.mails), 1)
        self.assertIn("warning", t.mails[0][1])
        t.run_auto(quiet)                      # same warning next day: no repeat mail
        self.assertEqual(len(t.mails), 1)
        t.warnings = ["disk is 90% full (50G available)", "TLS certificate expires in 5 days (automatic renewal looks broken)"]
        t.run_auto(quiet)                      # a different warning appears: mail again
        self.assertEqual(len(t.mails), 2)


class SecurityParsingTests(unittest.TestCase):
    def test_generated_files_are_not_alarms_but_code_is(self):
        from blog.security import parse_checksum_output
        out = ("wpmathpub\tphpmathpublisher/img/math_1.png\tFile was added\n"
               "evil\tshell.php\tFile was added\n"
               "akismet\tclass.akismet.php\tChecksum does not match\n"
               "Warning: Could not fetch checksums for plugin my-plugin\n")
        modified, added_php, added_other, unverifiable = parse_checksum_output(out)
        self.assertEqual(added_other, ["wpmathpub/phpmathpublisher/img/math_1.png"])
        self.assertEqual(added_php, ["evil/shell.php"])
        self.assertEqual(len(modified), 1)
        self.assertEqual(unverifiable, ["Could not fetch checksums for plugin my-plugin"])


class SnapshotTests(unittest.TestCase):
    def test_metrics_and_diff(self):
        from blog.security import diff_metrics, page_metrics
        html = "<html><head><title>T &amp; U</title><script>x=1</script></head><body><img src=a><p>Hello</p></body></html>"
        a = page_metrics(200, html)
        self.assertEqual((a["title"], a["images"], a["scripts"], a["text_chars"]), ("T & U", 1, 1, 5 + 0))
        b = page_metrics(200, html.replace("<img src=a>", "").replace("<script>x=1</script>", "<script>y=2</script><script></script>"))
        changes = {(k, x, y) for _, k, x, y in diff_metrics({"u": a}, {"u": b})}
        self.assertIn(("images", 1, 0), changes)
        self.assertIn(("scripts", 1, 2), changes)
        self.assertNotIn("text_sha", {k for k, _, _ in changes})     # visible text unchanged


class ClosedPluginTests(unittest.TestCase):
    def test_closed_notice(self):
        from blog.security import closed_notice
        d = {"error": "closed", "description": "This plugin has been closed as of March 27, 2025 and is not available for download. Reason: Security Issue."}
        self.assertEqual(closed_notice(d), "closed as of March 27, 2025. Reason: Security Issue")
        self.assertIsNone(closed_notice({"error": "Plugin not found."}))
        self.assertIsNone(closed_notice(None))


class MigrateCodeTests(unittest.TestCase):
    def conv(self, text, **kw):
        from blog.migrate import convert
        return convert(text, **kw)

    def test_rewrites_lang_and_line(self):
        new, st = self.conv('<pre lang="cpp" line="1">int main() {}</pre>')
        self.assertEqual(new, '<pre class="EnlighterJSRAW" data-enlighter-language="cpp" data-enlighter-linenumbers="true">int main() {}</pre>')
        self.assertEqual(st["blocks"], 1)

    def test_bash_becomes_shell_and_unknown_becomes_generic(self):
        self.assertIn('data-enlighter-language="shell"', self.conv('<pre lang="bash">ls</pre>')[0])
        new, st = self.conv('<pre lang="pascal">begin end.</pre>')
        self.assertIn('data-enlighter-language="generic"', new)
        self.assertEqual(st["generic"], 1)

    def test_line_offset_and_no_numbers(self):
        self.assertIn('data-enlighter-lineoffset="10"', self.conv('<pre lang="php" line="10">x</pre>')[0])
        self.assertIn('data-enlighter-linenumbers="false"', self.conv('<pre lang="php">x</pre>')[0])

    def test_raw_angle_brackets_are_escaped(self):
        # wp-syntax shows stored text literally; "#include <iostream>" would vanish as a tag if left unescaped
        new, _ = self.conv('<pre lang="cpp">#include <iostream>\nif (a<b && c>d) {}</pre>')
        self.assertIn("#include &lt;iostream&gt;", new)
        self.assertIn("a&lt;b &amp;&amp; c&gt;d", new)
        self.assertNotIn("<iostream>", new)

    def test_inline_html_in_code_is_shown_literally(self):
        new, _ = self.conv('<pre lang="pascal">x := <b>1</b>;</pre>')
        self.assertIn("&lt;b&gt;1&lt;/b&gt;", new)

    def test_existing_entities_keep_todays_display_unless_asked(self):
        kept, st = self.conv('<pre lang="bash">a &amp;&amp; b</pre>')
        self.assertIn("a &amp;amp;&amp;amp; b", kept)          # displays "&amp;&amp;" exactly as the live site does today
        self.assertEqual(st["entities"], 1)
        fixed, _ = self.conv('<pre lang="bash">a &amp;&amp; b</pre>', decode_entities=True)
        self.assertIn("a &amp;&amp; b", fixed)                  # displays "&&"

    def test_escaped_true_is_not_double_escaped(self):
        self.assertIn(">a &lt; b<", self.conv('<pre lang="php" escaped="true">a &lt; b</pre>')[0])

    def test_leading_blank_line_and_trailing_space_trimmed(self):
        self.assertIn(">x\n  y</pre>", self.conv('<pre lang="php">\nx\n  y\n\n  </pre>')[0])

    def test_untouched_cases(self):
        for same in ("<pre>plain</pre>", '<pre class="x">y</pre>', '<pre lang="">z</pre>', "no code here",
                     '<pre class="EnlighterJSRAW" data-enlighter-language="cpp">done</pre>'):
            self.assertEqual(self.conv(same)[0], same)

    def test_idempotent(self):
        once = self.conv('<pre lang="cpp" line="1">a<b</pre>')[0]
        self.assertEqual(self.conv(once)[0], once)

    def test_round_trip_text_is_identical(self):
        import html
        raw = 'if (a<b) { x = "&"; }\n\ty->z;'
        new, _ = self.conv(f'<pre lang="cpp">{raw}</pre>')
        inner = new[new.index(">") + 1:-len("</pre>")]
        self.assertEqual(html.unescape(inner), raw)


class ReadBackTests(unittest.TestCase):
    def test_newline_style_is_ignored_but_content_is_not(self):
        from blog.migrate import same_text
        self.assertTrue(same_text("a\nb\n", "a\r\nb"))
        self.assertFalse(same_text("a\nb", "a\nc"))


class GeneratedAssetTests(unittest.TestCase):
    def test_cache_dir_js_is_not_an_added_code_alarm(self):
        from blog.security import parse_checksum_output
        out = "enlighter\tcache/enlighterjs.min.js\tFile was added\nevil\tinc/shell.js\tFile was added\n"
        modified, added_php, added_other, _ = parse_checksum_output(out)
        self.assertEqual(added_other, ["enlighter/cache/enlighterjs.min.js"])
        self.assertEqual(added_php, ["evil/inc/shell.js"])


class NotifyTests(unittest.TestCase):
    def item(self, key, section="Updates waiting", text="x"):
        return {"key": key, "section": section, "text": text}

    def test_first_run_reports_everything_as_new(self):
        from blog.notify import decide
        send, new, standing, reason = decide([self.item("a"), self.item("b")], {}, 1000.0)
        self.assertEqual((send, len(new), len(standing), reason), (True, 2, 0, "new"))

    def test_standing_items_are_not_mailed_again(self):
        from blog.notify import decide
        state = {"keys": ["a", "b"], "last_sent": 990.0}
        send, new, standing, reason = decide([self.item("a"), self.item("b")], state, 1000.0)
        self.assertEqual((send, reason, len(standing)), (False, "quiet", 2))

    def test_only_the_new_item_triggers_mail(self):
        from blog.notify import decide
        send, new, standing, _ = decide([self.item("a"), self.item("c")], {"keys": ["a"], "last_sent": 990.0}, 1000.0)
        self.assertTrue(send)
        self.assertEqual([i["key"] for i in new], ["c"])
        self.assertEqual([i["key"] for i in standing], ["a"])

    def test_resolved_items_alone_do_not_trigger_mail(self):
        from blog.notify import decide
        self.assertFalse(decide([], {"keys": ["a"], "last_sent": 990.0}, 1000.0)[0])

    def test_heartbeat_only_after_the_interval(self):
        from blog.notify import decide
        day = 86400.0
        self.assertFalse(decide([], {"keys": [], "last_sent": 0.0}, 29 * day)[0])
        send, _, _, reason = decide([], {"keys": [], "last_sent": 0.0}, 31 * day)
        self.assertEqual((send, reason), (True, "heartbeat"))
        self.assertFalse(decide([], {"keys": [], "last_sent": 0.0}, 99 * day, heartbeat_days=0)[0])

    def test_changing_numbers_in_a_warning_do_not_make_it_new(self):
        from blog.notify import _norm
        self.assertEqual(_norm("TLS certificate expires in 12 days"), _norm("TLS certificate expires in 11 days"))

    def test_render_groups_and_says_nothing_was_changed(self):
        from blog.notify import render
        subject, body = render("s", [self.item("a", "Security review", "FAIL plugin x: closed")], [self.item("b", "Updates waiting", "plugin y 1 -> 2")], "new")
        self.assertIn("1 new item", subject)
        self.assertIn("NEW since the last notice", body)
        self.assertIn("Still open", body)
        self.assertIn("Nothing was changed", body)
        _, quiet = render("s", [], [], "heartbeat")
        self.assertIn("heartbeat", quiet)


class FixEntitiesTests(unittest.TestCase):
    def test_double_escaped_entities_are_fixed_only_inside_enlighter_blocks(self):
        from blog.migrate import fix_entities
        src = ('<p>a &amp;amp; b</p><pre class="EnlighterJSRAW" data-enlighter-language="shell">a &amp;amp;&amp;amp; b\n'
               'if (x&amp;lt;22) {} a &lt; b &amp;gt; c</pre>')
        new, st = fix_entities(src)
        self.assertIn("<p>a &amp;amp; b</p>", new)                       # prose untouched
        self.assertIn("a &amp;&amp; b", new)
        self.assertIn("x&lt;22", new)
        self.assertIn("a &lt; b &gt; c", new)                           # single-escaped text is already right
        self.assertEqual(st["blocks"], 1)
        self.assertEqual(st["preview"][0][1:], ("a &amp;&amp; b", "a && b"))

    def test_clean_blocks_are_left_alone_and_idempotent(self):
        from blog.migrate import fix_entities
        src = '<pre class="EnlighterJSRAW" data-enlighter-language="cpp">a &lt; b &amp;&amp; c</pre>'
        self.assertEqual(fix_entities(src)[0], src)
        once = fix_entities('<pre class="EnlighterJSRAW">x &amp;amp; y</pre>')[0]
        self.assertEqual(fix_entities(once)[0], once)
