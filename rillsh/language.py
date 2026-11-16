"""Quote-aware lexer, bounded grammar, expansion and descriptor-plan model."""

import re

LIMITS = {
    "source": 8192,
    "tokens": 1024,
    "pipelines": 32,
    "stages": 16,
    "words": 128,
    "redirections": 16,
}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
RESERVED = set(
    "if then else elif fi do done for while until case esac in function select time ! { }".split()
)
OPS = ("&&", "||", ">>", ">&", "<&", "<<", "|", ";", "<", ">", "&", "(", ")")
REDIRS = {"<", ">", ">>", ">&", "<&"}


class ShellError(ValueError):
    pass


def lex(source):
    if not isinstance(source, str) or len(source) > LIMITS["source"]:
        raise ShellError("source exceeds 8192 characters")
    if any((ord(c) < 32 and c not in "\n\t") or ord(c) > 126 for c in source):
        raise ShellError("source must be printable ASCII, tab or newline")
    tokens = []
    i = 0
    while i < len(source):
        c = source[i]
        if c in " \t":
            i += 1
            continue
        if c == "#":
            while i < len(source) and source[i] != "\n":
                i += 1
            continue
        if c == "\n":
            tokens.append({"kind": "op", "value": ";", "start": i, "end": i + 1})
            i += 1
            continue
        op = next((o for o in OPS if source.startswith(o, i)), None)
        if op:
            if op in {"<<", "&", "(", ")"}:
                raise ShellError("unsupported operator at " + str(i))
            tokens.append({"kind": "op", "value": op, "start": i, "end": i + len(op)})
            i += len(op)
            continue
        start, parts, plain, quote = i, [], True, ""

        def text(v):
            if parts and parts[-1][0] == "text":
                parts[-1][1] += v
            else:
                parts.append(["text", v])

        while i < len(source):
            c = source[i]
            if not quote and (
                c in " \t\n" or any(source.startswith(o, i) for o in OPS)
            ):
                break
            if c == "'" and quote != '"':
                plain = False
                if quote == "'":
                    quote = ""
                else:
                    quote = "'"
                    text("")
                i += 1
                continue
            if c == '"' and quote != "'":
                plain = False
                if quote == '"':
                    quote = ""
                else:
                    quote = '"'
                    text("")
                i += 1
                continue
            if c == "\\" and quote != "'":
                if i + 1 == len(source):
                    raise ShellError("unfinished escape at " + str(i))
                n = source[i + 1]
                if n == "\n":
                    i += 2
                    continue
                plain = False
                if quote == '"' and n not in '$`"\\':
                    text("\\")
                    i += 1
                else:
                    text(n)
                    i += 2
                continue
            if c == "$" and quote != "'":
                plain = False
                i += 1
                if i < len(source) and source[i] in "?$":
                    parts.append(["param", source[i]])
                    i += 1
                elif i < len(source) and source[i] == "{":
                    end = source.find("}", i + 1)
                    name = source[i + 1 : end] if end != -1 else ""
                    if not NAME.fullmatch(name):
                        raise ShellError("only ${NAME} parameter syntax is supported")
                    parts.append(["param", name])
                    i = end + 1
                elif i < len(source) and (source[i].isalpha() or source[i] == "_"):
                    end = i + 1
                    while end < len(source) and (
                        source[end].isalnum() or source[end] == "_"
                    ):
                        end += 1
                    parts.append(["param", source[i:end]])
                    i = end
                elif i < len(source) and source[i] in "('\"0123456789@*#!-":
                    raise ShellError("unsupported dollar expansion")
                else:
                    text("$")
                continue
            if quote != "'" and c == "`":
                raise ShellError("command substitution is not supported")
            if not quote and c in "*?[~{}":
                raise ShellError("quote literal glob, tilde or brace characters")
            text(c)
            i += 1
        if quote:
            raise ShellError("unclosed quote at " + str(start))
        # A removed backslash-newline alone is not an empty argument.
        if not parts:
            continue
        tokens.append(
            {"kind": "word", "parts": parts, "plain": plain, "start": start, "end": i}
        )
        if len(tokens) > LIMITS["tokens"]:
            raise ShellError("token limit exceeded")
    if len(tokens) > LIMITS["tokens"]:
        raise ShellError("token limit exceeded")
    return tokens


def literal(token):
    return (
        "".join(p[1] for p in token["parts"])
        if all(p[0] == "text" for p in token["parts"])
        else None
    )


