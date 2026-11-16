import { chromium } from "@playwright/test";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { resolve, extname } from "node:path";
import assert from "node:assert/strict";
import { plan } from "../docs/model.js";
const root = resolve("docs");
const server = createServer(async (req, res) => {
  try {
    const path = resolve(
      root,
      "." +
        decodeURIComponent(
          new URL(req.url, "http://localhost").pathname.replace(
            /\/$/,
            "/index.html",
          ),
        ),
    );
    if (!path.startsWith(root + "/")) throw Error("path");
    res.setHeader(
      "Content-Type",
      {
        ".html": "text/html",
        ".js": "text/javascript",
        ".css": "text/css",
        ".svg": "image/svg+xml",
      }[extname(path)] || "application/octet-stream",
    );
    res.end(await readFile(path));
  } catch {
    res.writeHead(404);
    res.end("not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base =
  process.env.RILLSH_URL || `http://127.0.0.1:${server.address().port}/`;
const shots = resolve(process.env.RILLSH_SHOTS || "build/screenshots");
await mkdir(shots, { recursive: true });
const browser = await chromium.launch({ headless: true });
let checks = 0;
const errors = [],
  requests = [],
  failed = [];
function ok(v, m) {
  assert.ok(v, m);
  checks++;
}
async function verify(page) {
  const source = await page.locator("#source").inputValue();
  const env = {
    NAME: await page.locator("#name").inputValue(),
    OUT: await page.locator("#out").inputValue(),
  };
  const status = Number(await page.locator("#status").inputValue()),
    result = plan(source, env, status, 4242);
  ok((await page.locator("#state").innerText()) === "PARSE COMPLETE", "parsed");
  ok(
    (await page.locator("#token-count").innerText()) ===
      String(result.ast.tokens.length),
    "token count",
  );
  ok(
    (await page.locator("#pipe-count").innerText()) ===
      String(result.pipelines.length),
    "pipeline count",
  );
  const stages = result.pipelines.flatMap((p) => p.stages);
  ok((await page.locator(".stage").count()) === stages.length, "stage count");
  for (const [i, stage] of stages.entries()) {
    const card = page.locator(".stage").nth(i);
    ok(
      JSON.stringify(await card.locator(".argv code").allTextContents()) ===
        JSON.stringify(stage.argv.map((v) => JSON.stringify(v))),
      "arguments exact",
    );
    const fds = await card
      .locator(":scope > .fd > span:last-child")
      .allTextContents();
    ok(JSON.stringify(fds) === JSON.stringify(stage.fds), "descriptors exact");
    for (const details of await card.locator("details").all())
      await details.locator("summary").click();
    for (const [j, step] of stage.steps.entries()) {
      const box = card.locator(".step").nth(j);
      ok(
        JSON.stringify(
          await box.locator(".fd > span:last-child").allTextContents(),
        ) === JSON.stringify(step.fds),
        "ordered snapshot exact",
      );
    }
  }
  return result;
}
try {
  for (const width of [1440, 1024, 768, 390, 320]) {
    const page = await browser.newPage({
      viewport: { width, height: 1080 },
      deviceScaleFactor: 1,
    });
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    page.on("request", (r) => requests.push(r.url()));
    page.on("requestfailed", (r) => failed.push(r.url()));
    await page.goto(base);
    await page.keyboard.press("Tab");
    ok(
      await page
        .locator(".skip-link")
        .evaluate((el) => el === document.activeElement),
      "skip link first keyboard target",
    );
    ok(
      await page
        .locator(".skip-link")
        .evaluate((el) => el.getBoundingClientRect().top >= 0),
      "focused skip link visible",
    );
    if (width === 1440)
      await page.screenshot({ path: resolve(shots, "skip-link.png") });
    await page.keyboard.press("Enter");
    ok(
      new URL(page.url()).hash === "#workbench",
      "skip link reaches workbench",
    );
    await page.waitForSelector(".stage");
    await verify(page);
    for (const id of ["quotes", "order", "lists"]) {
      await page.locator(`[data-preset="${id}"]`).click();
      await verify(page);
      ok(
        (await page
          .locator(`[data-preset="${id}"]`)
          .getAttribute("aria-pressed")) === "true",
        "preset selected",
      );
    }
    await page.locator("#source").fill('printf "%s" "$NAME"');
    ok((await page.locator(".stage").count()) === 0, "edit clears stale plan");
    ok(
      (await page.locator("#state").innerText()) === "INPUT CHANGED",
      "dirty state",
    );
    await page.locator("#name").fill("<img src=x onerror=alert(1)> | fake");
    await page.locator("#inspect").click();
    await verify(page);
    ok((await page.locator("img").count()) === 0, "no HTML insertion");
    await page.locator("#source").fill("echo $(bad)");
    await page.locator("#inspect").click();
    ok(
      (await page.locator("#state").innerText()) === "INVALID INPUT",
      "syntax error",
    );
    ok((await page.locator(".stage").count()) === 0, "invalid no stale");
    await page.locator("#source").fill('echo "$NAME"');
    await page.locator("#status").fill("256");
    await page.locator("#inspect").click();
    ok(
      (await page.locator("#error").innerText()).includes("0 to 255"),
      "status bounds",
    );
    await page.locator("#status").fill("7");
    await page.locator("#name").fill("Ada Lovelace");
    await page.locator("#source").fill("echo $constructor $toString");
    await page.locator("#inspect").click();
    await verify(page);
    ok(
      (await page.locator(".argv code").allTextContents()).join("|") ===
        '"echo"|""|""',
      "prototype not environment",
    );
    await page.locator("#source").fill("");
    await page.locator("#inspect").click();
    await verify(page);
    ok((await page.locator(".stage").count()) === 0, "empty program");
    await page.locator("#status").fill("0");
    await page.locator('[data-preset="pipeline"]').click();
    await verify(page);
    ok(
      await page.locator("body").evaluate((el) => el.scrollWidth <= innerWidth),
      "no overflow " + width,
    );
    for (const el of await page.locator("button,summary,input").all()) {
      const box = await el.boundingBox();
      if (box) ok(box.height >= 44, "44px control");
    }
    ok(
      await page
        .locator("h1")
        .evaluate(
          (el) =>
            el.getBoundingClientRect().height /
              parseFloat(getComputedStyle(el).lineHeight) <=
            3.1,
        ),
      "heading max3lines",
    );
    const contrast = await page.evaluate(() => {
      const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number);
      const lum = (c) =>
        c
          .slice(0, 3)
          .map((v) => v / 255)
          .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
          .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
      const failures = [];
      for (const el of document.querySelectorAll("body *")) {
        if (
          ![...el.childNodes].some(
            (n) => n.nodeType === 3 && n.textContent.trim(),
          ) ||
          !el.getClientRects().length
        )
          continue;
        const s = getComputedStyle(el);
        if (s.visibility === "hidden") continue;
        let p = el,
          bg;
        while (p) {
          bg = rgb(getComputedStyle(p).backgroundColor);
          if (bg.length === 3 || bg[3] > 0) break;
          p = p.parentElement;
        }
        if (!p) bg = [255, 255, 255];
        const a = lum(rgb(s.color)),
          b = lum(bg),
          ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        const large =
          parseFloat(s.fontSize) >= 24 ||
          (parseFloat(s.fontSize) >= 18.66 && Number(s.fontWeight) >= 700);
        if (ratio < (large ? 3 : 4.5))
          failures.push({
            id: el.id,
            tag: el.tagName,
            text: el.textContent.slice(0, 40),
            color: s.color,
            ratio,
          });
      }
      return failures;
    });
    ok(!contrast.length, "text contrast " + JSON.stringify(contrast));
    await page.evaluate(() => {
      document.activeElement.blur();
      scrollTo({ top: 0, left: 0, behavior: "instant" });
    });
    await page.screenshot({
      path: resolve(shots, `hero-${width}.png`),
      fullPage: true,
    });
    await page.screenshot({ path: resolve(shots, `top-${width}.png`) });
    if (width === 1440) {
      await page.locator('[data-preset="order"]').click();
      await verify(page);
      await page.locator("#plans").scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(shots, "order.png") });
      await page.locator("#source").fill("echo $(nope)");
      await page.locator("#inspect").click();
      await page.locator("#workbench").scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(shots, "error.png") });
    }
    await page.close();
  }
  ok(!errors.length, "no browser errors " + errors.join(";"));
  ok(!failed.length, "no failed requests " + failed.join(";"));
  ok(
    requests.every((u) => new URL(u).origin === new URL(base).origin),
    "origin only",
  );
  console.log(
    JSON.stringify({
      status: "PASS",
      checks,
      widths: [1440, 1024, 768, 390, 320],
      errors,
      failed,
      requests: [...new Set(requests)],
    }),
  );
} finally {
  await browser.close();
  await new Promise((r) => server.close(r));
}
