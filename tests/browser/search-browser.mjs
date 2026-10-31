import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { waitForDevToolsPort } from "./devtools-port.mjs";

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
  const port = await waitForDevToolsPort(browser, profile);
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
    const available = await evaluate(`fetch('/api/v1/releases').then(response => response.json()).then(body =>
      ['icd10-pl', 'icd9-pl', 'jgp'].filter(system => system === 'jgp' ? body.jgp_search : body.releases.some(release => release.system === system)))`);
    assert.deepEqual(await evaluate('[...document.querySelectorAll("input[name=system]:checked")].map(box => box.value)'), available);
    assert.equal(await evaluate('document.querySelector("input[value=jgp]").disabled'), !available.includes('jgp'), 'The local dictionary fixture need not include JGP');
    assert.equal(await evaluate('document.querySelector("input[value=icd11-pl]").checked'), false, "ICD-11 PL starts unselected (#53)");
    await evaluate('document.querySelector("#query").focus(); document.querySelector("#query").value="badania moczu"; document.querySelector("#query").dispatchEvent(new Event("input",{bubbles:true}))');
    await wait('document.querySelectorAll("#suggestions .suggestion").length > 0');
    assert.equal(await evaluate('document.querySelector("#query").getAttribute("aria-expanded")'), "true");
    await evaluate('document.querySelector("#query").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true}))');
    assert.equal(await evaluate('document.querySelector("#query").getAttribute("aria-activedescendant")'), "suggestion-0");
    await evaluate('document.getElementById("search-form").requestSubmit()');
    await wait(`document.querySelectorAll(".result-group").length === ${available.length} && [...document.querySelectorAll(".result-group")].every(node => node.dataset.searchState === 'ready' || node.dataset.searchState === 'uncovered')`);
    assert.equal(await evaluate('document.querySelectorAll("#suggestions .suggestion").length'), 0);
    const icd9 = await evaluate('[...document.querySelectorAll(".result-group")][1].querySelector(".result")?.getAttribute("href")');
    assert.ok(icd9 && /^\/slowniki\/icd-9\/[A-Z0-9.]+(?:\/jgp\/20\d\d-\d\d-\d\d)?$/.test(icd9), "unexpected ICD-9 result link");
    assert.equal(await overflow(), false);
    // A section folds from its header and comes back with its results.
    await evaluate('document.querySelector(".result-toggle").click()');
    assert.equal(await evaluate('document.querySelector(".result-toggle").getAttribute("aria-expanded")'), "false");
    assert.equal(await evaluate('document.getElementById(document.querySelector(".result-toggle").getAttribute("aria-controls")).hidden'), true);
    await evaluate('document.querySelector(".result-toggle").click()');
    assert.equal(await evaluate('document.getElementById(document.querySelector(".result-toggle").getAttribute("aria-controls")).hidden'), false);
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
  assert.ok(requests.filter((request) => request.url.endsWith("/api/v1/search")).every((request) => request.postData && (JSON.parse(request.postData).system === 'jgp' || JSON.parse(request.postData).release_id)));
  assert.equal(await evaluate('localStorage.getItem("minos.search.filters.v1")'), null, 'Reading defaults and searching must not create preferences');
  assert.equal(await evaluate('Boolean(localStorage.length || sessionStorage.length || document.cookie)'), false);
  // A copied command is the command alone, without the button's label.
  await send("Page.addScriptToEvaluateOnNewDocument", { source: 'Object.defineProperty(navigator, "clipboard", { value: { writeText: (text) => { window.copied = text; return Promise.resolve(); } } });' });
  await send("Page.navigate", { url: new URL("/dla-agentow", url).href });
  await wait('document.querySelector(".copy-button")');
  assert.equal(await evaluate('document.querySelector(".copy-button").click(); window.copied'), await evaluate('document.querySelector(".copy-button").parentElement.querySelector("code").textContent'));
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