def parse(source):
    tokens = lex(source)
    result = []
    i, gate = 0, "always"
    while i < len(tokens):
        if tokens[i]["kind"] == "op" and tokens[i]["value"] == ";":
            i += 1
            continue
        stages = []
        while True:
            words, redirs = [], []
            while i < len(tokens):
                t = tokens[i]
                if t["kind"] == "op" and t["value"] not in REDIRS:
                    break
                fd = None
                if (
                    t["kind"] == "word"
                    and t["plain"]
                    and literal(t) is not None
                    and literal(t).isdigit()
                    and i + 1 < len(tokens)
                    and tokens[i + 1]["kind"] == "op"
                    and tokens[i + 1]["value"] in REDIRS
                    and t["end"] == tokens[i + 1]["start"]
                ):
                    number = literal(t).lstrip("0") or "0"
                    if number not in {"0", "1", "2"}:
                        raise ShellError("only descriptors 0, 1 and 2 are supported")
                    fd = int(number)
                    i += 1
                    t = tokens[i]
                if t["kind"] == "op":
                    op = t["value"]
                    fd = (0 if op in {"<", "<&"} else 1) if fd is None else fd
                    if fd not in (0, 1, 2):
                        raise ShellError("only descriptors 0, 1 and 2 are supported")
                    i += 1
                    if i == len(tokens) or tokens[i]["kind"] != "word":
                        raise ShellError("redirection needs one word")
                    redirs.append({"fd": fd, "op": op, "word": tokens[i]})
                else:
                    words.append(t)
                i += 1
                if len(words) > LIMITS["words"] or len(redirs) > LIMITS["redirections"]:
                    raise ShellError("command limit exceeded")
            if not words:
                raise ShellError("each stage requires a command")
            first = literal(words[0])
            if words[0]["plain"] and (
                first in RESERVED
                or (first is not None and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", first))
            ):
                raise ShellError(
                    "reserved words and assignment prefixes are not supported"
                )
            stages.append({"words": words, "redirections": redirs})
            if len(stages) > LIMITS["stages"]:
                raise ShellError("pipeline stage limit exceeded")
            if (
                i < len(tokens)
                and tokens[i]["kind"] == "op"
                and tokens[i]["value"] == "|"
            ):
                i += 1
                continue
            break
        result.append({"gate": gate, "stages": stages})
        if len(result) > LIMITS["pipelines"]:
            raise ShellError("pipeline count limit exceeded")
        if i == len(tokens):
            break
        op = tokens[i]["value"]
        if op not in {";", "&&", "||"}:
            raise ShellError("unexpected operator")
        gate = {";": "always", "&&": "success", "||": "failure"}[op]
        i += 1
        if gate != "always" and (i == len(tokens) or tokens[i]["kind"] == "op"):
            raise ShellError("conditional operator needs a pipeline")
    return {"tokens": tokens, "pipelines": result}


def expand(word, env, status, pid):
    value = "".join(
        (
            v
            if kind == "text"
            else str(status) if v == "?" else str(pid) if v == "$" else env.get(v, "")
        )
        for kind, v in word["parts"]
    )
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ShellError("expanded word must be valid UTF-8") from exc
    if "\x00" in value or size > 65536:
        raise ShellError("expanded word exceeds limit or contains NUL")
    return value


def prepare(stage, env, status, pid):
    argv = [expand(w, env, status, pid) for w in stage["words"]]
    redirs = []
    for r in stage["redirections"]:
        target = expand(r["word"], env, status, pid)
        if r["op"] in {">&", "<&"} and target not in {"0", "1", "2", "-"}:
            raise ShellError("descriptor target must be 0, 1, 2 or -")
        redirs.append({"fd": r["fd"], "op": r["op"], "target": target})
    return {"argv": argv, "redirections": redirs}


def plan(source, env, status=0, pid=4242):
    ast = parse(source)
    pipelines = []
    for pipe in ast["pipelines"]:
        stages = []
        for i, stage in enumerate(pipe["stages"]):
            ready = prepare(stage, env, status, pid)
            fds = [
                "terminal:in" if i == 0 else "pipe:" + str(i - 1) + ":read",
                (
                    "terminal:out"
                    if i == len(pipe["stages"]) - 1
                    else "pipe:" + str(i) + ":write"
                ),
                "terminal:err",
            ]
            steps = []
            for index, r in enumerate(ready["redirections"]):
                fd, op, target = r["fd"], r["op"], r["target"]
                if op in {">&", "<&"}:
                    if target != "-" and fds[int(target)] == "closed":
                        raise ShellError("cannot duplicate a closed descriptor")
                    fds[fd] = "closed" if target == "-" else fds[int(target)]
                else:
                    fds[fd] = (
                        "open:"
                        + str(index + 1)
                        + ":"
                        + {"<": "read:", ">": "truncate:", ">>": "append:"}[op]
                        + target
                    )
                steps.append({"redirection": r, "fds": list(fds)})
            stages.append({**ready, "fds": fds, "steps": steps})
        pipelines.append({"gate": pipe["gate"], "stages": stages})
    return {"ast": ast, "pipelines": pipelines}
