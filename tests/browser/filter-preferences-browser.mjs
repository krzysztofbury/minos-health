import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output] = process.argv;
assert.ok(executable && url && output, "Usage: node filter-preferences-browser.mjs CHROMIUM URL OUTPUT");
const origin = new URL(url);
assert.ok(["http:", "https:"].includes(origin.protocol));
const local = origin.protocol === "http:";
assert.ok(!local || ["minoshealth.pl", "localhost", "127.0.0.1"].includes(origin.hostname), "HTTP fault injection requires a local test host");
const home = new URL("/", url).href;
const key = "minos.search.filters.v1";
const order = ["icd10-pl", "icd9-pl", "jgp"];
const ttl_ms = 90 * 24 * 60 * 60 * 1000;
const profile = await mkdtemp(path.join(os.tmpdir(), "fp-"));
const sleep = (duration_ms) => new Promise(resolve => setTimeout(resolve, duration_ms));
const requests = [], errors = [];
let browser, socket, send, evaluate;

async function stopBrowser() {
  if (!browser) return;
  if (socket?.readyState === WebSocket.OPEN) {
    // A graceful close flushes the owned profile before the restart assertion.
    try { await send("Browser.close"); } catch { /* Chromium may close CDP first. */ }
    socket.close();
  }
  const exited = () => browser.exitCode !== null || browser.signalCode !== null;
  for (let attempt = 0; !exited() && attempt < 60; attempt++) await sleep(50);
  if (!exited()) browser.kill("SIGTERM");
  for (let attempt = 0; !exited() && attempt < 60; attempt++) await sleep(50);
  if (!exited()) browser.kill("SIGKILL");
  for (let attempt = 0; !exited() && attempt < 60; attempt++) await sleep(50);
  assert.ok(exited(), "Owned Chromium must exit before deleting its profile");
  browser = undefined;
  socket = undefined;
}

async function startBrowser() {
  await rm(path.join(profile, "DevToolsActivePort"), { force: true });
  const host = local ? ["--host-resolver-rules=MAP minoshealth.pl 127.0.0.1"] : [];
  browser = spawn(executable, [...host, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
  let startupError;
  browser.once("error", error => { startupError = error; });
  browser.stdout.resume(); browser.stderr.resume();
  let target;
  const startup_deadline_ms = Date.now() + 60000;
  for (let attempt = 0; attempt < 1200 && Date.now() < startup_deadline_ms; attempt++) {
    try {
      const port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0];
      const reply = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(1000) });
      target = (await reply.json()).find(candidate => candidate.type === "page");
      if (target) break;
    } catch { /* The port file can precede the page target. */ }
    if (startupError) throw startupError;
    assert.equal(browser.exitCode, null, "Chromium startup failed");
    assert.equal(browser.signalCode, null, "Chromium startup was interrupted");
    await sleep(50);
  }
  assert.ok(target, "Chromium startup deadline exceeded");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("CDP connection deadline exceeded")), 5000);
    socket.addEventListener("open", () => { clearTimeout(timer); resolve(); }, { once: true });
    socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("CDP connection failed")); }, { once: true });
  });
  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id);
      if (!callback) return;
      pending.delete(message.id); clearTimeout(callback.timer);
      if (message.error) callback.reject(new Error(`CDP: ${message.error.message}`)); else callback.resolve(message.result);
    } else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request);
    else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
  });
  socket.addEventListener("close", () => {
    for (const callback of pending.values()) { clearTimeout(callback.timer); callback.reject(new Error("CDP closed")); }
    pending.clear();
  });
  send = (method, params = {}) => new Promise((resolve, reject) => {
    const value = ++id;
    const timer = setTimeout(() => { pending.delete(value); reject(new Error(`CDP deadline: ${method}`)); }, 20000);
    pending.set(value, { resolve, reject, timer });
    socket.send(JSON.stringify({ id: value, method, params }));
  });
  evaluate = async expression => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!result.exceptionDetails, "Browser evaluation failed");
    return result.result.value;
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
}

