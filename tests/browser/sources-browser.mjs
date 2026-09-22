import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { gzipSync } from "node:zlib";

const [, , chromium, pageUrl, fixturePath, outputDirectory] = process.argv;
assert.ok(chromium && pageUrl && fixturePath && outputDirectory);

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
    const listeners = this.listeners.get(message.method) || [];
    for (const listener of listeners) listener(message.params);
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  once(method, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeoutMs);
      this.on(method, (params) => {
        clearTimeout(timer);
        resolve(params);
      });
    });
  }
}

async function waitForFile(filePath, process, readStderr) {
  for (let attempt = 0; attempt < 600; attempt += 1) {
    try {
      return await readFile(filePath, "utf8");
    } catch {
      if (process.exitCode !== null) {
        throw new Error(
          `Chromium exited with ${process.exitCode} before publishing a DevTools port: ${readStderr()}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`Chromium did not publish a DevTools port within 30 seconds: ${readStderr()}`);
}

async function connectCdp(profileDirectory, process, readStderr) {
  const lines = (
    await waitForFile(
      path.join(profileDirectory, "DevToolsActivePort"),
      process,
      readStderr,
    )
  ).trim().split("\n");
  const targets = await (await fetch(`http://127.0.0.1:${lines[0]}/json/list`)).json();
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
    awaitPromise: true,
    expression,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}

async function waitForState(client, className) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const ready = await evaluate(
      client,
      `document.getElementById("load-state")?.classList.contains(${JSON.stringify(className)}) === true`,
    );
    if (ready) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const state = await evaluate(client, `(() => {
    const node = document.getElementById("load-state");
    return node ? { className: node.className, text: node.textContent } : null;
  })()`);
  throw new Error(`Page did not enter ${className}: ${JSON.stringify(state)}`);
}

async function navigateLoaded(client, width, height) {
  await client.send("Emulation.setDeviceMetricsOverride", {
    deviceScaleFactor: 1,
    height,
    mobile: width < 500,
    width,
  });
  const loaded = client.once("Page.loadEventFired");
  await client.send("Page.navigate", { url: pageUrl });
  await loaded;
}

async function navigate(client, width, height, expectedState) {
  await navigateLoaded(client, width, height);
  await waitForState(client, expectedState);
}

async function screenshot(client, fileName) {
  const capture = await client.send("Page.captureScreenshot", {
    captureBeyondViewport: true,
    format: "png",
    fromSurface: true,
  });
  await writeFile(path.join(outputDirectory, fileName), Buffer.from(capture.data, "base64"));
}

const profileDirectory = await mkdtemp(path.join(os.tmpdir(), "minos-sources-chromium-"));
let chromiumStderr = "";
const chromiumProcess = spawn(chromium, [
  "--headless=new",
  "--no-sandbox",
  "--disable-background-networking",
  "--disable-component-update",
  "--disable-default-apps",
  "--disable-extensions",
  "--disable-gpu",
  "--disable-sync",
  "--hide-scrollbars",
  "--host-resolver-rules=MAP minoshealth.pl 127.0.0.1",
  "--no-first-run",
  "--remote-debugging-port=0",
  `--user-data-dir=${profileDirectory}`,
  "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });

chromiumProcess.stderr.setEncoding("utf8");
chromiumProcess.stderr.on("data", (chunk) => { chromiumStderr += chunk; });

try {
  const client = await connectCdp(
    profileDirectory,
    chromiumProcess,
    () => chromiumStderr,
  );
  const consoleErrors = [];
  const exceptions = [];
  const requests = [];
  const responseHeaders = [];
  let responseMode = "origin";
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const maximumBody = JSON.stringify({
    ...fixture,
    entries: Array.from({ length: 8192 }, () => fixture.entries[1]),
  });
  assert.ok(Buffer.byteLength(maximumBody) > 6 * 1024 * 1024);
  assert.ok(Buffer.byteLength(maximumBody) <= 8 * 1024 * 1024);
  const maximumCompressedBody = gzipSync(maximumBody);
  assert.ok(maximumCompressedBody.byteLength < Buffer.byteLength(maximumBody));

  client.on("Runtime.consoleAPICalled", (event) => {
    if (event.type === "error" || event.type === "assert") consoleErrors.push(event);
  });
  client.on("Runtime.exceptionThrown", (event) => exceptions.push(event));
  client.on("Network.requestWillBeSent", (event) => requests.push(event.request.url));
  client.on("Network.responseReceived", (event) => {
    if (event.response.url.endsWith("/api/v1/provenance/rsk")) {
      responseHeaders.push(event.response.headers);
    }
  });
  client.on("Fetch.requestPaused", async (event) => {
    if (event.request.url.startsWith("https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/")) {
      await client.send("Fetch.fulfillRequest", {
        body: "",
        requestId: event.requestId,
        responseCode: 404,
      });
      return;
    }
    if (responseMode === "origin") {
      await client.send("Fetch.continueRequest", { requestId: event.requestId });
      return;
    }
    let body = Buffer.from("{}");
    let responseCode = 200;
    const headers = [{ name: "Content-Type", value: "application/json" }];
    if (responseMode === "empty") body = Buffer.from(JSON.stringify({ ...fixture, entries: [] }));
    if (responseMode === "malformed") body = Buffer.from(JSON.stringify({ schema_version: 1, source: "RSK" }));
    if (responseMode === "unavailable") responseCode = 503;
    await client.send("Fetch.fulfillRequest", {
      body: body.toString("base64"),
      requestId: event.requestId,
      responseCode,
      responseHeaders: headers,
    });
  });

  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Network.enable");
  await client.send("Accessibility.enable");
  await client.send("Fetch.enable", {
    patterns: [
      { requestStage: "Request", urlPattern: "*/api/v1/provenance/rsk" },
      { requestStage: "Request", urlPattern: "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/*" },
    ],
  });

  await client.send("Network.setBlockedURLs", { urls: ["*/sources.js"] });
  await navigateLoaded(client, 390, 844);
  assert.equal(
    await evaluate(client, `document.getElementById("load-state").classList.contains("is-unavailable")`),
    true,
  );
  await client.send("Network.setBlockedURLs", { urls: [] });

  await navigate(client, 1440, 1100, "is-ready");
  const desktop = await evaluate(client, `(() => ({
    cards: getComputedStyle(document.querySelector("tbody tr")).display,
    hostileText: document.body.textContent.includes('<img src=x onerror="alert(1)">'),
    injectedImages: document.querySelectorAll("main img").length,
    labeledCells: document.querySelectorAll("td[aria-label]").length,
    noDownloadAttributes: document.querySelectorAll("[download]").length === 0,
    noMinosDownloadLinks: [...document.querySelectorAll("a")].every((link) => !link.textContent.toLocaleLowerCase("pl-PL").includes("pobierz z minos")),
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    rows: document.querySelectorAll("tbody tr").length,
    table: getComputedStyle(document.querySelector("table")).display,
  }))()`);
  assert.equal(desktop.table, "table");
  assert.equal(desktop.cards, "table-row");
  assert.equal(desktop.overflow, false);
  assert.equal(desktop.hostileText, true);
  assert.equal(desktop.injectedImages, 0);
  assert.equal(desktop.labeledCells, 0);
  assert.equal(desktop.noDownloadAttributes, true);
  assert.equal(desktop.noMinosDownloadLinks, true);
  assert.equal(desktop.rows, 3);
  const unavailableOrigins = fixture.entries
    .filter((entry) => entry.status !== "current")
    .map((entry) => entry.origin_url);
  assert.equal(
    unavailableOrigins.some((originUrl) => requests.includes(originUrl)),
    false,
    "rendering historical provenance requested an unavailable upstream file",
  );
  const accessibility = await client.send("Accessibility.getFullAXTree");
  assert.equal(accessibility.nodes.some((node) => node.role?.value === "table"), true);
  assert.equal(
    accessibility.nodes.filter((node) => node.role?.value === "columnheader").length,
    5,
  );
  await screenshot(client, "sources-desktop.png");

  const debounce = await evaluate(client, `(async () => {
    const input = document.getElementById("source-search");
    input.value = "Zasób 1";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const immediate = document.querySelectorAll("tbody tr").length;
    await new Promise((resolve) => setTimeout(resolve, 75));
    const beforeDelay = document.querySelectorAll("tbody tr").length;
    await new Promise((resolve) => setTimeout(resolve, 150));
    const afterDelay = document.querySelectorAll("tbody tr").length;
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 200));
    return { afterDelay, beforeDelay, immediate };
  })()`);
  assert.deepEqual(debounce, { afterDelay: 1, beforeDelay: 3, immediate: 3 });

  await navigate(client, 390, 844, "is-ready");
  const mobile = await evaluate(client, `(() => ({
    card: getComputedStyle(document.querySelector("tbody tr")).display,
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    rowWidth: document.querySelector("tbody tr").getBoundingClientRect().width,
    viewport: window.innerWidth,
  }))()`);
  assert.equal(mobile.card, "grid");
  assert.equal(mobile.overflow, false);
  assert.ok(mobile.rowWidth <= mobile.viewport);
  await screenshot(client, "sources-mobile.png");

  await navigate(client, 320, 568, "is-ready");
  const narrow = await evaluate(client, `(() => ({
    overflow: document.documentElement.scrollWidth > window.innerWidth,
    rowWidth: document.querySelector("tbody tr").getBoundingClientRect().width,
    viewport: window.innerWidth,
  }))()`);
  assert.equal(narrow.overflow, false);
  assert.ok(narrow.rowWidth <= narrow.viewport);
  await screenshot(client, "sources-mobile-320.png");

  await writeFile(fixturePath, maximumBody);
  await writeFile(`${fixturePath}.gz`, maximumCompressedBody);
  await navigate(client, 1440, 1100, "is-ready");
  const maximum = await evaluate(client, `(() => ({
    count: document.getElementById("result-count").textContent,
    nodeCount: document.querySelectorAll("*").length,
    page: document.getElementById("page-status").textContent,
    renderDurationMs: Number(document.getElementById("timeline").dataset.renderDurationMs),
    rows: document.querySelectorAll("tbody tr").length,
  }))()`);
  assert.equal(maximum.rows, 75);
  assert.match(maximum.count, /8192 wyników, pokazano 1-75/);
  assert.equal(maximum.page, "Strona 1 z 110");
  assert.ok(maximum.nodeCount < 5000, `unexpected DOM node count: ${maximum.nodeCount}`);
  assert.ok(maximum.renderDurationMs < 3000, `slow bounded render: ${maximum.renderDurationMs} ms`);
  assert.equal(
    responseHeaders.some((headers) => String(headers["Content-Encoding"] || headers["content-encoding"]).toLowerCase() === "gzip"),
    true,
  );
  assert.equal(
    responseHeaders.some((headers) => Number(headers["Content-Length"] || headers["content-length"]) === maximumCompressedBody.byteLength),
    true,
  );
  await evaluate(client, `document.getElementById("next-page").click()`);
  assert.equal(await evaluate(client, `document.getElementById("page-status").textContent`), "Strona 2 z 110");
  assert.equal(await evaluate(client, `document.querySelectorAll("tbody tr").length`), 75);

  await navigate(client, 390, 844, "is-ready");
  assert.equal(await evaluate(client, `document.documentElement.scrollWidth > window.innerWidth`), false);
  assert.equal(await evaluate(client, `document.querySelectorAll("tbody tr").length`), 75);

  responseMode = "empty";
  await navigate(client, 390, 844, "is-ready");
  assert.equal(await evaluate(client, `document.getElementById("empty-state").hidden`), false);

  responseMode = "malformed";
  await navigate(client, 390, 844, "is-error");
  assert.match(
    await evaluate(client, `document.getElementById("load-state").textContent`),
    /nieprawidłowy format/,
  );

  responseMode = "unavailable";
  await navigate(client, 390, 844, "is-error");
  assert.match(
    await evaluate(client, `document.getElementById("load-state").textContent`),
    /niedostępny/,
  );

  assert.equal(consoleErrors.length, 0, "browser console errors were recorded");
  assert.equal(exceptions.length, 0, "uncaught browser exceptions were recorded");
  assert.equal(requests.some((url) => url.includes("/api/v1/artifacts")), false);
  assert.equal(requests.some((url) => /(?:amazonaws\.com|s3[.-])/i.test(url)), false);
} finally {
  chromiumProcess.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => chromiumProcess.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 3000)),
  ]);
  if (chromiumProcess.exitCode === null) chromiumProcess.kill("SIGKILL");
  await rm(profileDirectory, { force: true, recursive: true });
}

if (/:CONSOLE:|Uncaught/i.test(chromiumStderr)) {
  throw new Error(`Chromium reported a console error: ${chromiumStderr}`);
}
