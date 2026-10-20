import unittest

from devkit.table import format_table, parse_table


class TestFormat(unittest.TestCase):
    def test_basic(self):
        out = format_table(["Name", "Qty"], [["apple", 3], ["kiwi", 12]])
        self.assertEqual(out, "\n".join([
            "| Name  | Qty |",
            "| ----- | --- |",
            "| apple | 3   |",
            "| kiwi  | 12  |",
        ]))

    def test_alignment(self):
        out = format_table(["a", "b", "c", "d"],
                           [["x", "yy", "zzzz", "w"]],
                           ["left", "right", "center", None])
        self.assertEqual(out, "\n".join([
            "| a   |   b |  c   | d   |",
            "| :-- | --: | :--: | --- |",
            "| x   |  yy | zzzz | w   |",
        ]))

    def test_center_odd_space_on_right(self):
        out = format_table(["ab", "x"], [["abcdefg", "y"]], ["center", None])
        self.assertEqual(out.splitlines()[0], "|   ab    | x   |")

    def test_escape_none_and_short_rows(self):
        out = format_table(["k", "v"], [["a|b", None], ["c"]])
        self.assertEqual(out, "\n".join([
            "| k    | v   |",
            "| ---- | --- |",
            "| a\\|b |     |",
            "| c    |     |",
        ]))

    def test_no_rows(self):
        self.assertEqual(format_table(["x"], []), "| x   |\n| --- |")

    def test_errors(self):
        with self.assertRaises(ValueError):
            format_table(["a"], [["1", "2"]])
        with self.assertRaises(ValueError):
            format_table(["a", "b"], [], ["left"])
        with self.assertRaises(ValueError):
            format_table(["a"], [], ["middle"])


class TestParse(unittest.TestCase):
    def test_roundtrip(self):
        header, rows = ["k", "v"], [["a|b", "1"], ["c", ""]]
        align = ["left", "right"]
        got = parse_table(format_table(header, rows, align))
        self.assertEqual([list(x) for x in got], [header, rows, align])

    def test_loose_input(self):
        text = "a|b|c\n:-|:-:|-\n1|2|3\n|  4 | 5 |6|"
        got = parse_table(text)
        self.assertEqual([list(x) for x in got], [
            ["a", "b", "c"], [["1", "2", "3"], ["4", "5", "6"]],
            ["left", "center", None],
        ])

    def test_errors(self):
        for text in ["| a |", "| a |\n| b |", "| a | b |\n| --- |",
                     "| a | b |\n| --- | --- |\n| 1 |",
                     "| a |\n| -:- |"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_table(text)


if __name__ == "__main__":
    unittest.main()