async function wait(expression) {
  const deadline_ms = Date.now() + 15000;
  for (let attempt = 0; attempt < 300 && Date.now() < deadline_ms; attempt++) {
    if (await evaluate(expression)) return;
    await sleep(50);
  }
  throw new Error(`Filter acceptance deadline: ${expression}`);
}
async function openHome(reload = false) {
  // Do not let an old ready DOM satisfy a new navigation's readiness condition.
  if (reload) {
    await evaluate("document.querySelector('#query').disabled = true");
    await send("Page.reload", { ignoreCache: true });
  } else {
    await send("Page.navigate", { url: "about:blank" });
    await wait("location.href === 'about:blank' && !document.querySelector('#query')");
    await send("Page.navigate", { url: home });
  }
  await wait("document.querySelector('#query') && !document.querySelector('#query').disabled && !document.querySelector('#filter-preferences').hidden && !document.querySelector('#reset-filters').disabled");
}
const selected = () => evaluate("[...document.querySelectorAll('input[name=system]:checked')].map(box => box.value)");
const rawPreference = () => evaluate(`localStorage.getItem(${JSON.stringify(key)})`);
const clickFilter = system => evaluate(`document.querySelector('input[value="${system}"]').click()`);
const searchRequests = () => requests.filter(request => new URL(request.url).pathname === "/api/v1/search");
async function storedSystems(expected) {
  assert.deepEqual(await evaluate("Object.keys(localStorage)"), [key]);
  const raw = await rawPreference();
  assert.ok(raw.length <= 256);
  const value = JSON.parse(raw);
  assert.deepEqual(Object.keys(value).sort(), ["expires_at_ms", "systems", "version"]);
  assert.equal(value.version, 1);
  assert.deepEqual(value.systems, expected);
  assert.deepEqual(value.systems, order.filter(system => value.systems.includes(system)), "Systems must be ordered, unique and allowlisted");
  const now_ms = await evaluate("Date.now()");
  assert.ok(Number.isSafeInteger(value.expires_at_ms));
  assert.ok(value.expires_at_ms > now_ms && value.expires_at_ms <= now_ms + ttl_ms);
  return raw;
}
async function press(keyName, keyCode) {
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: keyName, code: keyName, windowsVirtualKeyCode: keyCode, text: keyName === "Enter" ? "\r" : "" });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: keyName, code: keyName, windowsVirtualKeyCode: keyCode });
}
async function submitTyped(query, systems) {
  await evaluate("document.querySelector('#query').value = ''; document.querySelector('#query').focus()");
  await send("Input.insertText", { text: query });
  await wait("!document.querySelector('#suggestions').hidden");
  await press("Escape", 27);
  const before = searchRequests().length;
  await press("Enter", 13);
  await wait(`document.querySelectorAll('.result-group').length === ${systems.length} && [...document.querySelectorAll('.result-group')].every(node => ['ready', 'uncovered'].includes(node.dataset.searchState))`);
  assert.deepEqual(searchRequests().slice(before).map(request => JSON.parse(request.postData).system).sort(), [...systems].sort(), "Typed submission must naturally fan out once per source");
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.result-group')].map(node => node.getAttribute('aria-labelledby').replace('group-', ''))"), systems);
}
async function snapshot(name) {
  await evaluate("document.fonts.ready.then(() => true)");
  const layout = await evaluate("({width: document.documentElement.scrollWidth, viewport: innerWidth})");
  assert.ok(layout.width <= layout.viewport, `${name}: ${JSON.stringify(layout)}`);
  const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(path.join(output, `${name}.png`), Buffer.from(image.data, "base64"));
}
async function reset() {
  await evaluate("document.querySelector('#reset-filters').focus()");
  await press("Enter", 13);
  assert.deepEqual(await selected(), order);
  assert.equal(await rawPreference(), null);
}

