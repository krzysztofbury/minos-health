import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output] = process.argv;
assert.ok(executable && url && output);
const profile = await mkdtemp(path.join(os.tmpdir(), "minos-jgp-browser-"));
const args = ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"];
// Local containers serve plain HTTP under the production hostname; the public
// origin is HTTPS-only, so an http: target can never reach production.
const local = new URL(url).protocol === "http:";
if (local) args.unshift("--host-resolver-rules=MAP minoshealth.pl 127.0.0.1");
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
    // CSP violations and console errors; expected 404 documents log as "network",
    // and plain-HTTP local targets make Chromium ignore COOP (HTTPS in production).
    else if (message.method === "Log.entryAdded" && message.params.entry.level === "error" && message.params.entry.source !== "network" && !(local && message.params.entry.text.startsWith("The Cross-Origin-Opener-Policy header has been ignored"))) errors.push(message.params.entry.text);
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
    throw new Error(`jgp browser condition timed out: ${expression}`);
  };
  const visit = async (route, ready) => {
    await send("Page.navigate", { url: new URL(route, url).href });
    await wait(`location.pathname === '${route}' && document.readyState === 'complete' && document.fonts.status === 'loaded' && ${ready}`);
  };
  const shot = async name => {
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(image.data, "base64"));
  };
  const noOverflow = async () => assert.equal(await evaluate("document.documentElement.scrollWidth > window.innerWidth"), false);
  const listed = "[...document.querySelectorAll('.jgp-list li[data-search]')].filter(row => !row.hidden).map(row => row.querySelector('.code-chip').textContent)";
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable"); await send("Log.enable");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });

    // Catalog: chapters closed, filter revealed only by jgp.js, base-points label.
    await visit("/jgp/2026-07-10", "!document.querySelector('.jgp-filter').hidden");
    assert.deepEqual(await evaluate(listed), ["A01", "PZD02", "H21", "J01AJ", "AU35C", "SU52"]);
    assert.equal(await evaluate("document.querySelector('.site-nav [aria-current=page]').textContent.trim()"), "Grupy JGP");
    assert.ok((await evaluate("document.querySelector('.jgp-points-note').textContent")).includes("Punkty bazowe wg katalogu, bez modyfikatorów"));
    assert.ok(await evaluate("getComputedStyle(document.body).fontFamily.includes('Atkinson')"));
    await noOverflow();
    await shot(`jgp-catalog-${name}`);

    // Filter: case and diacritics folded, local only, no request and no URL change.
    // A chapter opened by hand before filtering is open again once it clears.
    await evaluate("document.getElementById('k-1ae-1').open = true");
    const before = requests.length;
    await send("Runtime.evaluate", { expression: "document.getElementById('jgp-filter').focus()" });
    await send("Input.insertText", { text: "DUZE" });
    await wait("document.querySelector('.jgp-filter-status').textContent === 'Pasuje: 1 z 6 grup'");
    assert.deepEqual(await evaluate(listed), ["PZD02"]);
    assert.equal(await evaluate("[...document.querySelectorAll('.jgp-chapter')].filter(chapter => !chapter.hidden && chapter.open).length"), 1);
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.jgp-catalog-part')].filter(part => !part.hidden).map(part => part.querySelector('.jgp-catalog-title').textContent)"), ["Katalog grup 1a"]);
    assert.equal(await evaluate("location.href"), new URL("/jgp/2026-07-10", url).href);
    // Opening a chapter may lazily fetch a font face; the filter itself never
    // fetches, and its text appears in no request.
    const fetched = requests.slice(before);
    assert.ok(fetched.every(request => /^\/fonts\/[a-z0-9-]+\.woff2$/.test(new URL(request).pathname) && new URL(request).origin === new URL(url).origin), fetched.join(" "));
    assert.ok(requests.every(request => !/duze/i.test(request)));
    await noOverflow();
    await shot(`jgp-filter-${name}`);
    await evaluate("const box = document.getElementById('jgp-filter'); box.value = ''; box.dispatchEvent(new Event('input'))");
    assert.equal(await evaluate("document.querySelector('.jgp-filter-status').textContent"), "6 grup w 2 rozdziałach. Rozwiń rozdział strzałką lub kliknięciem.");
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.jgp-chapter')].map(chapter => chapter.id + ':' + chapter.open + ':' + chapter.hidden)"), ["k-1a-1:false:false", "k-1ae-1:true:false"]);

    // Keyboard date change navigates by path, never by query string.
    await send("Runtime.evaluate", { expression: "document.getElementById('jgp-date').focus()" });
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowUp", code: "ArrowUp", windowsVirtualKeyCode: 38 });
    // A keyboard edit alone waits for Enter: "1" of "15" is not a choice yet.
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal(await evaluate("location.pathname"), "/jgp/2026-07-10");
    await send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await wait("/^\\/jgp\\/2026-\\d{2}-\\d{2}$/.test(location.pathname) && location.pathname !== '/jgp/2026-07-10' && document.querySelector('.jgp-row')");

    // The zakres picker opens over the page without widening it.
    await visit("/jgp/2026-07-10", "document.querySelector('.jgp-scopes')");
    await evaluate("document.querySelector('.jgp-scopes > summary').click()");
    await wait("document.querySelector('.jgp-scopes').open");
    await noOverflow();
    await shot(`jgp-scopes-${name}`);

    // Scope filter: zakres 15 keeps only groups marked for chirurgia ogólna.
    await visit("/jgp/2026-07-10/zakres/15", "document.querySelector('.jgp-row')");
    assert.deepEqual(await evaluate(listed), ["A01", "H21"]);
    assert.ok((await evaluate("document.querySelector('.jgp-scopes summary').textContent")).includes("chirurgia ogólna"));

    // Group page from a catalog row click.
    await visit("/jgp/2026-07-10", "document.querySelector('.jgp-row')");
    await evaluate("document.querySelector('.jgp-row[href=\"/jgp/2026-07-10/PZD02\"]').click()");
    await wait("location.pathname === '/jgp/2026-07-10/PZD02' && document.querySelector('.jgp-values') && document.fonts.status === 'loaded'");
    const table = await evaluate("document.querySelector('.jgp-values').textContent");
    assert.ok(table.includes("Wartość punktowa - hospitalizacja planowa"));
    assert.ok(table.includes("12 871,72 pkt"));
    assert.ok(table.includes("brak wartości w katalogu"));
    assert.equal(await evaluate("document.getElementById('jgp-date').dataset.suffix"), "/PZD02");
    assert.ok(await evaluate("document.querySelector('.site-header .brand') && document.querySelector('.site-footer') && document.querySelector('.breadcrumbs')"));
    await noOverflow();
    await shot(`jgp-group-${name}`);

    // Explicit gap before the first covered release.
    await visit("/jgp/2026-04-30", "document.querySelector('h1')");
    assert.equal(await evaluate("document.querySelector('h1').textContent"), "Brak zweryfikowanego katalogu JGP dla tego dnia");
    await noOverflow();
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.every(request => !request.includes("?")));
  console.log("JGP browser acceptance passed: desktop/mobile catalog, local filter without requests, keyboard date navigation, scope filter, group page values, explicit gap.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) {
    browser.kill("SIGKILL");
    await new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); });
  }
  // Chromium helpers can still write to the profile briefly after the main exit.
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
