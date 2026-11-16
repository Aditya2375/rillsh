// Independent browser port of the bounded language. Never executes commands.
export const limits = {
  source: 8192,
  tokens: 1024,
  pipelines: 32,
  stages: 16,
  words: 128,
  redirections: 16,
};
const name = /^[A-Za-z_][A-Za-z0-9_]*$/;
const reserved = new Set(
  "if then else elif fi do done for while until case esac in function select time ! { }".split(
    " ",
  ),
);
const ops = [
  "&&",
  "||",
  ">>",
  ">&",
  "<&",
  "<<",
  "|",
  ";",
  "<",
  ">",
  "&",
  "(",
  ")",
];
const redirs = new Set(["<", ">", ">>", ">&", "<&"]);
const fail = (s) => {
  throw new Error(s);
};
export function lex(source) {
  if (typeof source !== "string" || source.length > 8192)
    fail("source exceeds 8192 characters");
  if (
    [...source].some(
      (c) =>
        (c.charCodeAt(0) < 32 && !["\n", "\t"].includes(c)) ||
        c.charCodeAt(0) > 126,
    )
  )
    fail("source must be printable ASCII, tab or newline");
  let i = 0;
  const tokens = [];
  while (i < source.length) {
    let c = source[i];
    if (" \t".includes(c)) {
      i++;
      continue;
    }
    if (c === "#") {
      while (i < source.length && source[i] !== "\n") i++;
      continue;
    }
    if (c === "\n") {
      tokens.push({ kind: "op", value: ";", start: i, end: i + 1 });
      i++;
      continue;
    }
    let op = ops.find((o) => source.startsWith(o, i));
    if (op) {
      if (["<<", "&", "(", ")"].includes(op))
        fail("unsupported operator at " + i);
      tokens.push({ kind: "op", value: op, start: i, end: i + op.length });
      i += op.length;
      continue;
    }
    let start = i,
      parts = [],
      plain = true,
      quote = "";
    const text = (v) => {
      if (parts.length && parts.at(-1)[0] === "text") parts.at(-1)[1] += v;
      else parts.push(["text", v]);
    };
    while (i < source.length) {
      c = source[i];
      if (
        !quote &&
        (" \t\n".includes(c) || ops.some((o) => source.startsWith(o, i)))
      )
        break;
      if (c === "'" && quote !== '"') {
        plain = false;
        if (quote === "'") quote = "";
        else {
          quote = "'";
          text("");
        }
        i++;
        continue;
      }
      if (c === '"' && quote !== "'") {
        plain = false;
        if (quote === '"') quote = "";
        else {
          quote = '"';
          text("");
        }
        i++;
        continue;
      }
      if (c === "\\" && quote !== "'") {
        if (i + 1 === source.length) fail("unfinished escape at " + i);
        const n = source[i + 1];
        if (n === "\n") {
          i += 2;
          continue;
        }
        plain = false;
        if (quote === '"' && !'$`"\\'.includes(n)) {
          text("\\");
          i++;
        } else {
          text(n);
          i += 2;
        }
        continue;
      }
      if (c === "$" && quote !== "'") {
        plain = false;
        i++;
        if (i < source.length && "?$".includes(source[i])) {
          parts.push(["param", source[i]]);
          i++;
        } else if (i < source.length && source[i] === "{") {
          const end = source.indexOf("}", i + 1),
            n = end !== -1 ? source.slice(i + 1, end) : "";
          if (!name.test(n)) fail("only ${NAME} parameter syntax is supported");
          parts.push(["param", n]);
          i = end + 1;
        } else if (i < source.length && /[A-Za-z_]/.test(source[i])) {
          let end = i + 1;
          while (end < source.length && /[A-Za-z0-9_]/.test(source[end])) end++;
          parts.push(["param", source.slice(i, end)]);
          i = end;
        } else if (
          i < source.length &&
          "('\"0123456789@*#!-".includes(source[i])
        )
          fail("unsupported dollar expansion");
        else text("$");
        continue;
      }
      if (quote !== "'" && c === "`")
        fail("command substitution is not supported");
      if (!quote && "*?[~{}".includes(c))
        fail("quote literal glob, tilde or brace characters");
      text(c);
      i++;
    }
    if (quote) fail("unclosed quote at " + start);
    if (!parts.length) continue;
    tokens.push({ kind: "word", parts, plain, start, end: i });
    if (tokens.length > 1024) fail("token limit exceeded");
  }
  if (tokens.length > 1024) fail("token limit exceeded");
  return tokens;
}
const literal = (t) =>
  t.parts.every((p) => p[0] === "text")
    ? t.parts.map((p) => p[1]).join("")
    : null;