async function checkMetadata() {
  const metadata = await evaluate(`({title: document.title, description: document.querySelector('meta[name=description]').content,
    og: document.querySelector('meta[property="og:image"]').content, twitter: document.querySelector('meta[name="twitter:image"]').content,
    width: document.querySelector('meta[property="og:image:width"]').content, height: document.querySelector('meta[property="og:image:height"]').content,
    card: document.querySelector('meta[name="twitter:card"]').content,
    preferences: document.querySelector('#filter-preferences').textContent,
    privacy: document.querySelector('footer a[href="/polityka-prywatnosci"]').getAttribute('href')})`);
  for (const text of [metadata.title, metadata.description]) for (const source of ["ICD-10", "ICD-9", "JGP"]) assert.ok(text.includes(source));
  assert.equal(metadata.og, "https://minoshealth.pl/social-preview-v1.png");
  assert.equal(metadata.twitter, metadata.og);
  assert.equal(metadata.width, "1200"); assert.equal(metadata.height, "630");
  assert.equal(metadata.card, "summary_large_image");
  for (const text of ["localStorage", "90 dni", "Nie zapisujemy wyszukiwań", "Szczegóły prywatności"]) assert.ok(!metadata.preferences.includes(text), "No inline storage notice");
  assert.ok(metadata.preferences.includes("Przywróć wszystkie filtry"));
  assert.equal(metadata.privacy, "/polityka-prywatnosci");
  // Probe this stack's route, not the canonical public host when testing locally.
  const raster = await evaluate(`(async () => {
    const result = [];
    for (const method of ['GET', 'HEAD']) {
      const response = await fetch('/social-preview-v1.png', {method, credentials:'omit'});
      const bytes = new Uint8Array(await response.arrayBuffer());
      result.push({method, status:response.status, type:response.headers.get('Content-Type'), length:bytes.length,
        signature:Array.from(bytes.slice(0,8)), width:bytes.length >= 24 ? new DataView(bytes.buffer).getUint32(16) : null,
        height:bytes.length >= 24 ? new DataView(bytes.buffer).getUint32(20) : null});
    }
    return result;
  })()`);
  for (const response of raster) { assert.equal(response.status, 200); assert.equal(response.type, "image/png"); }
  assert.deepEqual(raster[0].signature, [137, 80, 78, 71, 13, 10, 26, 10]);
  assert.equal(raster[0].width, 1200); assert.equal(raster[0].height, 630);
  assert.equal(raster[1].length, 0);
  await send("Page.navigate", { url: new URL(metadata.privacy, home).href });
  await wait("location.pathname === '/polityka-prywatnosci' && document.querySelector('article')");
  const policy = await evaluate("document.querySelector('article').textContent.replace(/\\s+/g, ' ')");
  for (const text of [key, "automatycznie", "90 dni", "nie przedłuża", "usuwa tylko ten wpis", "Nie zawiera treści wyszukiwań", "nie stanowi samo w sobie wyjątku"]) assert.ok(policy.includes(text), `Privacy policy missing: ${text}`);
  await openHome();
}

async function invalidRecords() {
  const now_ms = await evaluate("Date.now()");
  const valid = { version: 1, systems: ["icd9-pl"], expires_at_ms: now_ms + 3600000 };
  const cases = [
    ["malformed JSON", "{"], ["oversized JSON", JSON.stringify(valid) + " ".repeat(257)],
    ["null", "null"], ["array", "[]"], ["missing field", JSON.stringify({ version: 1, systems: [] })],
    ["version", JSON.stringify({ ...valid, version: 2 })], ["version type", JSON.stringify({ ...valid, version: "1" })],
    ["systems type", JSON.stringify({ ...valid, systems: "icd9-pl" })],
    ["duplicate", JSON.stringify({ ...valid, systems: ["icd9-pl", "icd9-pl"] })],
    ["unordered", JSON.stringify({ ...valid, systems: ["jgp", "icd10-pl"] })],
    ["unknown", JSON.stringify({ ...valid, systems: ["icd11"] })],
    ["extra field", JSON.stringify({ ...valid, query: "must not be stored" })],
    ...["1", null, true, {}, [], now_ms + 1000.5, now_ms - 1, now_ms + ttl_ms + 60000].map(expires_at_ms => ["invalid expiry", JSON.stringify({ ...valid, expires_at_ms })]),
  ];
  for (const [name, raw] of cases) {
    await evaluate(`localStorage.setItem(${JSON.stringify(key)}, ${JSON.stringify(raw)})`);
    await openHome(true);
    assert.deepEqual(await selected(), order, name);
    assert.equal(await rawPreference(), null, `${name} must be rejected and deleted`);
  }
}

async function blockedStorage() {
  for (const mode of ["getter", "getItem", "setItem", "removeItem"]) {
    await reset();
    if (["getter", "getItem", "removeItem"].includes(mode)) await clickFilter("icd9-pl");
    const before = await rawPreference();
    const source = `window.__blockedCalls = 0;
      const deny = function() { window.__blockedCalls++; throw new DOMException('blocked', 'SecurityError'); };
      ${mode === "getter" ? "Object.defineProperty(window, 'localStorage', {configurable:true, get:deny});" : `Storage.prototype.${mode} = deny;`}`;
    const script = await send("Page.addScriptToEvaluateOnNewDocument", { source });
    try {
      await openHome(true);
      if (mode === "getter" || mode === "getItem") assert.deepEqual(await selected(), order);
      if (mode === "setItem") {
        await clickFilter("icd9-pl");
        assert.deepEqual(await selected(), ["icd10-pl", "jgp"]);
        assert.ok(await evaluate("document.querySelector('#filter-preferences-status').textContent.includes('Nie udało się zapamiętać')"));
        assert.equal(await rawPreference(), null);
      }
      if (mode === "removeItem") {
        await evaluate("document.querySelector('#reset-filters').click()");
        assert.deepEqual(await selected(), order);
        assert.equal(await rawPreference(), before);
        assert.ok(await evaluate("document.querySelector('#filter-preferences-status').textContent.includes('Nie udało się usunąć')"));
      }
      assert.ok(await evaluate("window.__blockedCalls > 0"), `${mode} guard was not exercised`);
      // Working search is the important fallback, not just lack of exceptions.
      await submitTyped("E11", await selected());
    } finally {
      await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: script.identifier });
      await openHome(true);
      if (mode === "getter" || mode === "getItem") assert.equal(await rawPreference(), before, "A failed read must not delete an otherwise valid preference");
      await reset();
    }
  }
}

