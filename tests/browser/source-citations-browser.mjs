import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { waitForDevToolsPort } from "./devtools-port.mjs";

const [, , chromium, origin, output] = process.argv;
assert.ok(chromium && origin && output);
const profile = await mkdtemp(path.join(os.tmpdir(), "source-citations-"));
const browser = spawn(chromium, [
  "--headless=new", "--no-sandbox", "--disable-background-networking",
  "--disable-component-update", "--disable-extensions", "--disable-gpu",
  "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--host-resolver-rules=MAP minoshealth.pl 127.0.0.1", "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume();
browser.stderr.resume();
let socket;
try {
  const port = await waitForDevToolsPort(browser, profile);
  let target;
  for (let attempt = 0; attempt < 100 && !target; attempt += 1) {
    target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item => item.type === "page");
    if (!target) await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.ok(target);
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let next = 1;
  const pending = new Map();
  const errors = [];
  const requests = [];
  let failBaw = false;
  function send(method, params = {}) {
    const id = next++;
    socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
      pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    });
  }
  socket.addEventListener("message", event => {
    const value = JSON.parse(event.data);
    if (value.id) {
      const handler = pending.get(value.id);
      if (handler) { pending.delete(value.id); value.error ? handler.reject(new Error(value.error.message)) : handler.resolve(value.result); }
    }
    if (value.method === "Runtime.exceptionThrown") errors.push(value.params.exceptionDetails.text);
    if (value.method === "Network.requestWillBeSent") requests.push(value.params.request.url);
    if (value.method === "Fetch.requestPaused") {
      const event = value.params;
      const operation = failBaw && event.request.url.endsWith("/api/v1/provenance/baw")
        ? send("Fetch.fulfillRequest", { requestId: event.requestId, responseCode: 503 })
        : send("Fetch.continueRequest", { requestId: event.requestId });
      operation.catch(error => errors.push(error.message));
    }
  });
  async function evaluate(expression) {
    const answer = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.equal(answer.exceptionDetails, undefined);
    return answer.result.value;
  }
  async function wait(expression) {
    for (let attempt = 0; attempt < 150; attempt += 1) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Page condition did not settle: ${expression}`);
  }
  async function navigate(route, width) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: 1000, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: origin + route });
    await wait(`location.pathname === ${JSON.stringify(route)} && document.readyState === "complete" && Boolean(document.querySelector("main"))`);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true, "horizontal overflow");
  }
  async function screenshot(name) {
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    await writeFile(path.join(output, name), Buffer.from(image.data, "base64"));
  }
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Emulation.setScriptExecutionDisabled", { value: true });
  await navigate("/zrodla", 1440);
  assert.ok(await evaluate('document.querySelectorAll("#source-fallback .source-card").length >= 3'));
  assert.equal(await evaluate('document.querySelector("#catalog-controls").hidden'), true);
  await screenshot("catalog-nojs-desktop.png");
  await navigate("/zrodla", 320);
  await screenshot("catalog-nojs-320.png");
  await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  const touch = await evaluate(`(() => { const link = document.querySelector('#source-fallback a[href="/zrodla/rsk/00CD10"]'); link.scrollIntoView({block:"center"}); const r = link.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2,height:r.height}; })()`);
  assert.ok(touch.height >= 44);
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: touch.x, y: touch.y }] });
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await wait('location.pathname === "/zrodla/rsk/00CD10" && document.readyState === "complete"');
  await navigate("/zrodla/rsk/00CD10", 390);
  const citation = await evaluate('Array.from(document.querySelectorAll("a")).find(a => a.textContent.includes("Stały adres")).pathname');
  assert.match(citation, /^\/zrodla\/rsk\/obserwacje\/[1-9][0-9]*-[0-9a-f]{64}\/00CD10$/);
  await navigate(citation, 390);
  assert.ok(await evaluate('document.body.textContent.includes("obserwacja historyczna")'));
  assert.equal(await evaluate('Boolean(document.querySelector("main img"))'), false, "source markup escaped");
  await screenshot("citation-nojs-390.png");
  await navigate("/zrodla/baw/43868", 1440);
  assert.ok(await evaluate(`Boolean(document.querySelector('a[href="/zrodla/baw/43943"]'))`));
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
  assert.equal(await evaluate('document.activeElement.tagName'), "A");
  await evaluate('document.documentElement.style.fontSize = "200%"');
  assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
  await screenshot("baw-nojs-zoom.png");
  await send("Emulation.setScriptExecutionDisabled", { value: false });
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/v1/provenance/*" }] });
  failBaw = true;
  await navigate("/zrodla", 390);
  await wait('document.querySelector("#load-state").classList.contains("is-error")');
  assert.equal(await evaluate('document.querySelector("[data-fallback-source=baw]").hidden'), false);
  assert.ok(await evaluate('document.querySelectorAll("#catalog-results .source-card").length > 0'));
  assert.ok(await evaluate(`Boolean(document.querySelector('#catalog-results a[href="/zrodla/nfz"]'))`));
  await screenshot("catalog-provider-failure-390.png");
  await evaluate('document.querySelector("#source-search").value = "private-canary"; document.querySelector("#source-search").dispatchEvent(new Event("input", { bubbles: true }))');
  assert.equal(await evaluate('document.querySelector("[data-fallback-source=baw] [data-fallback-note]").hidden'), false);
  failBaw = false;
  await evaluate('document.querySelector("#retry-button").click()');
  await wait('document.querySelector("#load-state").classList.contains("is-ready")');
  assert.equal(await evaluate('Array.from(document.querySelectorAll("[data-fallback-source]")).every(node => node.hidden)'), true);
  await evaluate('document.querySelector("#source-search").value = "private-canary"; document.querySelector("#source-search").dispatchEvent(new Event("input", { bubbles: true }))');
  assert.equal(requests.some(url => url.includes("private-canary")), false);
  assert.deepEqual(errors, []);
  const receipt = { no_js: true, widths: [1440, 390, 320], keyboard: true, touch: true, text_zoom: "200%", provider_failure_retains_ssr: true, retry_recovers: true, no_query_in_requests: true, errors };
  await writeFile(path.join(output, "browser.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt));
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await new Promise(resolve => browser.exitCode !== null ? resolve() : browser.once("exit", resolve));
  await rm(profile, { recursive: true, force: true });
}
