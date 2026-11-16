import json
import os
import random
import shlex
import subprocess
import tempfile
import unittest
from rillsh.language import ShellError, expand, lex, parse, plan


class Language(unittest.TestCase):
    def test_quotes(self):
        p = plan(
            'printf "%s\\n" a"b"\'c\' "" \'$HOME\' "$NAME" \\$', {"NAME": "two words"}
        )
        self.assertEqual(
            p["pipelines"][0]["stages"][0]["argv"],
            ["printf", "%s\\n", "abc", "", "$HOME", "two words", "$"],
        )

    def test_parameters(self):
        p = plan('printf "$? $$ ${NAME} $MISSING"', {"NAME": "a | fake"}, 7, 42)
        self.assertEqual(p["pipelines"][0]["stages"][0]["argv"][1], "7 42 a | fake ")
        self.assertEqual(
            plan("echo $MISSING", {})["pipelines"][0]["stages"][0]["argv"], ["echo", ""]
        )

    def test_ionumber_quoting_adjacency(self):
        for src, args, fd in [
            ("printf 2>f", ["printf"], 2),
            ('printf "2">f', ["printf", "2"], 1),
            ("printf 2 >f", ["printf", "2"], 1),
            ("printf a2>f", ["printf", "a2"], 1),
            ("printf \\2>f", ["printf", "2"], 1),
        ]:
            s = plan(src, {})["pipelines"][0]["stages"][0]
            self.assertEqual(s["argv"], args)
            self.assertEqual(s["redirections"][0]["fd"], fd)

    def test_order_and_snapshot_dup(self):
        a = plan("tool 2>&1 >out", {})["pipelines"][0]["stages"][0]
        b = plan("tool >out 2>&1", {})["pipelines"][0]["stages"][0]
        self.assertEqual(
            a["fds"], ["terminal:in", "open:2:truncate:out", "terminal:out"]
        )
        self.assertEqual(
            b["fds"], ["terminal:in", "open:1:truncate:out", "open:1:truncate:out"]
        )
        self.assertEqual(len(a["steps"]), 2)

    def test_pipeline_before_redirections(self):
        stages = plan("one >out | two <in", {})["pipelines"][0]["stages"]
        self.assertEqual(stages[0]["fds"][1], "open:1:truncate:out")
        self.assertEqual(stages[1]["fds"][0], "open:1:read:in")

    def test_lists_comments(self):
        p = parse("# comment\ntrue && false || pwd; export X=1\npwd # trailing")
        self.assertEqual(
            [x["gate"] for x in p["pipelines"]],
            ["always", "success", "failure", "always", "always"],
        )
        self.assertEqual(
            plan("echo x#y", {})["pipelines"][0]["stages"][0]["argv"], ["echo", "x#y"]
        )

    def test_rejects(self):
        for src in [
            'echo "x',
            "echo \\",
            "echo $(id)",
            "echo `id`",
            "x &",
            "(x)",
            "cat <<EOF",
            "echo *",
            "echo ~",
            "echo ${X:-bad}",
            "X=1 echo hi",
            "if true",
            "echo $1",
            "a |",
            "a ||",
            "a && ; b",
            "a 9>f",
            "a >",
            "| a",
            "a | | b",
            "echo \x00",
            "echo é",
        ]:
            with self.subTest(src=src), self.assertRaises(ShellError):
                parse(src)
        for src in ["x >&9", "x <&wrong"]:
            with self.assertRaises(ShellError):
                plan(src, {})

    def test_bounds(self):
        for src in [
            "x" * 8193,
            "x " + "a " * 129,
            "x " + ">f " * 17,
            "|".join(["x"] * 17),
            ";".join(["x"] * 33),
            ";" * 1025,
        ]:
            with self.assertRaises(ShellError):
                parse(src)
        self.assertEqual(len(parse("|".join(["x"] * 16))["pipelines"][0]["stages"]), 16)
        with self.assertRaises(ShellError):
            plan("echo $X", {"X": "a" * 65537})
        with self.assertRaises(ShellError):
            plan("echo $X", {"X": "\x00"})

    def test_shlex_quote_oracle_1200(self):
        rng = random.Random(814)
        chars = "abZX09 #|;&<>\\'\"$*?[]{}()~`\t"
        for _ in range(1200):
            words = [
                "".join(rng.choice(chars) for _ in range(rng.randrange(14)))
                for _ in range(rng.randrange(1, 9))
            ]
            source = "printf " + " ".join(shlex.quote(w) for w in words)
            actual = plan(source, {})["pipelines"][0]["stages"][0]["argv"][1:]
            self.assertEqual(actual, words)

    def test_literal_tokens_spans(self):
        src = 'echo ab"cd" 2>f'
        ts = lex(src)
        self.assertEqual(
            [src[t["start"] : t["end"]] for t in ts], ["echo", 'ab"cd"', "2", ">", "f"]
        )
        self.assertEqual(lex("echo a\\\nb")[1]["parts"], [["text", "ab"]])


if __name__ == "__main__":
    unittest.main()
