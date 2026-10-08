import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { waitForDevToolsPort } from "./devtools-port.mjs";

const [, , executable, url, output, bodyFile, expected] = process.argv;
assert.ok(executable && url && output && bodyFile && expected);
const body = JSON.parse(await readFile(bodyFile, "utf8"));
const profile = await mkdtemp(path.join(os.tmpdir(), "minos-grouping-browser-"));
const browser = spawn(executable, ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", "--host-resolver-rules=MAP minoshealth.pl 127.0.0.1", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  const port = await waitForDevToolsPort(browser, profile);
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page");
    if (!target) await new Promise(r => setTimeout(r, 50));
  }
  assert.ok(target);
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let id = 0, held = null, hold = false;
  const pending = new Map(), errors = [], requests = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) { const cb = pending.get(message.id); pending.delete(message.id); if (message.error) cb.reject(new Error(message.error.message)); else cb.resolve(message.result); }
    else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
    else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
    else if (message.method === "Fetch.requestPaused") {
      if (hold) { held = message.params.requestId; hold = false; }
      else void send("Fetch.continueRequest", { requestId: message.params.requestId });
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; pending.set(n, { resolve, reject }); socket.send(JSON.stringify({ id: n, method, params })); });
  const evaluate = async expression => { const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }); assert.ok(!r.exceptionDetails, JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const wait = async expression => { for (let i = 0; i < 400; i++) { if (await evaluate(expression)) return; await new Promise(r => setTimeout(r, 50)); } throw new Error("Grouping browser timed out: " + expression); };
  const shot = async (name, full = true) => { const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full }); await writeFile(path.join(output, name + ".png"), Buffer.from(image.data, "base64")); };
  const set = async (selector, value) => evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.value=${JSON.stringify(value)}; e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/v1/group", requestStage: "Response" }] });
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: new URL("/gruper", url).href });
    await wait("document.readyState === 'complete' && document.querySelector('#group-fields') && !document.querySelector('#group-fields').disabled && document.fonts.status === 'loaded'");
    await shot("grouping-empty-" + name);
    const e = body.encounter;
    for (const [id, val] of Object.entries({ admitted: e.admitted_on, discharged: e.discharged_on, age: String(e.age_completed_years), "service-date": body.service_date, "reporting-month": body.reporting_month.slice(0, 7) })) await set("#" + id, val);
    await set("#wards input[type=text]", e.stays[0].department);
    await set("#wards input[type=date]", e.stays[0].admitted_on);
    await set("#wards label:last-child input", e.stays[0].discharged_on);
    await set("#principal input", "E11.0");
    await evaluate("document.querySelector('#principal input').focus()");
    await wait("document.querySelector('#principal [role=option]')");
    assert.ok(await evaluate("document.querySelector('#principal [role=option]').textContent.length > 8"));
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowDown" });
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter" });
    await set("#principal input", e.principal_diagnosis.code);
    await set("#principal select", "episode");
    await evaluate("document.getElementById('add-procedure').click()");
    await set("#procedures input[type=text]", e.procedures[0].code);
    await set("#procedures input[type=date]", e.procedures[0].performed_on);
    await evaluate("const s=document.querySelector('#procedures select'); s.selectedIndex=1; s.dispatchEvent(new Event('change',{bubbles:true}))");
    await set("#product-report", "empty");
    await wait(`document.querySelector('[data-group="${expected}"].group-valid')`);
    assert.equal(await evaluate("location.pathname + location.search"), "/gruper");
    assert.equal(await evaluate("localStorage.length + sessionStorage.length"), 0);
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
    await evaluate("document.activeElement.blur(); window.scrollTo(0,0)");
    await shot("grouping-result-" + name);
    await evaluate(`document.querySelector('[data-group="${expected}"] summary').click()`);
    assert.ok(await evaluate(`document.querySelector('[data-group="${expected}"] details').textContent.includes('wiersze')`));
    await evaluate("document.querySelector('.group-results').scrollIntoView()");
    await shot("grouping-trace-" + name, false);

    // Hold a completed response, edit the input, and release the obsolete result.
    hold = true;
    await set("#age", String(e.age_completed_years + 1));
    assert.equal(await evaluate("document.querySelectorAll('.group-card').length"), 0);
    for (let i = 0; i < 200 && !held; i++) await new Promise(r => setTimeout(r, 50));
    assert.ok(held);
    await set("#age", "");
    await send("Fetch.continueRequest", { requestId: held }).catch(() => {}); held = null;
    await new Promise(r => setTimeout(r, 600));
    assert.equal(await evaluate("document.querySelectorAll('.group-card').length"), 0);
    await set("#age", String(e.age_completed_years));
    await set("#product-report", "present");
    await wait("document.querySelector('#group-status').textContent.includes('Wyłączenia produktów')");
    assert.equal(await evaluate("document.querySelectorAll('.group-card').length"), 0);
    await evaluate("document.getElementById('clear-case').click()");
    assert.equal(await evaluate("document.querySelector('#principal input').value"), "");
  }
  assert.deepEqual(errors, []);
  // Chromium records the date input's built-in data: calendar icon as a request.
  // It never leaves the browser. Every actual network request stays same-origin.
  assert.deepEqual(requests.filter(r => new URL(r).protocol !== "data:" && (new URL(r).origin !== new URL(url).origin || new URL(r).search)), []);
  console.log("Grouping browser passed: desktop/mobile, keyboard code search, trace, stale response refusal, product refusal, clear and storage privacy.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) {
    browser.kill("SIGKILL");
    await new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); });
  }
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
