import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { waitForDevToolsPort } from "./devtools-port.mjs";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output] = process.argv;
assert.ok(executable && url && output);
const profile = await mkdtemp(path.join(os.tmpdir(), "jc-"));
const browser = spawn(executable, ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--host-resolver-rules=MAP minoshealth.pl 127.0.0.1", "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  let target;
  // The shared reader refuses an empty or partial port line (#236).
  const port = await waitForDevToolsPort(browser, profile);
  for (let attempt = 0; attempt < 1200; attempt++) {
    try {
      target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item => item.type === "page");
      if (target) break;
    } catch { if (browser.exitCode !== null) throw new Error("browser startup failed"); }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.ok(target);
  socket = new WebSocket(target.webSocketDebuggerUrl);
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
    for (let attempt = 0; attempt < 200; attempt++) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error("condition browser deadline exceeded");
  };
  const navigate = async (route, selector) => {
    await send("Page.navigate", { url: new URL(route, url).href });
    await wait(`location.pathname === ${JSON.stringify(route)} && document.querySelector(${JSON.stringify(selector)}) && document.fonts.status === 'loaded'`);
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await navigate("/jgp/2026-07-10/A01", ".jgp-conditions");
    assert.ok(await evaluate("document.querySelector('.jgp-rules').textContent.includes('Wskazanie rozpoznania')"));
    assert.ok(await evaluate("document.querySelector('.answer-rail').textContent.includes('nie wynik grupowania')"));
    const list = await evaluate("document.querySelector('.jgp-rules a[href*=" + JSON.stringify("/listy/") + "]').getAttribute('href')");
    assert.equal(list, "/jgp/2026-07-10/listy/1a/1/1");
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
    const groupImage = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `conditions-${name}.png`), Buffer.from(groupImage.data, "base64"));
    await navigate("/jgp/2026-07-10/listy/1a/2/1", ".code-list");
    assert.equal(await evaluate("document.querySelectorAll('.code-list li').length"), 3);
    assert.equal(await evaluate("Boolean(document.querySelector('a[href*=\"G60.4\"]'))"), false);
    await evaluate("document.querySelector('a[href*=\"/E11.0/jgp/\"]').click()");
    await wait("location.pathname === '/slowniki/icd-10/E11.0/jgp/2026-07-10' && document.querySelector('.jgp-memberships')");
    assert.ok(await evaluate("document.querySelector('.answer-sentence').textContent.includes('występuje w')"));
    // The side column reads with the page, never in a scroller of its own.
    assert.equal(await evaluate("getComputedStyle(document.querySelector('.answer-rail')).overflowY"), "visible");
    assert.ok(await evaluate("document.querySelector('.site-header .brand') && document.querySelector('.site-footer')"));
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
    const reverseImage = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `membership-${name}.png`), Buffer.from(reverseImage.data, "base64"));
    await evaluate("const input = document.getElementById('jgp-date'); input.value = '11.07.2026'; input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))");
    await wait("location.pathname === '/slowniki/icd-10/E11.0/jgp/2026-07-11' && document.querySelector('.jgp-memberships')");
    assert.equal(await evaluate("document.querySelector('.jgp-memberships a[href*=\"/A01\"]').getAttribute('href')"), "/jgp/2026-07-11/A01");
    await navigate("/jgp/2026-07-10/N09", ".jgp-conditions");
    assert.ok(await evaluate("document.body.textContent.includes('bez wiersza katalogu na ten dzień')"));
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.every(request => !request.includes("?")));
  console.log("Conditions browser acceptance passed: desktop/mobile, structured source rules, physical list evidence, unmatched codes without fabricated links, dated reverse links and date selection.");
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
