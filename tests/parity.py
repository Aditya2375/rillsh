"""Private-free generated corpus for independent language-port parity."""

import json
import random
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rillsh.language import plan, ShellError

rng = random.Random(419)
env = {
    "NAME": "Ada Lovelace",
    "OUT": "report.txt",
    "constructor": "valid",
    "prototype": "text",
}
corpus = [
    'printf "%s\\n" "$NAME" | wc -c >$OUT 2>&1',
    "tool 2>&1 >out",
    "true && false || pwd",
    'echo "" \'$NAME\' "$? $$"',
    "echo $constructor $prototype",
    "x 2>f",
    'x "2">f',
    "echo \\2>f",
    "a |",
    "a &&",
    "echo ${X:-x}",
    "x >&9",
    "x <&-",
    "echo $(id)",
    "x &",
    "echo é",
    "echo\x00",
    'echo "unclosed',
    "x " * 129,
    "|".join(["x"] * 17),
]
fragments = [
    "a",
    '"two words"',
    "''",
    "'$NAME'",
    '"$NAME"',
    "${OUT}",
    "$?",
    "$$",
    r"a\ b",
    r'"x\qy"',
    "constructor",
    "prototype",
    '"*"',
    "2",
    '"2"',
    "a#b",
    '"<script>"',
]
for _ in range(2500):
    stages = []
    for _ in range(rng.randrange(1, 5)):
        stage = (
            rng.choice(["printf", "cat", "pwd", "false", "export"])
            + " "
            + " ".join(rng.choice(fragments) for _ in range(rng.randrange(0, 6)))
        )
        for _ in range(rng.randrange(3)):
            stage += " " + rng.choice(
                [">out", "2>&1", "<in", ">>$OUT", "1>&-", "2>err", "0<&1"]
            )
        stages.append(stage)
    corpus.append(" | ".join(stages) + rng.choice(["", "; pwd", " && true || false"]))
for _ in range(400):
    corpus.append(
        "".join(
            rng.choice("ab|;&<>$\\'\"(){}012 #\n") for _ in range(rng.randrange(30))
        )
    )
corpus += [
    "x " + "9" * 6000 + ">f",
    "x " + "0" * 6000 + "2>f",
    "x 0<&- 1>&0",
    "x >out 2>out",
    "x >out 2>&1",
]
rows = []
for source in corpus:
    try:
        rows.append({"source": source, "result": plan(source, env, 7, 4242)})
    except ShellError as e:
        rows.append({"source": source, "error": str(e)})
print(json.dumps({"env": env, "status": 7, "pid": 4242, "rows": rows}))
