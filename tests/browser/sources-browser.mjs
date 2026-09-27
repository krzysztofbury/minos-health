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
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find((candidate) => candidate.type === "page");
  assert.ok(target);
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

try {
  const client = await connect(profile, browser);
  const errors = [];
  const requests = [];
  let bawUnavailable = false;
  client.on("Runtime.consoleAPICalled", (event) => {
    if (event.type === "error") errors.push(event);
  });
  client.on("Runtime.exceptionThrown", (event) => errors.push(event));
  client.on("Network.requestWillBeSent", (event) => requests.push(event.request.url));
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
    cards: 2, hostname: "minoshealth.pl", textSafe: true,
    injectedImages: 0, noDownloadLinks: true, collapsed: true, overflow: false,
  });
  const tree = await client.send("Accessibility.getFullAXTree");
  assert.ok(tree.nodes.some((node) => node.role?.value === "tablist"));
  assert.ok(tree.nodes.some((node) => node.role?.value === "tabpanel"));
  await screenshot(client, "sources-desktop.png");

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
  assert.deepEqual(search, { nfz: 1, rskOnly: 0, icd: 1, restored: 2 });

  await navigate(client, 390, 844, "is-ready");
  const mobile = await evaluate(client, `(() => ({
    cards: document.querySelectorAll(".source-card").length,
    overflow: document.documentElement.scrollWidth > innerWidth,
    searchTop: document.getElementById("source-search").getBoundingClientRect().top,
    firstHeight: document.querySelector(".source-card").getBoundingClientRect().height,
  }))()`);
  assert.equal(mobile.cards, 2);
  assert.equal(mobile.overflow, false);
  assert.ok(mobile.searchTop < 844, `search starts below first viewport: ${mobile.searchTop}`);
  assert.ok(mobile.firstHeight < 450, `collapsed card is too tall: ${mobile.firstHeight}`);
  await screenshot(client, "sources-mobile.png");

  await navigate(client, 320, 568, "is-ready");
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > innerWidth`), false);
  await screenshot(client, "sources-mobile-320.png");

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
    cta: document.querySelector(".hero-cta")?.getAttribute("href"),
    value: document.querySelector("h1")?.textContent,
    overflow: document.documentElement.scrollWidth > innerWidth,
  }))()`);
  assert.equal(home.cta, "/zrodla");
  assert.match(home.value, /Sprawdź dane/);
  assert.equal(home.overflow, false);
  await screenshot(client, "home-mobile.png");
} finally {
  browser.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => browser.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  if (browser.exitCode === null && browser.signalCode === null) browser.kill("SIGKILL");
  await rm(profile, { force: true, recursive: true, maxRetries: 10, retryDelay: 200 });
}
