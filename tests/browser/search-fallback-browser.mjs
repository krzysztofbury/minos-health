import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output] = process.argv;
assert.ok(executable && url && output);
assert.equal(new URL(url).protocol, "http:", "This synthetic test is local-only");
const profile = await mkdtemp(path.join(os.tmpdir(), "minos-fallback-browser-"));
const browser = spawn(executable, ["--host-resolver-rules=MAP minoshealth.pl 127.0.0.1", "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  let port;
  for (let i = 0; i < 1200; i++) {
    try { port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; break; }
    catch { if (browser.exitCode !== null) throw new Error("browser startup failed"); await new Promise((resolve) => setTimeout(resolve, 50)); }
  }
  assert.ok(port);
  let target;
  for (let i = 0; i < 100; i++) {
    target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((candidate) => candidate.type === "page");
    if (target) break;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.ok(target);
  socket = new WebSocket(target.webSocketDebuggerUrl);
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
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: new URL("/", url).href });
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled');
    for (const [label, query, expected] of [["folded", "etykieta lodz", "bez polskich znaków"], ["synonym", "syntetyczna nazwa", "synonim:"], ["membership", "wariant", "występuje w 1 grupie JGP"]]) {
      await evaluate(`document.querySelector("#query").value=${JSON.stringify(query)}; document.getElementById("search-form").requestSubmit()`);
      await wait(`document.querySelector("#results").textContent.includes(${JSON.stringify(expected)})`);
      assert.equal(await evaluate("document.documentElement.scrollWidth > window.innerWidth"), false);
      if (label === "membership") {
        const links = await evaluate('[...document.querySelectorAll(".result")].map((link) => link.getAttribute("href"))');
        assert.ok(links.some((link) => /^\/slowniki\/icd-10\/E11\.0\/jgp\/20\d\d-\d\d-\d\d$/.test(link)));
      }
      const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      await writeFile(path.join(output, `${name}-${label}.png`), Buffer.from(image.data, "base64"));
    }
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.some((request) => request.url.endsWith("/api/v1/search") && request.method === "POST"));
  assert.ok(requests.every((request) => !/etykieta|syntetyczna|wariant/.test(request.url)));
  assert.equal(await evaluate("Boolean(localStorage.length || sessionStorage.length || document.cookie)"), false);
  console.log("Real browser fallback acceptance passed: desktop/mobile labels, membership dates, no overflow, query URLs or storage.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise((resolve) => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise((resolve) => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) {
    browser.kill("SIGKILL");
    await new Promise((resolve) => browser.once("exit", resolve));
  }
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
