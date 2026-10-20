import unittest

from devkit.semver import Version, compare, parse, satisfies


class TestParse(unittest.TestCase):
    def test_full(self):
        v = parse("1.2.3-rc.1+build.5")
        self.assertEqual((v.major, v.minor, v.patch), (1, 2, 3))
        self.assertEqual(tuple(v.prerelease), ("rc", "1"))
        self.assertEqual(tuple(v.build), ("build", "5"))

    def test_str_roundtrip(self):
        for s in ["0.0.0", "1.2.3", "1.2.3-alpha.1", "1.0.0+20130313",
                  "1.0.0-x-y.7+exp.sha.5114f85"]:
            self.assertEqual(str(parse(s)), s)

    def test_invalid(self):
        for s in ["1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.3-01",
                  "1.2.3-", "1.2.3+", "1.2.3-a..b", "v1.2.3", "1.2.x", ""]:
            with self.subTest(s=s), self.assertRaises(ValueError):
                parse(s)


class TestCompare(unittest.TestCase):
    def test_spec_order(self):
        order = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta",
                 "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
                 "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
        for a, b in zip(order, order[1:]):
            with self.subTest(a=a, b=b):
                self.assertEqual(compare(a, b), -1)
                self.assertEqual(compare(b, a), 1)

    def test_numeric_parts(self):
        self.assertEqual(compare("1.10.0", "1.9.0"), 1)
        self.assertEqual(compare("10.0.0", "9.99.99"), 1)

    def test_build_ignored(self):
        self.assertEqual(compare("1.0.0+a", "1.0.0+b"), 0)
        self.assertEqual(compare(Version(1, 0, 0), "1.0.0"), 0)


class TestSatisfies(unittest.TestCase):
    def check(self, rng, yes, no):
        for v in yes:
            with self.subTest(range=rng, v=v):
                self.assertTrue(satisfies(v, rng))
        for v in no:
            with self.subTest(range=rng, v=v):
                self.assertFalse(satisfies(v, rng))

    def test_operators(self):
        self.check(">=1.2.0 <1.5.0", ["1.2.0", "1.4.99"], ["1.1.9", "1.5.0"])
        self.check("1.2.3", ["1.2.3", "1.2.3+b"], ["1.2.4"])
        self.check("=1.2.3", ["1.2.3"], ["1.2.2"])
        self.check(">1.0.0 <=2.0.0", ["1.0.1", "2.0.0"], ["1.0.0", "2.0.1"])

    def test_caret(self):
        self.check("^1.2.3", ["1.2.3", "1.9.0"], ["1.2.2", "2.0.0"])
        self.check("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0", "0.2.2"])
        self.check("^0.0.3", ["0.0.3"], ["0.0.4", "0.0.2"])

    def test_tilde(self):
        self.check("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0", "1.2.2"])

    def test_or(self):
        self.check("^1.0.0 || >=3.0.0", ["1.5.0", "3.1.0"], ["2.0.0"])

    def test_prerelease(self):
        self.check(">=1.2.3-beta.1", ["1.2.3-beta.2", "1.2.3", "1.3.0"],
                   ["1.2.3-alpha", "1.2.4-beta.1"])
        self.check(">=1.0.0", ["1.0.0"], ["1.2.3-beta.2"])
        self.check("^1.2.3-rc.1", ["1.2.3-rc.2", "1.5.0"], ["2.0.0-rc.1"])

    def test_invalid_range(self):
        for r in ["", ">=", "^1.2", ">=1.0.0 || ", "abc"]:
            with self.subTest(r=r), self.assertRaises(ValueError):
                satisfies("1.0.0", r)


if __name__ == "__main__":
    unittest.main()
