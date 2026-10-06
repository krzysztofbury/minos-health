/* Bounded, local-only workflow observations. Never export queries or URLs. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { waitForDevToolsPort } from "./devtools-port.mjs";

const [, , executable, url, output, taskFile] = process.argv;
assert.ok(executable && url && output && taskFile);
const origin = new URL(url);
assert.equal(origin.protocol, "http:", "Measurements and faults are local-only");
assert.equal(origin.hostname, "minoshealth.pl");
const tasks = JSON.parse(await readFile(taskFile, "utf8")).tasks;
assert.ok(tasks.length > 0 && tasks.length <= 8);
assert.equal(new Set(tasks.map(task => task.id)).size, tasks.length);
for (const task of tasks) {
  assert.match(task.id, /^W[0-9]{2}$/);
  assert.ok(typeof task.query === 'string' && task.query.length <= 120 && task.query.length > 0);
  assert.ok(task.group_prefix.startsWith('/') && task.href_prefix.startsWith('/'));
}
const profile = await mkdtemp(path.join(os.tmpdir(), "wf-"));
const browser = spawn(executable, ["--host-resolver-rules=MAP minoshealth.pl 127.0.0.1", "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  const port = await waitForDevToolsPort(browser, profile);
  let target;
  for (let attempt = 0; attempt < 100; attempt++) {
    target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(item => item.type === "page");
    if (target) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.ok(target, "browser target unavailable");
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let id = 0;
  const pending = new Map(), errors = [], requests = [], requestStates = new Map();
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id); pending.delete(message.id);
      if (message.error) callback.reject(new Error("browser protocol failed")); else callback.resolve(message.result);
    } else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
    else if (message.method === "Network.requestWillBeSent") {
      const request = message.params.request;
      assert.ok(!new URL(request.url).search, "query URL observed");
      if (request.url.endsWith("/api/v1/search")) {
        assert.equal(request.method, "POST");
        const body = JSON.parse(request.postData);
        const state = { system: body.system, limit: body.limit, status: null, cancelled: false };
        requests.push(state);
        requestStates.set(message.params.requestId, state);
      }
    } else if (message.method === "Network.responseReceived") {
      const state = requestStates.get(message.params.requestId);
      if (state) state.status = message.params.response.status;
    } else if (message.method === "Network.loadingFailed") {
      const state = requestStates.get(message.params.requestId);
      if (state) state.cancelled = Boolean(message.params.canceled);
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const value = ++id;
    const timer = setTimeout(() => { pending.delete(value); reject(new Error("browser protocol deadline")); }, 15000);
    pending.set(value, { resolve: result => { clearTimeout(timer); resolve(result); }, reject: error => { clearTimeout(timer); reject(error); } });
    socket.send(JSON.stringify({ id: value, method, params }));
  });
  const evaluate = async expression => {
    const answer = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!answer.exceptionDetails, "workflow browser evaluation failed");
    return answer.result.value;
  };
  const wait = async expression => {
    for (let attempt = 0; attempt < 280; attempt++) {
      if (await evaluate(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error("workflow observation deadline");
  };
  const settled = 'document.querySelectorAll(".result-group").length === 3 && [...document.querySelectorAll(".result-group")].every(node => ["ready","failed","uncovered"].includes(node.dataset.searchState))';
  const ready = 'document.querySelectorAll(".result-group").length === 3 && [...document.querySelectorAll(".result-group")].every(node => node.dataset.searchState === "ready")';
  const home = async () => {
    await send("Page.navigate", { url: "about:blank" });
    await wait('location.href === "about:blank"');
    await send("Page.navigate", { url });
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled');
    assert.deepEqual(await evaluate('[...document.querySelectorAll("input[name=system]:checked")].map(node => node.value)'), ["icd10-pl", "icd9-pl", "jgp"]);
    // Timestamp DOM completion in the page, then wait two animation frames.
    // Polling only retrieves that timestamp; it does not add 50 ms to latency.
    await evaluate(`window.observation = {};
      const observer = new MutationObserver(records => {
        const state = window.observation;
        const popup = document.querySelector('#suggestions');
        const changed = records.some(record => record.target === popup || popup.contains(record.target));
        if (changed && state.suggestStart !== undefined && state.suggestEnd === undefined && document.querySelector('#suggestions .suggestion')) {
          const generation = state.suggestGeneration;
          const query = document.querySelector('#query').value;
          state.suggestEnd = null;
          requestAnimationFrame(() => requestAnimationFrame(() => {
            if (state.suggestGeneration === generation && document.querySelector('#query').value === query) state.suggestEnd = performance.now();
          }));
        }
        if (state.submitStart !== undefined && state.resultEnd === undefined && ${settled}) {
          state.resultEnd = null;
          requestAnimationFrame(() => requestAnimationFrame(() => { state.resultEnd = performance.now(); }));
        }
      }); observer.observe(document.body, {subtree:true, childList:true, attributes:true}); true`);
  };
  const snapshot = async name => {
    await evaluate("document.fonts.ready.then(() => true)");
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `${name}.png`), Buffer.from(image.data, "base64"));
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  const version = await send("Browser.getVersion");
  await home();
  const debounce_ms = await evaluate("fetch('/search.js').then(response => response.text()).then(body => Number((body.match(/SUGGEST_DELAY_MS = ([0-9]+)/) || body.match(/setTimeout[(]suggest, ([0-9]+)[)]/))[1]))");
  const rows = [];
  for (const [device, width, height, latency] of [["desktop",1440,1000,0], ["mobile",390,844,80]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Network.emulateNetworkConditions", { offline: false, latency, downloadThroughput: latency ? 2000000 : -1, uploadThroughput: latency ? 500000 : -1 });
    for (const [typing_cadence_ms, repetitions] of [[60, 3], [180, 1]]) {
    for (let repetition = 0; repetition < repetitions; repetition++) {
      for (const task of tasks) {
        await home();
        const offset = requests.length;
        await evaluate('document.querySelector("#query").focus(); window.observation.typingStart = performance.now()');
        for (const character of task.query) {
          // Last input event is the keypress-to-suggestions origin, including debounce.
          await evaluate("window.observation.suggestGeneration = (window.observation.suggestGeneration || 0) + 1; window.observation.suggestStart = performance.now(); delete window.observation.suggestEnd");
          await send("Input.insertText", { text: character });
          await new Promise(resolve => setTimeout(resolve, typing_cadence_ms));
        }
        await wait('Number.isFinite(window.observation.suggestEnd) || performance.now()-window.observation.suggestStart >= 3000');
        const suggestion = await evaluate('({suggestion_ms: Number.isFinite(window.observation.suggestEnd) ? window.observation.suggestEnd-window.observation.suggestStart : null, typing_ms: window.observation.suggestStart-window.observation.typingStart})');
        await evaluate('window.observation.submitStart = performance.now(); document.querySelector("#search-form").requestSubmit()');
        await wait('Number.isFinite(window.observation.resultEnd)');
        const observed = await evaluate(`(() => {
          const selector = ${JSON.stringify(task.href_prefix)};
          const links = [...document.querySelectorAll('.result-group .result')];
          const group = links.filter(link => link.pathname.startsWith(${JSON.stringify(task.group_prefix)}));
          const rank = group.findIndex(link => link.pathname.startsWith(selector) && link.pathname.endsWith(${JSON.stringify(task.href_suffix || "")})) + 1;
          return {submit_to_results_ms: window.observation.resultEnd-window.observation.submitStart, rank: rank || null, completed: rank > 0,
            service_date: document.querySelector('#group-jgp').textContent.match(/[0-9]{4}-[0-9]{2}-[0-9]{2}/)?.[0] || null,
            source_states: [...document.querySelectorAll('.result-group')].map(node => node.dataset.searchState)};
        })()`);
        if (observed.source_states[2] === "ready") {
          assert.match(observed.service_date || '', /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/, "Ready JGP results must display their service date");
        } else {
          await snapshot(`${device}-${task.id}-${typing_cadence_ms}-${repetition}-failed-source`);
        }
        const calls = requests.slice(offset);
        if (repetition === 0 && typing_cadence_ms === 60) await snapshot(`${device}-${task.id}`);
        let detail_ms = null;
        if (observed.completed) {
          const started = performance.now();
          await evaluate(`[...document.querySelectorAll('a.result')].find(link => link.pathname.startsWith(${JSON.stringify(task.href_prefix)}) && link.pathname.endsWith(${JSON.stringify(task.href_suffix || "")})).click()`);
          await wait(`location.pathname.startsWith(${JSON.stringify(task.href_prefix)}) && location.pathname.endsWith(${JSON.stringify(task.href_suffix || "")}) && Boolean(document.querySelector('main h1'))`);
          // Navigation completion uses CDP retrieval and includes up to 50 ms polling.
          await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))');
          detail_ms = performance.now() - started;
        }
        rows.push({ id: task.id, device, repetition, typing_cadence_ms, cache: repetition || typing_cadence_ms === 180 ? "warm_browser" : "first_pass", emulated_latency_ms: latency, ...suggestion, ...observed, detail_ms, search_requests: calls });
        await writeFile(path.join(output, "workflow-partial.json"), JSON.stringify({ browser: version.product, debounce_ms, status: "incomplete", rows }, null, 2) + "\n");
        assert.equal(await evaluate("Boolean(localStorage.length || sessionStorage.length || document.cookie)"), false);
      }
    }
    }
  }
  // A transport-only local fault exercises the unchanged all-source UI.
  await home();
  await evaluate(`window.originalFetch=fetch; window.faultCalls=0; window.fetch=async (url, options) => {
    if (url === '/api/v1/search' && JSON.parse(options.body).system === 'icd10-pl' && ++window.faultCalls === 1)
      return new Response(JSON.stringify({error:{code:'busy'}}), {status:429,headers:{'Retry-After':'1'}});
    return window.originalFetch(url,options);
  }; document.querySelector('#query').value='E11.9'; document.querySelector('#search-form').requestSubmit()`);
  await wait("document.querySelector('#results').textContent.includes('Ponawiam')");
  await wait(ready);
  assert.equal(await evaluate("window.faultCalls"), 2);
  await evaluate(`window.fetch=async (url,options) => url === '/api/v1/search' && JSON.parse(options.body).system === 'icd10-pl' ? new Response(JSON.stringify({error:{code:'query_timeout'}}),{status:504}) : window.originalFetch(url,options);
    document.querySelector('#search-form').requestSubmit()`);
  await wait("document.querySelector('.result-group').dataset.searchState === 'failed'");
  await wait(settled);
  assert.equal(await evaluate("document.querySelector('#results-status').textContent.includes('Część słowników nie odpowiedziała')"), true);
  assert.equal(await evaluate("document.querySelector('#results-status').textContent.includes('Nic nie znaleziono')"), false);
  await snapshot("mobile-partial-timeout");
  await evaluate("window.fetch=window.originalFetch; document.querySelector('.result-group-foot button').click()");
  await wait(ready);
  assert.deepEqual(errors, []);
  await writeFile(path.join(output, "workflow.json"), JSON.stringify({
    browser: version.product, debounce_ms, rows,
    limitations: "Local packaged API/edge, no production contention. Mobile viewport with emulated latency is not physical mobile hardware. Scripted completion/rank, not practitioner errors, confusion or incumbent comparison. Detail includes up to 50ms polling. First pass is not guaranteed cold server.",
    faults: {transient_busy_retry: "passed", partial_timeout_not_empty: "passed", manual_recovery: "passed"},
    privacy: "POST-only searches; no query URLs, cookies or storage"
  }, null, 2) + "\n");
  console.log("Local workflow observations recorded; no practitioner performance claim.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) { browser.kill("SIGKILL"); await new Promise(resolve => browser.once("exit", resolve)); }
  await rm(profile, {recursive: true, force:true, maxRetries:10, retryDelay:200});
}
