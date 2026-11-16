import { plan } from "./model.js";
const $ = (id) => document.getElementById(id);
const presets = {
  pipeline: 'printf "%s\\n" "$NAME" | tr a-z A-Z | wc -c >$OUT 2>&1',
  quotes: 'printf "%s\\n" "hello $NAME" \'$NAME\' "" a"b"\'c\'',
  order: "tool 2>&1 >$OUT; tool >$OUT 2>&1",
  lists: 'false && printf skipped || printf recovered; printf "%s" "$?"',
};
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const maps = (fds, parent) =>
  fds.forEach((v, i) => {
    const n = el("div", undefined, "fd");
    n.append(el("span", String(i) + " →", "fd-label"), el("span", v));
    parent.append(n);
  });
function clear(state, message) {
  $("state").textContent = state;
  $("state").className =
    "badge " + (state === "INVALID INPUT" ? "bad" : "dirty");
  $("error").textContent = message;
  $("tokens").replaceChildren();
  $("plans").replaceChildren();
  for (const id of ["token-count", "pipe-count", "stage-count"])
    $(id).textContent = "0";
  $("summary").textContent =
    state === "INVALID INPUT" ? "No valid plan." : "Ready when you are.";
  $("summary-note").textContent =
    state === "INVALID INPUT"
      ? "Fix the input and inspect again. No previous plan is retained."
      : "Input changed. Inspect again to replace the previous plan.";
  $("plans").append(el("p", "No current plan.", "empty"));
}
function inspect() {
  try {
    const status = $("status").value;
    if (!/^(0|[1-9][0-9]{0,2})$/.test(status) || Number(status) > 255)
      throw new Error("Sample status must be an integer from 0 to 255.");
    if ($("name").value.length > 256 || $("out").value.length > 256)
      throw new Error("Sample variable exceeds 256 characters.");
    const source = $("source").value,
      result = plan(
        source,
        { NAME: $("name").value, OUT: $("out").value },
        Number(status),
        4242,
      );
    $("tokens").replaceChildren();
    $("plans").replaceChildren();
    $("error").textContent = "";
    $("state").className = "badge";
    $("state").textContent = "PARSE COMPLETE";
    $("token-count").textContent = String(result.ast.tokens.length);
    $("pipe-count").textContent = String(result.pipelines.length);
    const stages = result.pipelines.reduce((n, p) => n + p.stages.length, 0);
    $("stage-count").textContent = String(stages);
    $("summary").textContent = stages
      ? `${stages} stage${stages === 1 ? "" : "s"}. No execution.`
      : "An empty line.";
    $("summary-note").textContent =
      "Valid bounded-language structure. This is a preview, not a runtime result.";
    for (const token of result.ast.tokens) {
      const n = el(
        "div",
        undefined,
        "token " + (token.kind === "op" ? "op" : "word"),
      );
      n.append(
        el(
          "small",
          `${token.kind.toUpperCase()} / ${token.start}:${token.end}`,
        ),
        el("span", source.slice(token.start, token.end)),
      );
      $("tokens").append(n);
    }
    result.pipelines.forEach((pipe, p) => {
      const box = el("article", undefined, "pipeline");
      const head = el("div", undefined, "pipeline-head");
      head.append(
        el("span", `PIPELINE / ${String(p + 1).padStart(2, "0")}`),
        el(
          "span",
          {
            always: "ALWAYS",
            success: "IF PREVIOUS STATUS = 0",
            failure: "IF PREVIOUS STATUS ≠ 0",
          }[pipe.gate],
          "gate",
        ),
      );
      box.append(head);
      const list = el("div", undefined, "stage-list");
      pipe.stages.forEach((stage, i) => {
        const card = el("section", undefined, "stage");
        card.append(
          el(
            "p",
            `STAGE ${String(i + 1).padStart(2, "0")} / ${pipe.stages.length > 1 ? "CHILD PROCESS" : "PARENT IF BUILTIN, ELSE CHILD"}`,
            "stage-number",
          ),
          el("h4", stage.argv[0] || "Empty command name"),
        );
        const argv = el("ol", undefined, "argv");
        stage.argv.forEach((v, a) => {
          const li = el("li");
          li.append(
            el("span", String(a).padStart(2, "0"), "index"),
            el("code", JSON.stringify(v)),
          );
          argv.append(li);
        });
        card.append(argv);
        maps(stage.fds, card);
        if (stage.steps.length) {
          const details = el("details"),
            summary = el(
              "summary",
              `Follow ${stage.steps.length} ordered redirection${stage.steps.length === 1 ? "" : "s"}`,
            );
          details.append(summary);
          stage.steps.forEach((step, s) => {
            const n = el("div", undefined, "step");
            n.append(
              el(
                "p",
                `${s + 1}. ${step.redirection.fd}${step.redirection.op}${JSON.stringify(step.redirection.target)}`,
              ),
            );
            maps(step.fds, n);
            details.append(n);
          });
          card.append(details);
        }
        list.append(card);
      });
      box.append(list);
      $("plans").append(box);
    });
    if (!result.pipelines.length)
      $("plans").append(
        el(
          "p",
          "No pipelines. The native shell keeps its previous status.",
          "empty",
        ),
      );
  } catch (error) {
    clear("INVALID INPUT", error.message);
  }
}
$("inspect").addEventListener("click", inspect);
for (const id of ["source", "name", "out", "status"])
  $(id).addEventListener("input", () => {
    clear("INPUT CHANGED", "");
    document
      .querySelectorAll("[data-preset]")
      .forEach(
        (b) => (
          b.classList.remove("active"),
          b.setAttribute("aria-pressed", "false")
        ),
      );
  });
document.querySelectorAll("[data-preset]").forEach((button) =>
  button.addEventListener("click", () => {
    $("source").value = presets[button.dataset.preset];
    document
      .querySelectorAll("[data-preset]")
      .forEach(
        (b) => (
          b.classList.toggle("active", b === button),
          b.setAttribute("aria-pressed", String(b === button))
        ),
      );
    inspect();
  }),
);
inspect();

document
  .querySelectorAll("[data-preset]")
  .forEach((b) =>
    b.setAttribute("aria-pressed", String(b.classList.contains("active"))),
  );
