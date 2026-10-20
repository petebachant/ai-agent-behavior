import unittest

from devkit.ini import InterpolationError, ParseError, parse


class TestParse(unittest.TestCase):
    def test_basic(self):
        text = "# comment\n[server]\nHost = example.com\nport: 8080\n\n; x\n"
        self.assertEqual(parse(text),
                         {"server": {"host": "example.com", "port": "8080"}})

    def test_first_separator_wins(self):
        self.assertEqual(parse("[a]\nurl = http://x:1\ntime: 10=ten\n"),
                         {"a": {"url": "http://x:1", "time": "10=ten"}})

    def test_order_repeats_and_whitespace(self):
        text = "[ b ]\nx = 1\n[a]\ny = 2\n[b]\nz = 3\nX = 4\n"
        out = parse(text)
        self.assertEqual(list(out), ["b", "a"])
        self.assertEqual(out["b"], {"x": "4", "z": "3"})
        self.assertEqual(list(out["b"]), ["x", "z"])

    def test_section_names_case_sensitive(self):
        self.assertEqual(list(parse("[A]\n[a]\n")), ["A", "a"])

    def test_empty_value(self):
        self.assertEqual(parse("[a]\nk =\n"), {"a": {"k": ""}})

    def test_continuation(self):
        text = "[a]\nlist = one\n  two\n\tthree\nnext = x\n"
        self.assertEqual(parse(text),
                         {"a": {"list": "one\ntwo\nthree", "next": "x"}})

    def test_blank_line_ends_continuation(self):
        text = "[a]\nk = v\n\n  j = w\n"
        self.assertEqual(parse(text), {"a": {"k": "v", "j": "w"}})

    def test_parse_errors(self):
        for text, lineno in [("k = v\n", 1), ("[a]\nk = v\njunk\n", 3),
                             ("[a]\n\n= v\n", 3)]:
            with self.subTest(text=text):
                with self.assertRaises(ParseError) as cm:
                    parse(text)
                self.assertEqual(cm.exception.lineno, lineno)
                self.assertIsInstance(cm.exception, ValueError)


class TestInterpolation(unittest.TestCase):
    def test_same_and_other_section(self):
        text = ("[paths]\nroot = /srv\ndata = ${root}/data\n"
                "[app]\nlog = ${paths:data}/app.log\n")
        out = parse(text)
        self.assertEqual(out["paths"]["data"], "/srv/data")
        self.assertEqual(out["app"]["log"], "/srv/data/app.log")

    def test_keys_lowercased_in_refs(self):
        self.assertEqual(parse("[a]\nName = x\nk = ${NAME}\n")["a"]["k"], "x")

    def test_dollars(self):
        out = parse("[a]\nx = 1\np = $$5 and $${x} and $ alone\n")
        self.assertEqual(out["a"]["p"], "$5 and ${x} and $ alone")

    def test_forward_reference(self):
        self.assertEqual(parse("[a]\nb = ${c}!\nc = hi\n")["a"]["b"], "hi!")

    def test_errors(self):
        for text in ["[a]\nk = ${missing}\n", "[a]\nk = ${b:x}\n",
                     "[a]\nx = ${y}\ny = ${x}\n", "[a]\nx = ${x}\n"]:
            with self.subTest(text=text), self.assertRaises(InterpolationError):
                parse(text)


if __name__ == "__main__":
    unittest.main()
