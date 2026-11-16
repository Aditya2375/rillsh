import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { plan, parse } from "../docs/model.js";
const corpus = JSON.parse(
  execFileSync("python3", ["tests/parity.py"], {
    maxBuffer: 100 * 1024 * 1024,
  }),
);
let count = 0;
for (const row of corpus.rows) {
  let result, error;
  try {
    result = plan(row.source, corpus.env, corpus.status, corpus.pid);
  } catch (e) {
    error = e.message;
  }
  assert.deepEqual(
    { result, error },
    { result: row.result, error: row.error },
    row.source,
  );
  count++;
}
assert.equal(
  plan("echo $constructor $prototype", {
    constructor: "real",
    prototype: "text",
  }).pipelines[0].stages[0].argv.join(","),
  "echo,real,text",
);
count++;
assert.equal(plan("echo $toString", {}).pipelines[0].stages[0].argv[1], "");
count++;
assert.throws(() => parse("x".repeat(8193)));
count++;
assert.throws(() => plan("echo $X", { X: "\0" }));
count++;
assert.throws(() => plan("echo $X", { X: "\udcff" }), /valid UTF-8/);
count++;
assert.equal(plan("echo $X", { X: "🙂" }).pipelines[0].stages[0].argv[1], "🙂");
count++;
console.log(
  JSON.stringify({
    status: "PASS",
    checks: count,
    fullNativePortParity: corpus.rows.length,
  }),
);
