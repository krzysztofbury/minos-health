import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output] = process.argv;
assert.ok(executable && url && output);
const profile = await mkdtemp(path.join(os.tmpdir(), "minos-search-browser-"));
const args = ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"];
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
  // Same 60 s startup window as sources-browser.mjs; slow CI runners need >10 s.
  for (let i = 0; i < 1200; i++) {
    try { port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; break; }
    catch { if (browser.exitCode !== null) throw new Error("browser startup failed"); await new Promise((resolve) => setTimeout(resolve, 50)); }
  }
  assert.ok(port);
  socket = new WebSocket((await pageTarget(port)).webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let id = 0;
  const pending = new Map();
  const requests = [];
  const errors = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id); pending.delete(message.id);
      if (message.error) callback.reject(new Error("browser protocol failed")); else callback.resolve(message.result);
    } else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request);
    else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const value = ++id; pending.set(value, { resolve, reject }); socket.send(JSON.stringify({ id: value, method, params })); });
  const evaluate = async (expression) => {
    const value = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!value.exceptionDetails, "browser evaluation failed");
    return value.result.value;
  };
  const wait = async (expression) => {
    for (let i = 0; i < 200; i++) { if (await evaluate(expression)) return; await new Promise((resolve) => setTimeout(resolve, 50)); }
    throw new Error("browser acceptance condition timed out");
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  const home = new URL("/", url).href;
  const overflow = () => evaluate("document.documentElement.scrollWidth > window.innerWidth");
  const shoot = async (name) => {
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(image.data, "base64"));
  };
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: home });
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled && !document.querySelector(\'input[value="icd9-pl"]\').disabled');
    assert.equal(await evaluate('document.querySelector(\'input[value="icd10-pl"]\').checked'), true);
    assert.equal(await evaluate("document.querySelectorAll('.filter-soon').length"), 1); // ICD-11 only; JGP has its own section since #26
    await evaluate('document.querySelector(\'input[value="icd9-pl"]\').click()');
    await evaluate('document.querySelector("#query").focus(); document.querySelector("#query").value="badania moczu"; document.querySelector("#query").dispatchEvent(new Event("input",{bubbles:true}))');
    await wait('document.querySelectorAll("#suggestions .suggestion").length > 0');
    assert.equal(await evaluate('document.querySelector("#query").getAttribute("aria-expanded")'), "true");
    await evaluate('document.querySelector("#query").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true}))');
    assert.equal(await evaluate('document.querySelector("#query").getAttribute("aria-activedescendant")'), "suggestion-0");
    await evaluate('document.getElementById("search-form").requestSubmit()');
    await wait('document.querySelectorAll(".result-group").length === 2 && ![...document.querySelectorAll(".result-group-head p")].some((node) => node.textContent.startsWith("Szukam"))');
    assert.equal(await evaluate('document.querySelectorAll("#suggestions .suggestion").length'), 0);
    const icd9 = await evaluate('[...document.querySelectorAll(".result-group")][1].querySelector(".result")?.getAttribute("href")');
    assert.ok(icd9 && /^\/slowniki\/icd-9\/[A-Z0-9.]+(?:\/jgp\/20\d\d-\d\d-\d\d)?$/.test(icd9), "unexpected ICD-9 result link");
    assert.equal(await overflow(), false);
    await shoot(name);
    await send("Page.navigate", { url: new URL("/slowniki/icd-10#kod=E11.0", url).href });
    await wait('document.querySelector(\'[role="treeitem"][aria-selected="true"]\')?.dataset.code === "E11.0" && document.querySelector("#detail h2")');
    assert.deepEqual(await evaluate('[...document.querySelectorAll("#detail .detail-path button")].map((node) => node.textContent)'), ["E00-E99", "E11"]);
    assert.equal(await evaluate('document.querySelector("#detail").textContent.includes("Przykład opisowy")'), true);
    await evaluate('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowUp",bubbles:true}))');
    assert.equal(await evaluate('document.activeElement.dataset.code'), "E11");
    assert.equal(await overflow(), false);
    await shoot(`${name}-icd10`);
    await send("Page.navigate", { url: new URL("/slowniki/icd-9", url).href });
    await wait('document.querySelectorAll(\'#tree > [role="treeitem"]\').length === 2 && !document.querySelector("#unplaced").hidden');
    assert.equal(await evaluate('document.querySelector("#unplaced-list").textContent.includes("00.00001")'), true);
    assert.equal(await overflow(), false);
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.some((request) => request.url.endsWith("/api/v1/search") && request.method === "POST"));
  assert.ok(requests.some((request) => /\/api\/v1\/tree\/[a-z0-9-]+\/[0-9]+$/.test(request.url)));
  assert.ok(requests.every((request) => !request.url.includes("badania") && !request.url.includes("moczu")));
  assert.ok(requests.filter((request) => request.url.endsWith("/api/v1/search")).every((request) => request.postData && JSON.parse(request.postData).release_id));
  assert.equal(await evaluate('Boolean(localStorage.length || sessionStorage.length || document.cookie)'), false);
  console.log("Real browser acceptance passed: desktop/mobile search, suggestions, dictionary tree, keyboard, no query URLs or storage.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  if (browser.exitCode === null && browser.signalCode === null) browser.kill("SIGKILL");
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