async function missingAvailability() {
  const script = await send("Page.addScriptToEvaluateOnNewDocument", { source: `const originalFetch = window.fetch;
    window.fetch = async function(url, options) {
      const response = await originalFetch(url, options);
      if (url !== '/api/v1/releases') return response;
      const body = await response.json(); body.jgp_search = false;
      body.releases = body.releases.filter(release => release.system !== 'jgp');
      return new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}});
    };` });
  try {
    await openHome(true);
    assert.deepEqual(await selected(), ["icd10-pl", "icd9-pl"]);
    assert.equal(await evaluate("document.querySelector('input[value=jgp]').disabled"), true);
    assert.equal(await rawPreference(), null, "Availability alone must not write user intent");
    await clickFilter("icd9-pl");
    await storedSystems(["icd10-pl", "jgp"]);
    // Only an unavailable preferred source is left: no effective selection.
    await clickFilter("icd10-pl");
    await storedSystems(["jgp"]);
    assert.deepEqual(await selected(), []);
    const count = searchRequests().length;
    await evaluate("document.querySelector('#query').value='badania moczu'; document.querySelector('#search-form').requestSubmit()");
    await wait("document.querySelector('#results-status').textContent.includes('Zaznacz co najmniej')");
    await sleep(500);
    assert.equal(searchRequests().length, count);
  } finally {
    await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: script.identifier });
    await openHome(true);
  }
  assert.deepEqual(await selected(), ["jgp"], "Returning availability must restore the preserved intent");
  await storedSystems(["jgp"]);
  await reset();
}

async function cancelledTransport() {
  await evaluate(`window.__originalFetch = window.fetch; window.__slowCalls = 0; window.__slowDone = 0; window.__slowAborted = 0;
    window.fetch = async function(url, options) {
      if (url !== '/api/v1/search') return window.__originalFetch(url, options);
      window.__slowCalls++;
      await new Promise(resolve => setTimeout(resolve, 800));
      window.__slowDone++;
      if (options.signal.aborted) window.__slowAborted++;
      // Deliberately non-cooperative transport: stale completion must not render.
      return new Response(JSON.stringify({results:[{code:'STALE',label:'stale suggestion',href:'/jgp/2026-10-05/STALE'}],truncated:false}), {status:200});
    };`);
  try {
    await evaluate("document.querySelector('#query').focus()");
    await send("Input.insertText", { text: "badania moczu" });
    await wait("window.__slowCalls === 3");
    await clickFilter("icd9-pl");
    await wait("window.__slowDone === 3");
    assert.equal(await evaluate("window.__slowAborted"), 3);
    assert.equal(await evaluate("document.querySelector('#suggestions').hidden"), true);
    assert.equal(await evaluate("document.querySelector('#query').getAttribute('aria-expanded')"), "false");
    assert.equal(await evaluate("document.querySelectorAll('#suggestions [role=option]').length"), 0);
    await evaluate("window.__slowCalls=0; window.__slowDone=0; window.__slowAborted=0; document.querySelector('#search-form').requestSubmit()");
    await wait("window.__slowCalls === 2");
    await clickFilter("icd10-pl"); await clickFilter("jgp");
    await wait("document.querySelector('#results-status').textContent.includes('Zaznacz co najmniej')");
    const calls = await evaluate("window.__slowCalls");
    await evaluate("document.querySelector('#search-form').requestSubmit()");
    await wait("window.__slowDone === window.__slowCalls");
    await sleep(100);
    assert.equal(await evaluate("window.__slowCalls"), calls, "Empty selection must not POST after cancelling a pending search");
    assert.equal(await evaluate("document.querySelectorAll('.result-group').length"), 0);
    assert.ok(await evaluate("document.querySelector('#results-status').textContent.includes('Zaznacz co najmniej')"), "Cancelled completion must not replace empty-selection validation");
    assert.ok(await evaluate("window.__slowAborted >= 2"));
  } finally {
    await evaluate("window.fetch=window.__originalFetch");
    await openHome(); await reset();
  }
}

