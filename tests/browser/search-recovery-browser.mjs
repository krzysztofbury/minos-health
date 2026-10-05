import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output] = process.argv;
assert.ok(executable && url && output);
assert.equal(new URL(url).protocol, "http:", "Fault injection is local-only");
const profile = await mkdtemp(path.join(os.tmpdir(), "sr-"));
const browser = spawn(executable, ["--host-resolver-rules=MAP minoshealth.pl 127.0.0.1", "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  let target;
  for (let attempt = 0; attempt < 300; attempt++) {
    try {
      const port = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0];
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
    else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const value = ++id; pending.set(value, { resolve, reject }); socket.send(JSON.stringify({ id: value, method, params })); });
  const evaluate = async expression => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!result.exceptionDetails, "browser evaluation failed");
    return result.result.value;
  };
  const wait = async expression => {
    for (let attempt = 0; attempt < 300; attempt++) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error(`Recovery browser deadline: ${expression}`);
  };
  const submit = async query => evaluate(`document.querySelector('#query').value=${JSON.stringify(query)}; document.querySelector('#search-form').requestSubmit()`);
  const ready = "document.querySelector('.result-group')?.dataset.searchState === 'ready'";
  const snapshot = async name => {
    await evaluate("document.fonts.ready.then(() => true)");
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(image.data, "base64"));
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: "about:blank" });
    await wait('location.href === "about:blank" && !document.querySelector("#query")');
    await send("Page.navigate", { url });
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled');
    await evaluate('document.querySelectorAll("input[name=system]").forEach(box => { box.checked = box.value === "icd10-pl"; })');
    await submit("zawal serca"); await wait(ready);
    assert.ok(await evaluate("document.querySelector('.result-group .result').textContent.includes('I21')"));
    await snapshot(`${name}-expanded`);
    // Wrap only the transport. Production UI, abort logic and rendering stay real.
    await evaluate(`window.originalFetch=window.fetch; window.fault=''; window.calls=0;
      window.fetch=async function(url, options) {
        if (url !== '/api/v1/search') return window.originalFetch(url, options);
        window.calls++;
        if (window.fault === 'slow') await new Promise(resolve => setTimeout(resolve, 800));
        if (window.fault === 'once' && window.calls === 1 || window.fault === 'busy' || window.fault === 'long') return new Response(JSON.stringify({error:{code:'busy'}}), {status:429,headers:{'Retry-After':window.fault === 'long' ? '60' : '1'}});
        if (window.fault === 'timeout') return new Response(JSON.stringify({error:{code:'query_timeout'}}), {status:504});
        if (window.fault === 'empty') return new Response(JSON.stringify({results:[],truncated:false}), {status:200});
        if (window.fault === 'html' && window.calls === 1) return new Response('<html>busy</html>', {status:503});
        return window.originalFetch(url, options);
      }`);
    await evaluate("window.fault='slow'; window.calls=0");
    await submit("zawal serca");
    await wait("document.querySelector('#results').textContent.includes('Wciąż szukam')");
    await snapshot(`${name}-slow`); await wait(ready);
    for (const mode of ["once", "html"]) {
      await evaluate(`window.fault=${JSON.stringify(mode)}; window.calls=0`);
      await submit("zawal serca");
      await wait("document.querySelector('#results').textContent.includes('Ponawiam')");
      await wait(ready); assert.equal(await evaluate("window.calls"), 2);
    }
    for (const mode of ["busy", "timeout", "long"]) {
      await evaluate(`window.fault=${JSON.stringify(mode)}; window.calls=0`);
      await submit("zawal serca");
      await wait("document.querySelector('.result-group')?.dataset.searchState === 'failed'");
      assert.equal(await evaluate("window.calls"), mode === "busy" ? 2 : 1);
      assert.ok(await evaluate("!document.querySelector('#results-status').textContent.includes('Nic nie znaleziono')"));
      await snapshot(`${name}-${mode}`);
      await evaluate("window.fault=''; document.querySelector('.result-group-foot button').click()");
      await wait(ready);
      assert.ok(await evaluate("document.querySelector('#results-status').textContent.includes('Kliknij')"));
    }
    await evaluate("window.fault='empty'; window.calls=0");
    await submit("zawal serca"); await wait(ready);
    assert.ok(await evaluate("document.querySelector('#results-status').textContent.includes('Nic nie znaleziono')"));
    await evaluate("window.fault='slow'");
    await submit("zawal serca");
    await evaluate("window.fault=''");
    await submit("E11.9"); await wait(ready);
    await new Promise(resolve => setTimeout(resolve, 900));
    assert.ok(await evaluate("document.querySelector('.result-group .result').textContent.includes('E11.9')"));
    await evaluate("document.querySelector('.result-group .result').click()");
    await wait("location.pathname.includes('/slowniki/icd-10/E11.9') && document.querySelector('.jgp-memberships')");
    await snapshot(`${name}-detail`);
    const frame = (await send('Page.getFrameTree')).frameTree.frame.id;
    const errorHTML = await readFile(path.join(output, 'detail-timeout.html'), 'utf8');
    await send('Page.setDocumentContent', { frameId: frame, html: errorHTML });
    await wait("document.querySelector('h1').textContent.includes('Nie udało się wczytać')");
    await snapshot(`${name}-detail-timeout`);
    await evaluate("document.querySelector('.not-found-actions a').click()");
    await wait("document.querySelector('.jgp-memberships')");
    await send("Page.navigate", { url });
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled');
    await evaluate('document.querySelectorAll("input[name=system]").forEach(box => { box.checked = box.value === "jgp"; })');
    await submit("endoproteza");
    await wait(ready);
    assert.ok(await evaluate("document.querySelector('.result-group').textContent.includes('powiązana procedura:')"));
    await snapshot(`${name}-related-procedure`);
    assert.equal(await evaluate("Boolean(localStorage.length || sessionStorage.length || document.cookie)"), false);
  }
  assert.deepEqual(errors, []);
  assert.ok(requests.filter(request => request.url.endsWith('/api/v1/search')).every(request => request.method === 'POST'));
  assert.ok(requests.every(request => !/zawal|endoproteza/.test(request.url)));
  console.log('Recovery browser passed: real dictionary/JGP searches and clicks, 800ms feedback, transient and sustained 429, HTML 503, timeout, Retry-After, manual recovery, stale cancellation, empty results, desktop/mobile, query privacy.');
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) {
    browser.kill("SIGKILL"); await new Promise(resolve => browser.once("exit", resolve));
  }
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
