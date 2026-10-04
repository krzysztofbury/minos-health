import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , chromium, pageUrl, rskFixture, bawFixture, outputDirectory] = process.argv;
assert.ok(chromium && pageUrl && rskFixture && bawFixture && outputDirectory);

class CdpClient {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener("message", (event) => this.onMessage(event.data));
  }
  onMessage(raw) {
    const message = JSON.parse(raw);
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
      return;
    }
    for (const listener of this.listeners.get(message.method) || []) listener(message.params);
  }
  send(method, params = {}) {
    const id = this.nextId++;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(method, callback) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(callback);
    this.listeners.set(method, listeners);
  }
  once(method, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeout);
      this.on(method, (event) => { clearTimeout(timer); resolve(event); });
    });
  }
}

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

async function connect(profile, process) {
  let port;
  for (let attempt = 0; attempt < 1200; attempt++) {
    try { port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; break; }
    catch {
      if (process.exitCode !== null) throw new Error("Chromium exited before starting CDP");
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  assert.ok(port, "Chromium did not start CDP");
  const target = await pageTarget(port);
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  return new CdpClient(socket);
}

async function evaluate(client, expression) {
  const result = await client.send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function navigate(client, width, height, expected) {
  await client.send("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: width < 500,
  });
  const loaded = client.once("Page.loadEventFired");
  await client.send("Page.navigate", { url: pageUrl });
  await loaded;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(client, `document.getElementById("load-state").classList.contains("${expected}")`)) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Page did not reach ${expected}`);
}

async function screenshot(client, name) {
  const image = await client.send("Page.captureScreenshot", {
    captureBeyondViewport: true, format: "png", fromSurface: true,
  });
  await writeFile(path.join(outputDirectory, name), Buffer.from(image.data, "base64"));
}

const profile = await mkdtemp(path.join(os.tmpdir(), "minos-sources-browser-"));
const browser = spawn(chromium, [
  "--headless=new", "--no-sandbox", "--disable-background-networking",
  "--disable-component-update", "--disable-extensions", "--disable-gpu",
  "--disable-sync", "--hide-scrollbars",
  "--host-resolver-rules=MAP minoshealth.pl 127.0.0.1",
  "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });
// Drain both pipes immediately: a noisy runner must not block Chromium on write.
browser.stdout.resume();
let startupDiagnostics = Buffer.alloc(0);
browser.stderr.on("data", (chunk) => {
  startupDiagnostics = Buffer.concat([startupDiagnostics, chunk]).subarray(-8192);
});

try {
  let client;
  try {
    client = await connect(profile, browser);
  } catch (error) {
    // Only pre-navigation diagnostics are exposed, without local paths/controls.
    const safeDiagnostics = startupDiagnostics.toString("utf8").replace(/\/[\w./+-]+/g, "[path]").replace(/\p{C}/gu, " ");
    throw new Error(`${error.message}\nChromium startup diagnostics:\n${safeDiagnostics}`, { cause: error });
  }
  const errors = [];
  const requests = [];
  const requestBodies = [];
  let bawUnavailable = false;
  client.on("Runtime.consoleAPICalled", (event) => {
    if (event.type === "error") errors.push(event);
  });
  client.on("Runtime.exceptionThrown", (event) => errors.push(event));
  client.on("Network.requestWillBeSent", (event) => {
    requests.push(event.request.url);
    if (event.request.postData) requestBodies.push(event.request.postData);
  });
  client.on("Fetch.requestPaused", async (event) => {
    if (bawUnavailable && event.request.url.endsWith("/api/v1/provenance/baw")) {
      await client.send("Fetch.fulfillRequest", { requestId: event.requestId, responseCode: 503 });
    } else {
      await client.send("Fetch.continueRequest", { requestId: event.requestId });
    }
  });
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Network.enable");
  await client.send("Accessibility.enable");
  await client.send("Fetch.enable", {
    patterns: [{ requestStage: "Request", urlPattern: "*/api/v1/provenance/baw" }],
  });

  const rsk = JSON.parse(await readFile(rskFixture, "utf8"));
  const baw = JSON.parse(await readFile(bawFixture, "utf8"));
  assert.equal(rsk.source, "RSK");
  assert.equal(baw.source, "BAW");
  await navigate(client, 1440, 900, "is-ready");
  await evaluate(client, `document.getElementById('tab-rsk').focus()`);
  await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  assert.equal(await evaluate(client, `document.getElementById('tab-baw').getAttribute('aria-selected')`), "true");
  await evaluate(client, `document.querySelector('.act-history > summary').focus()`);
  await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
  await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
  assert.equal(await evaluate(client, `document.querySelector('.act-history').open`), true);
  await evaluate(client, `document.getElementById('tab-all').click()`);
  const desktop = await evaluate(client, `(() => ({
    cards: document.querySelectorAll(".source-card").length,
    hostname: document.location.hostname,
    textSafe: document.body.textContent.includes('<img src=x onerror="alert(1)">'),
    injectedImages: document.querySelectorAll("main img").length,
    noDownloadLinks: document.querySelectorAll("[download]").length === 0,
    collapsed: [...document.querySelectorAll(".source-card details")].every((node) => !node.open),
    overflow: document.documentElement.scrollWidth > window.innerWidth,
  }))()`);
  assert.deepEqual(desktop, {
    cards: 4, hostname: "minoshealth.pl", textSafe: true,
    injectedImages: 0, noDownloadLinks: true, collapsed: true, overflow: false,
  });
  assert.equal(await evaluate(client, `document.querySelector('a[href="https://baw.nfz.gov.pl/NFZ/document/44042/Zarzadzenie-46_2026_DSOZ"]') !== null`), true);
  const history = await evaluate(client, `(() => {
    const card = [...document.querySelectorAll('.source-card')].find(node => node.querySelector('h3').textContent === 'Zarządzenie 46/2026/DSOZ');
    card.querySelector('.act-history').open = true;
    return {
      acts: [...document.querySelectorAll('h3')].filter(node => node.textContent === 'Zarządzenie 46/2026/DSOZ').length,
      states: card.querySelectorAll('.act-state').length,
      variants: card.querySelectorAll('.act-variant').length,
      labels: [...card.querySelectorAll('.act-variant > summary')].map(node => node.textContent),
      latest: card.querySelector('.act-state h4').textContent,
    };
  })()`);
  assert.equal(history.acts, 1);
  assert.equal(history.states, 3);
  assert.equal(history.variants, 7);
  assert.match(history.latest, /102\/2026/);
  assert.equal(history.labels.filter(label => label === 'Porównanie zmian').length, 2);
  assert.equal(history.labels.filter(label => label === 'Skrócone porównanie zmian').length, 2);
  await evaluate(client, `document.querySelectorAll('.act-variant').forEach(node => node.open = true)`);
  await screenshot(client, "sources-history-desktop.png");
  await evaluate(client, `document.querySelectorAll('.source-card details').forEach(node => node.open = false)`);
  const rows = await evaluate(client, `(() => {
    const card = document.querySelector('.source-card');
    card.querySelector('details').open = true;
    const row = card.querySelector('.file-row');
    return { height: row.getBoundingClientRect().height, label: row.querySelector('.file-action').getAttribute('aria-label'), technicalOpen: card.querySelector('.technical-details').open };
  })()`);
  assert.ok(rows.height < 65, 'desktop file row is not compact');
  assert.match(rows.label, /Otwórz .* w RSK/);
  assert.equal(rows.technicalOpen, false);
  await screenshot(client, "sources-files-desktop.png");
  const tree = await client.send("Accessibility.getFullAXTree");
  assert.ok(tree.nodes.some((node) => node.role?.value === "tablist"));
  assert.ok(tree.nodes.some((node) => node.role?.value === "tabpanel"));
  await screenshot(client, "sources-desktop.png");

  await writeFile(bawFixture, JSON.stringify({
    ...baw, documents: baw.documents.map((doc) => ({
      ...doc, source_url: `https://baw.nfz.gov.pl/NFZ/document/${doc.document_id}`,
    })),
  }));
  await navigate(client, 1440, 900, "is-ready");
  assert.equal(await evaluate(client, `document.querySelector('a[href*="baw.nfz.gov.pl/NFZ/document/"]') === null`), true);
  const amendment = baw.documents.find(doc => doc.document_id === 43942);
  await writeFile(bawFixture, JSON.stringify({ ...baw, documents: [...baw.documents, {
    ...amendment, document_id: 50001, act_number: "60/2026/DSOZ", act_date: "2026-06-30",
    source_url: "https://baw.nfz.gov.pl/NFZ/document/50001/Zarzadzenie-60_2026_DSOZ",
    files: [{ ...amendment.files[0], file_id: 500010 }],
  }] }));
  await navigate(client, 1440, 900, "is-ready");
  assert.equal(await evaluate(client, `document.querySelector('.additional-amendments a').textContent.includes('60/2026/DSOZ')`), true);
  assert.equal(await evaluate(client, `document.querySelector('.additional-amendments').textContent.includes('nie ma osobnego tekstu')`), true);
  await writeFile(bawFixture, JSON.stringify({
    ...baw, schema_version: 1, documents: baw.documents.map(({ lineage: _lineage, ...doc }) => ({ ...doc, files: [] })),
  }));
  await navigate(client, 1440, 900, "is-ready");
  assert.equal(await evaluate(client, `document.querySelectorAll('.source-card').length`), 10);
  assert.equal(await evaluate(client, `document.querySelector('.act-history').textContent.includes('Starszy zapis')`), true);
  assert.equal(await evaluate(client, `document.body.textContent.includes('Minos nie ma kopii plików')`), true);
  await writeFile(bawFixture, JSON.stringify(baw));
  await navigate(client, 1440, 900, "is-ready");

  const search = await evaluate(client, `(() => {
    const input = document.getElementById("source-search");
    input.value = "NFZ";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const nfz = document.querySelectorAll(".source-card").length;
    document.getElementById("tab-rsk").click();
    const rskOnly = document.querySelectorAll(".source-card").length;
    document.getElementById("tab-all").click();
    input.value = "ICD10";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const icd = document.querySelectorAll(".source-card").length;
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return { nfz, rskOnly, icd, restored: document.querySelectorAll(".source-card").length };
  })()`);
  assert.deepEqual(search, { nfz: 3, rskOnly: 0, icd: 1, restored: 4 });

  const metadataSearch = await evaluate(client, `(() => {
    const input = document.getElementById('source-search');
    const query = text => { input.value = text; input.dispatchEvent(new Event('input', {bubbles:true})); };
    query('NFZ załącznik 9');
    const first = document.querySelector('.source-card h3').textContent;
    const evidence = document.querySelector('.match-evidence').textContent;
    query('9 zalacznik NFZ');
    const reversed = document.querySelector('.source-card h3').textContent;
    query('NFZ załącznik 99');
    const numericMiss = document.querySelectorAll('.source-card').length;
    query('zalacznikx');
    const correctedHits = document.querySelectorAll('.source-card').length;
    const suggestion = document.querySelector('.suggestion-button');
    const labelled = document.getElementById('search-suggestions').textContent;
    suggestion.click();
    const accepted = document.querySelectorAll('.source-card').length;
    const focused = document.activeElement.id;
    query('one two three four five six seven eight nine');
    const invalid = input.getAttribute('aria-invalid');
    document.getElementById('reset-search').click();
    return { first, evidence, reversed, numericMiss, correctedHits, labelled, accepted, focused,
      invalid, restored: document.querySelectorAll('.source-card').length,
      location: location.search + location.hash, persisted: localStorage.length + sessionStorage.length };
  })()`);
  assert.equal(metadataSearch.first, 'Zarządzenie 46/2026/DSOZ');
  assert.equal(metadataSearch.reversed, metadataSearch.first);
  assert.match(metadataSearch.evidence, /Załącznik 9.xlsx/);
  assert.match(metadataSearch.evidence, /Tekst po zmianach/);
  assert.match(metadataSearch.evidence, /23 lip 2026/);
  assert.equal(metadataSearch.numericMiss, 0);
  assert.equal(metadataSearch.correctedHits, 0);
  assert.match(metadataSearch.labelled, /Czy chodziło o/);
  assert.equal(metadataSearch.accepted, 1);
  assert.equal(metadataSearch.focused, 'source-search');
  assert.equal(metadataSearch.invalid, 'true');
  assert.equal(metadataSearch.restored, 4);
  assert.equal(metadataSearch.location, '');
  assert.equal(metadataSearch.persisted, 0);
  assert.equal(requests.concat(requestBodies).some(value => /zalacznikx|za%C5%82%C4%85cznik|NFZ%20za/i.test(value)), false);
  await evaluate(client, `(() => { const input = document.getElementById('source-search'); input.value = '9 zalacznik NFZ'; input.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  await screenshot(client, "sources-search-desktop.png");

  const manyEntries = Array.from({ length: 55 }, (_, index) => {
    const uuid = '00000000-0000-4000-8000-' + String(index + 1000).padStart(12, '0');
    return { ...rsk.entries[0], hcd: 'AA' + String(index).padStart(4, '0'), file_uuid: uuid,
      origin_url: 'https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/' + uuid,
      resource_name: 'Fixture catalog ' + index, resource_short_name: 'FIX' + index };
  });
  await writeFile(rskFixture, JSON.stringify({ ...rsk, entries: manyEntries }));
  await navigate(client, 1440, 900, "is-ready");
  assert.equal(await evaluate(client, `document.querySelectorAll('.source-card').length`), 50);
  assert.equal(await evaluate(client, `document.getElementById('result-count').textContent`), '58 zestawów');
  await evaluate(client, `document.getElementById('more-results').click()`);
  assert.equal(await evaluate(client, `document.querySelectorAll('.source-card').length`), 58);
  assert.equal(await evaluate(client, `document.getElementById('more-results').hidden`), true);
  await evaluate(client, `document.getElementById('tab-rsk').click()`);
  assert.equal(await evaluate(client, `document.querySelectorAll('.source-card').length`), 50);
  await writeFile(rskFixture, JSON.stringify(rsk));

  await navigate(client, 390, 844, "is-ready");
  const mobile = await evaluate(client, `(() => ({
    cards: document.querySelectorAll(".source-card").length,
    overflow: document.documentElement.scrollWidth > innerWidth,
    searchTop: document.getElementById("source-search").getBoundingClientRect().top,
    firstHeight: document.querySelector(".source-card").getBoundingClientRect().height,
  }))()`);
  assert.equal(mobile.cards, 4);
  assert.equal(mobile.overflow, false);
  assert.ok(mobile.searchTop < 844, `search starts below first viewport: ${mobile.searchTop}`);
  assert.ok(mobile.firstHeight < 450, `collapsed card is too tall: ${mobile.firstHeight}`);
  await screenshot(client, "sources-mobile.png");
  await evaluate(client, `(() => { const input = document.getElementById('source-search'); input.value = 'zalacznikx'; input.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "sources-suggestions-mobile.png");
  await evaluate(client, `document.querySelector('.suggestion-button').click()`);
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "sources-search-mobile.png");
  await evaluate(client, `document.getElementById('reset-search').click()`);
  await evaluate(client, `document.querySelectorAll('.source-card details:not(.technical-details)').forEach(node => node.open = true)`);
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "sources-expanded-mobile.png");
  await client.send("Emulation.setDeviceMetricsOverride", { width: 720, height: 500, deviceScaleFactor: 2, mobile: false });
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "sources-zoom.png");

  await navigate(client, 320, 568, "is-ready");
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "sources-mobile-320.png");

  bawUnavailable = true;
  await evaluate(client, `document.getElementById("retry-button").click()`);
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(client, `document.getElementById("load-state").classList.contains("is-error")`)) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const retry = await evaluate(client, `(() => ({
    cards: document.querySelectorAll(".source-card").length,
    cached: document.getElementById("source-summary").textContent.includes("może być nieaktualny"),
    named: document.getElementById("load-state").textContent.includes("BAW"),
  }))()`);
  assert.deepEqual(retry, { cards: 4, cached: true, named: true });
  await screenshot(client, "sources-retry-failure.png");
  bawUnavailable = false;
  await evaluate(client, `document.getElementById("retry-button").click()`);
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(client, `document.getElementById("load-state").classList.contains("is-ready")`)) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.equal(await evaluate(client, `document.getElementById("source-summary").textContent.includes("może być nieaktualny")`), false);
  assert.equal(await evaluate(client, `document.querySelectorAll(".source-card").length`), 4);
  bawUnavailable = true;
  await navigate(client, 390, 844, "is-error");
  assert.equal(await evaluate(client, `document.querySelectorAll(".source-card").length`), 1);
  assert.match(await evaluate(client, `document.getElementById("load-state").textContent`), /BAW/);
  assert.equal(errors.length, 0, "browser console errors were recorded");
  assert.equal(requests.some((url) => /(?:amazonaws\.com|s3[.-]|\/api\/v1\/artifacts)/i.test(url)), false);
  assert.equal(requests.some((url) => url.includes("/api/rsk/file/get/")), false);

  const homeLoaded = client.once("Page.loadEventFired");
  await client.send("Page.navigate", { url: new URL("/", pageUrl).href });
  await homeLoaded;
  const home = await evaluate(client, `(() => ({
    sources: Boolean(document.querySelector('.site-nav a[href="/zrodla"]')),
    value: document.querySelector("h1")?.textContent,
    disabled: document.getElementById("query")?.disabled,
    overflow: document.documentElement.scrollWidth > innerWidth,
  }))()`);
  assert.equal(home.sources, true);
  assert.match(home.value, /Znajdź rozpoznanie, procedurę lub grupę JGP/);
  assert.equal(home.disabled, true);
  assert.equal(home.overflow, false);
  await screenshot(client, "home-mobile.png");
  await client.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "home-desktop.png");
} finally {
  browser.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => browser.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  if (browser.exitCode === null && browser.signalCode === null) browser.kill("SIGKILL");
  await rm(profile, { force: true, recursive: true, maxRetries: 10, retryDelay: 200 });
}
