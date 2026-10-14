// Read-only acceptance sweep over every public page family (#80).
// Usage: node site-audit-browser.mjs CHROMIUM ORIGIN OUTPUT_DIR
// Loads each page sequentially, so it is safe against production: no fault
// injection, no typed queries and no concurrency.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { waitForDevToolsPort } from "./devtools-port.mjs";

const [, , chromium, origin, output] = process.argv;
assert.ok(chromium && origin && output);
const host = new URL(origin).host;
// One representative route per server-rendered family, plus the two
// JavaScript applications and an expected 404.
const PAGES = [
  ["/", 200], ["/slowniki", 200], ["/slowniki/icd-10", 200],
  ["/slowniki/icd-10/A00", 200], ["/slowniki/icd-10/A00/jgp/2026-07-01", 200],
  ["/slowniki/icd-9", 200], ["/slowniki/icd-9/G47", 200],
  ["/jgp/2026-07-01", 200], ["/jgp/2026-07-01/A01", 200],
  ["/zrodla", 200], ["/zrodla/rsk/00CD10", 200], ["/zrodla/baw/43718", 200],
  ["/zrodla/nfz", 200], ["/gruper", 200],
  ["/polityka-prywatnosci", 200], ["/regulamin", 200],
  ["/slowniki/icd-10/ZZZ", 404],
];
// Text the visitor must never see: Cloudflare email obfuscation output.
const FORBIDDEN_TEXT = ["[email protected]", "[email protected]"];
const ALLOWED_SCRIPT_HOSTS = new Set([host, "plausible.io"]);

