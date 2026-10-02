import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output, code = "A01", expectedRank = "4"] = process.argv;
assert.ok(executable && url && output);
const profile = await mkdtemp(path.join(os.tmpdir(), "minos-rank-browser-"));
const args = ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"];
// Local containers serve plain HTTP under the production hostname; the public
// origin is HTTPS-only, so an http: target can never reach production.
if (new URL(url).protocol === "http:") args.unshift("--host-resolver-rules=MAP minoshealth.pl 127.0.0.1");
const browser = spawn(executable, args, { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
// DevToolsActivePort can appear before the initial about:blank page target.
async function pageTarget(port) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const target = targets.find((candidate) => candidate.type === "page");
    if (target) return target;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("Chromium did not expose a page target");
}

let socket;
try {
  let port;
  for (let i = 0; i < 200; i++) {
    try { port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; break; }
    catch { if (browser.exitCode !== null) throw new Error("browser startup failed"); await new Promise(resolve => setTimeout(resolve, 50)); }
  }
  assert.ok(port);
  socket = new WebSocket((await pageTarget(port)).webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let id = 0;
  const pending = new Map(), errors = [], requests = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id); pending.delete(message.id);
      if (message.error) callback.reject(new Error("browser protocol failed")); else callback.resolve(message.result);
    } else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
    else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const value = ++id; pending.set(value, { resolve, reject }); socket.send(JSON.stringify({ id: value, method, params })); });
  const evaluate = async expression => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!result.exceptionDetails);
    return result.result.value;
  };
  const wait = async expression => {
    for (let i = 0; i < 200; i++) { if (await evaluate(expression)) return; await new Promise(resolve => setTimeout(resolve, 50)); }
    throw new Error("rank browser condition timed out");
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: new URL(`/slowniki/icd-9/${code}`, url).href });
    await wait(`location.pathname === '/slowniki/icd-9/${code}' && document.querySelector('.procedure-rank') && document.fonts.status === 'loaded'`);
    const section = await evaluate("document.querySelector('.procedure-rank').textContent");
    assert.ok(section.includes(`Ranga ${expectedRank}`));
    assert.ok(section.includes("JGP (SZP)"));
    assert.ok(section.includes("nie dobór rulesetu dla daty świadczenia"));
    assert.ok(section.includes("SHA-256 wykazu"));
    assert.ok(section.includes("Pierwszy okres sprawozdawczy wersji"));
    const api = await evaluate(`(async () => {
      const releases = await (await fetch('/api/v1/releases')).json();
      const selected = releases.releases.find(item => item.system === 'icd9-pl' && item.origin === 'czs');
      const response = await fetch('/api/v1/procedure-ranks/' + selected.release_id + '/${code}');
      return { status: response.status, answer: await response.json() };
    })()`);
    assert.equal(api.status, 200);
    assert.equal(api.answer.evidence.rank, Number(expectedRank));
    assert.equal(api.answer.evidence.ruleset_binding, "not_bound");
    assert.equal(api.answer.evidence.grouper_type, "SZP");
    assert.equal(await evaluate("document.documentElement.scrollWidth > window.innerWidth"), false);
    assert.ok(await evaluate("getComputedStyle(document.body).fontFamily.includes('Atkinson')"));
    assert.ok(await evaluate("document.querySelector('.site-header .brand') && document.querySelector('.site-footer') && document.querySelector('.breadcrumbs')"));
    assert.equal(await evaluate("document.querySelector('.code-actions a').getAttribute('href')"), `/slowniki/icd-9#kod=${code}`);
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `rank-${name}.png`), Buffer.from(image.data, "base64"));
    await send("Page.navigate", { url: new URL("/slowniki/icd-9/A", url).href });
    await wait("location.pathname === '/slowniki/icd-9/A' && document.querySelector('.procedure-rank')");
    assert.ok(await evaluate("document.querySelector('.procedure-rank').textContent.includes('Nie oznacza to rangi 0')"));
    await send("Page.navigate", { url: new URL("/slowniki/icd-10/E11.0", url).href });
    await wait("location.pathname === '/slowniki/icd-10/E11.0' && document.querySelector('.detail-code') && document.querySelector('h1')");
    assert.equal(await evaluate("Boolean(document.querySelector('.procedure-rank'))"), false);
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.every(request => !request.includes("?")));
  console.log("Rank browser acceptance passed: desktop/mobile, source evidence, explicit unpaired state, absent rank not zero, #102 layout/fonts/navigation preserved.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) {
    browser.kill("SIGKILL");
    await new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); });
  }
  await rm(profile, { recursive: true, force: true });
}