export function parse(source) {
  const tokens = lex(source),
    result = [];
  let i = 0,
    gate = "always";
  while (i < tokens.length) {
    if (tokens[i].kind === "op" && tokens[i].value === ";") {
      i++;
      continue;
    }
    const stages = [];
    while (true) {
      const words = [],
        rs = [];
      while (i < tokens.length) {
        let t = tokens[i];
        if (t.kind === "op" && !redirs.has(t.value)) break;
        let fd = null;
        if (
          t.kind === "word" &&
          t.plain &&
          literal(t) !== null &&
          /^[0-9]+$/.test(literal(t)) &&
          i + 1 < tokens.length &&
          tokens[i + 1].kind === "op" &&
          redirs.has(tokens[i + 1].value) &&
          t.end === tokens[i + 1].start
        ) {
          const number = literal(t).replace(/^0+/, "") || "0";
          if (!["0", "1", "2"].includes(number))
            fail("only descriptors 0, 1 and 2 are supported");
          fd = Number(number);
          i++;
          t = tokens[i];
        }
        if (t.kind === "op") {
          const op = t.value;
          if (fd === null) fd = ["<", "<&"].includes(op) ? 0 : 1;
          if (![0, 1, 2].includes(fd))
            fail("only descriptors 0, 1 and 2 are supported");
          i++;
          if (i === tokens.length || tokens[i].kind !== "word")
            fail("redirection needs one word");
          rs.push({ fd, op, word: tokens[i] });
        } else words.push(t);
        i++;
        if (words.length > 128 || rs.length > 16)
          fail("command limit exceeded");
      }
      if (!words.length) fail("each stage requires a command");
      const first = literal(words[0]);
      if (
        words[0].plain &&
        (reserved.has(first) ||
          (first !== null && /^[A-Za-z_][A-Za-z0-9_]*=/.test(first)))
      )
        fail("reserved words and assignment prefixes are not supported");
      stages.push({ words, redirections: rs });
      if (stages.length > 16) fail("pipeline stage limit exceeded");
      if (
        i < tokens.length &&
        tokens[i].kind === "op" &&
        tokens[i].value === "|"
      ) {
        i++;
        continue;
      }
      break;
    }
    result.push({ gate, stages });
    if (result.length > 32) fail("pipeline count limit exceeded");
    if (i === tokens.length) break;
    const op = tokens[i].value;
    if (![";", "&&", "||"].includes(op)) fail("unexpected operator");
    gate = { ";": "always", "&&": "success", "||": "failure" }[op];
    i++;
    if (gate !== "always" && (i === tokens.length || tokens[i].kind === "op"))
      fail("conditional operator needs a pipeline");
  }
  return { tokens, pipelines: result };
}
export function expand(word, env, status, pid) {
  const value = word.parts
    .map(([kind, v]) =>
      kind === "text"
        ? v
        : v === "?"
          ? String(status)
          : v === "$"
            ? String(pid)
            : Object.hasOwn(env, v)
              ? env[v]
              : "",
    )
    .join("");
  if (
    [...value].some(
      (c) => c.codePointAt(0) >= 0xd800 && c.codePointAt(0) <= 0xdfff,
    )
  )
    fail("expanded word must be valid UTF-8");
  if (value.includes("\0") || new TextEncoder().encode(value).length > 65536)
    fail("expanded word exceeds limit or contains NUL");
  return value;
}
export function prepare(stage, env, status, pid) {
  const argv = stage.words.map((w) => expand(w, env, status, pid));
  const redirections = stage.redirections.map((r) => {
    const target = expand(r.word, env, status, pid);
    if ([">&", "<&"].includes(r.op) && !["0", "1", "2", "-"].includes(target))
      fail("descriptor target must be 0, 1, 2 or -");
    return { fd: r.fd, op: r.op, target };
  });
  return { argv, redirections };
}
export function plan(source, env, status = 0, pid = 4242) {
  const ast = parse(source);
  const pipelines = ast.pipelines.map((pipe) => ({
    gate: pipe.gate,
    stages: pipe.stages.map((stage, i) => {
      const ready = prepare(stage, env, status, pid),
        fds = [
          i === 0 ? "terminal:in" : `pipe:${i - 1}:read`,
          i === pipe.stages.length - 1 ? "terminal:out" : `pipe:${i}:write`,
          "terminal:err",
        ],
        steps = [];
      for (const [index, r] of ready.redirections.entries()) {
        const { fd, op, target } = r;
        if ([">&", "<&"].includes(op)) {
          if (target !== "-" && fds[Number(target)] === "closed")
            fail("cannot duplicate a closed descriptor");
          fds[fd] = target === "-" ? "closed" : fds[Number(target)];
        } else
          fds[fd] =
            "open:" +
            (index + 1) +
            ":" +
            { "<": "read:", ">": "truncate:", ">>": "append:" }[op] +
            target;
        steps.push({ redirection: r, fds: [...fds] });
      }
      return { ...ready, fds, steps };
    }),
  }));
  return { ast, pipelines };
}
