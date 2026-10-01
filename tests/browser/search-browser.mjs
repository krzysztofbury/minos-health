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
let socket;
try {
  let port;
  for (let i = 0; i < 200; i++) {
    try { port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; break; }
    catch { if (browser.exitCode !== null) throw new Error("browser startup failed"); await new Promise((resolve) => setTimeout(resolve, 50)); }
  }
  assert.ok(port);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket = new WebSocket(targets.find((item) => item.type === "page").webSocketDebuggerUrl);
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
    await send("Page.navigate", { url });
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled');
    assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true);
    await evaluate('document.querySelector("#query").value="badania moczu"; document.querySelector("#query").dispatchEvent(new Event("input",{bubbles:true}))');
    await wait('document.querySelectorAll("#results article").length > 0 && !document.querySelector("#state").textContent.includes("Wyszukiwanie...")');
    assert.equal(await evaluate('document.querySelector("#results").textContent.includes("Odmiana wyrazów")'), true);
    assert.equal(await evaluate('document.querySelector("#provenance a").href.startsWith("https://slowniki.nfz.gov.pl/")'), true);
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(image.data, "base64"));
    await evaluate('document.querySelector("#release").selectedIndex=1; document.querySelector("#release").dispatchEvent(new Event("change",{bubbles:true})); document.querySelector("#query").value="A01"; document.querySelector("#query").dispatchEvent(new Event("input",{bubbles:true}))');
    await wait('document.querySelector("#results article") && document.querySelector("#results article h2").textContent.includes("A01") && !document.querySelector("#state").textContent.includes("Wyszukiwanie...")');
    await evaluate('document.querySelector("#query").focus(); document.querySelector("#query").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowDown",bubbles:true}))');
    assert.equal(await evaluate('document.activeElement.tagName === "ARTICLE"'), true);
    await evaluate('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}))');
    assert.equal(await evaluate('document.querySelector("#results details").open'), true);
    await evaluate('document.querySelector("#query").value=""; document.querySelector("#query").dispatchEvent(new Event("input",{bubbles:true}))');
    await wait('document.querySelectorAll("#results article").length === 0');
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.some((request) => request.url.endsWith("/api/v1/search") && request.method === "POST"));
  assert.ok(requests.every((request) => !request.url.includes("badania") && !request.url.includes("A01") && !request.url.includes("plausible")));
  assert.ok(requests.filter((request) => request.url.endsWith("/api/v1/search")).every((request) => request.postData && JSON.parse(request.postData).release_id));
  assert.equal(await evaluate('Boolean(localStorage.length || sessionStorage.length || document.cookie)'), false);
  console.log("Real browser acceptance passed: desktop/mobile, morphology, context, keyboard, no query URLs or analytics.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await new Promise((resolve) => { if (browser.exitCode !== null) resolve(); else browser.once("exit", resolve); });
  await rm(profile, { recursive: true, force: true });
}