await mkdir(output, { recursive: true });
const profile = await mkdtemp(path.join(os.tmpdir(), "site-audit-"));
const browser = spawn(chromium, [
  "--headless=new", "--no-sandbox", "--disable-background-networking",
  "--disable-component-update", "--disable-extensions", "--disable-gpu",
  "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume();
browser.stderr.resume();
let socket;
const failures = [];
const results = [];
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
  let errors = [];
  let scripts = [];
  let documentStatus = 0;
  let pageUrl = "";
  function send(method, params = {}) {
    const id = next++;
    socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 30000);
      pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
    });
  }
  socket.addEventListener("message", event => {
    const value = JSON.parse(event.data);
    if (value.id) {
      const handler = pending.get(value.id);
      if (handler) { pending.delete(value.id); value.error ? handler.reject(new Error(value.error.message)) : handler.resolve(value.result); }
      return;
    }
    if (value.method === "Runtime.exceptionThrown") errors.push(value.params.exceptionDetails.exception?.description || value.params.exceptionDetails.text);
    if (value.method === "Log.entryAdded" && value.params.entry.level === "error") {
      const entry = value.params.entry;
      // An expected 404 document logs its own status; that is not a page error.
      if (!(entry.source === "network" && entry.url === pageUrl && documentStatus === 404)) errors.push(`${entry.source}: ${entry.text}`);
    }
    if (value.method === "Network.requestWillBeSent" && value.params.type === "Script") scripts.push(value.params.request.url);
    if (value.method === "Network.responseReceived" && value.params.type === "Document") documentStatus = value.params.response.status;
  });
  async function evaluate(expression) {
    const answer = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.equal(answer.exceptionDetails, undefined, expression);
    return answer.result.value;
  }
  async function load(route, width) {
    errors = [];
    scripts = [];
    documentStatus = 0;
    pageUrl = origin + route;
    await send("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: width < 500 });
    const loaded = new Promise(resolve => {
      const listener = event => {
        if (JSON.parse(event.data).method === "Page.loadEventFired") { socket.removeEventListener("message", listener); resolve(); }
      };
      socket.addEventListener("message", listener);
    });
    await send("Page.navigate", { url: origin + route });
    await loaded;
    // Let deferred scripts and their first requests settle.
    await new Promise(resolve => setTimeout(resolve, 600));
  }
  async function inspect(route, expected, mode, width) {
    const state = await evaluate(`(() => ({
      overflow: document.documentElement.scrollWidth - innerWidth,
      main: Boolean(document.querySelector("main")),
      h1: document.querySelector("h1")?.textContent.trim().slice(0, 80) || "",
      text: document.body.innerText,
      // Content past the viewport that an overflow:hidden ancestor clips
      // escapes the scrollWidth check; deliberate scroll containers are fine.
      clipped: [...document.querySelectorAll("main *")].filter(node => {
        if (!node.childNodes.length || ![...node.childNodes].some(child => child.nodeType === 3 && child.textContent.trim())) return false;
        const box = node.getBoundingClientRect();
        if (box.width === 0 || box.right <= innerWidth + 1) return false;
        for (let up = node.parentElement; up; up = up.parentElement) {
          if (["auto", "scroll"].includes(getComputedStyle(up).overflowX)) return false;
        }
        return true;
      }).slice(0, 3).map(node => node.tagName.toLowerCase() + (node.className ? "." + String(node.className).split(" ")[0] : "") + " " + JSON.stringify(node.textContent.trim().slice(0, 40))),
    }))()`);
    const problems = [];
    if (documentStatus !== expected) problems.push(`status ${documentStatus}, expected ${expected}`);
    if (!state.main) problems.push("no <main>");
    if (!state.h1) problems.push("no <h1>");
    if (state.overflow > 0) problems.push(`horizontal overflow ${state.overflow}px`);
    for (const node of state.clipped) problems.push(`clipped ${node}`);
    for (const text of FORBIDDEN_TEXT) if (state.text.includes(text)) problems.push(`visible text ${JSON.stringify(text)}`);
    for (const url of scripts) {
      const parsed = new URL(url);
      if (!ALLOWED_SCRIPT_HOSTS.has(parsed.host)) problems.push(`script from ${parsed.host}`);
      if (parsed.pathname.startsWith("/cdn-cgi/")) problems.push(`edge-injected script ${parsed.pathname}`);
    }
    for (const error of errors) problems.push(`console: ${error.slice(0, 200)}`);
    results.push({ route, mode, width, status: documentStatus, h1: state.h1, problems });
    if (problems.length) failures.push(`${route} [${mode} ${width}px]: ${problems.join("; ")}`);
  }
  async function screenshot(name) {
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
    await writeFile(path.join(output, name), Buffer.from(image.data, "base64"));
  }
  const slug = route => (route.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "home");
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await send("Log.enable");
  for (const scriptsDisabled of [true, false]) {
    await send("Emulation.setScriptExecutionDisabled", { value: scriptsDisabled });
    const mode = scriptsDisabled ? "no-js" : "js";
    for (const [route, expected] of PAGES) {
      for (const width of scriptsDisabled ? [1440, 390, 320] : [1440, 390]) {
        await load(route, width);
        await inspect(route, expected, mode, width);
        if (width === 390) await screenshot(`${slug(route)}-${mode}-390.png`);
      }
      if (scriptsDisabled) {
        // Text-only 200% zoom: double computed font sizes, keep device pixels.
        await load(route, 390);
        await evaluate("[...document.querySelectorAll('body, body *')].map(node => [node, getComputedStyle(node).fontSize]).forEach(([node, size]) => { node.style.fontSize = (parseFloat(size) * 2) + 'px'; })");
        await inspect(route, expected, "no-js text-200", 390);
        await screenshot(`${slug(route)}-no-js-text-200-390.png`);
      }
    }
  }
  const receipt = { origin, pages: PAGES.length, checks: results.length, failures };
  await writeFile(path.join(output, "site-audit.json"), JSON.stringify({ ...receipt, results }, null, 2) + "\n");
  console.log(JSON.stringify(receipt, null, 2));
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await new Promise(resolve => browser.exitCode !== null ? resolve() : browser.once("exit", resolve));
  // Chromium can still be flushing its profile just after exit.
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
process.exitCode = failures.length ? 1 : 0;