try {
  await startBrowser();
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await openHome();
    const available = await evaluate("fetch('/api/v1/releases').then(response => response.json()).then(body => ['icd10-pl','icd9-pl','jgp'].filter(system => system === 'jgp' ? body.jgp_search : body.releases.some(release => release.system === system)))");
    assert.deepEqual(available, order, "This suite requires the packaged ICD-10, ICD-9 and JGP stack");
    assert.deepEqual(await selected(), available);
    assert.equal(await rawPreference(), null);
    assert.equal(await evaluate("document.querySelectorAll('.filter-soon[aria-disabled=true]').length"), 1);
    await submitTyped("badania moczu", available);
    assert.equal(await rawPreference(), null, "Search and suggestions must not persist defaults or queries");
    await snapshot(`filters-${name}`);
    await checkMetadata();
    await clickFilter("icd9-pl");
    const saved = await storedSystems(["icd10-pl", "jgp"]);
    await openHome(true);
    assert.deepEqual(await selected(), ["icd10-pl", "jgp"]);
    assert.equal(await rawPreference(), saved, "Reload must not refresh expiry");
    await stopBrowser(); await startBrowser(); await openHome();
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    assert.deepEqual(await selected(), ["icd10-pl", "jgp"], "Preferences must survive an actual Chromium restart in the same owned profile");
    assert.equal(await rawPreference(), saved);
    await submitTyped("E11", ["icd10-pl", "jgp"]);
    assert.equal(await rawPreference(), saved, "Searching must not refresh expiry");
    await openHome();
    await clickFilter("icd10-pl"); await clickFilter("jgp");
    await storedSystems([]);
    await openHome(true); assert.deepEqual(await selected(), []);
    const before = searchRequests().length;
    await evaluate("document.querySelector('#query').focus()");
    await send("Input.insertText", { text: "badania moczu" }); await press("Enter", 13);
    await wait("document.querySelector('#results-status').textContent.includes('Zaznacz co najmniej')");
    await sleep(500);
    assert.equal(searchRequests().length, before, "Empty stored selection must validate without POST");
    if (local) await evaluate("localStorage.setItem('test.unrelated', 'keep')");
    await reset();
    await wait("[...document.querySelectorAll('.result-group')].length === 3 && [...document.querySelectorAll('.result-group')].every(node => node.dataset.searchState !== 'pending')");
    assert.equal(await rawPreference(), null, "Reset's search must not recreate preferences");
    if (local) {
      assert.equal(await evaluate("localStorage.getItem('test.unrelated')"), "keep");
      await evaluate("localStorage.removeItem('test.unrelated')");
    }
    await openHome(true); assert.equal(await rawPreference(), null);
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    // Text-only 200% zoom: change computed font sizes, not device pixels or box widths.
    const font_px = await evaluate("parseFloat(getComputedStyle(document.querySelector('#reset-filters')).fontSize)");
    await evaluate("const sizes = [...document.querySelectorAll('body, body *')].map(node => [node, getComputedStyle(node).fontSize]); sizes.forEach(([node,size]) => node.style.fontSize = (parseFloat(size) * 2) + 'px')");
    assert.equal(await evaluate("parseFloat(getComputedStyle(document.querySelector('#reset-filters')).fontSize)"), font_px * 2);
    await snapshot(`filters-${name}-text-200`);
    await openHome();
  }
  if (local) { await invalidRecords(); await blockedStorage(); await missingAvailability(); await cancelledTransport(); }
  assert.deepEqual(errors, []);
  for (const request of searchRequests()) {
    assert.equal(request.method, "POST");
    assert.ok(request.postData);
    const body = JSON.parse(request.postData);
    assert.ok(order.includes(body.system));
    if (body.system !== "jgp") assert.ok(body.release_id);
  }
  assert.ok(requests.every(request => !new URL(request.url).search));
  assert.ok(requests.every(request => !/badania|moczu|E11/.test(request.url)));
  assert.deepEqual(await evaluate("Object.keys(localStorage)"), []);
  assert.equal(await evaluate("sessionStorage.length"), 0);
  assert.equal(await evaluate("document.cookie"), "");
  assert.deepEqual((await send("Network.getAllCookies")).cookies, []);
  console.log(`Filter browser passed: defaults, typed three-source fanout, metadata/raster/privacy, real changes, reload/restart, fixed expiry, empty selection, keyboard reset, text zoom, query privacy${local ? ', invalid records, blocked storage, missing availability and cancellation' : ' (no fault injection)'}.`);
} finally {
  await stopBrowser();
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
