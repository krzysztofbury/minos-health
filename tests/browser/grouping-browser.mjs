import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { waitForDevToolsPort } from "./devtools-port.mjs";

const [, , executable, url, output, bodyFile, expected] = process.argv;
assert.ok(executable && url && output && bodyFile && expected);
const body = JSON.parse(await readFile(bodyFile, "utf8"));
const profile = await mkdtemp(path.join(os.tmpdir(), "minos-grouping-browser-"));
const browser = spawn(executable, ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", ...(url.startsWith("http:") ? ["--host-resolver-rules=MAP minoshealth.pl 127.0.0.1"] : []), `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  const port = await waitForDevToolsPort(browser, profile);
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page");
    if (!target) await new Promise(r => setTimeout(r, 50));
  }
  assert.ok(target);
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  let id = 0, held = null, hold = false;
  const pending = new Map(), errors = [], requests = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) { const cb = pending.get(message.id); pending.delete(message.id); if (message.error) cb.reject(new Error(message.error.message)); else cb.resolve(message.result); }
    else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
    else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
    else if (message.method === "Fetch.requestPaused") {
      if (hold) { held = message.params.requestId; hold = false; }
      else void send("Fetch.continueRequest", { requestId: message.params.requestId });
    }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; pending.set(n, { resolve, reject }); socket.send(JSON.stringify({ id: n, method, params })); });
  const evaluate = async expression => { const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }); assert.ok(!r.exceptionDetails, JSON.stringify(r.exceptionDetails)); return r.result.value; };
  const wait = async expression => { for (let i = 0; i < 400; i++) { if (await evaluate(expression)) return; await new Promise(r => setTimeout(r, 50)); } throw new Error("Grouping browser timed out: " + expression); };
  const shot = async (name, full = true) => { const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full }); await writeFile(path.join(output, name + ".png"), Buffer.from(image.data, "base64")); };
  const set = async (selector, value) => evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.value=${JSON.stringify(value)}; e.dispatchEvent(new Event('input',{bubbles:true})); })()`);
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/v1/group", requestStage: "Response" }] });
  const click = async selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const step = async index => wait(`!document.querySelector('[data-step="${index}"]').hidden`);
  const next = async index => { await click("#step-next"); await step(index); };
  const pl = iso => iso.split("-").reverse().join(".");
  // Typing digits only exercises the DD.MM.RRRR mask the way a coder types.
  const type = async (selector, text) => { await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`); await evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); e.value=''; })()`); await send("Input.insertText", { text }); };
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: new URL("/gruper", url).href });
    await wait("document.readyState === 'complete' && document.querySelector('#group-fields') && !document.querySelector('#group-fields').disabled && document.fonts.status === 'loaded'");
    await shot("grouping-empty-" + name, false);
    const e = body.encounter;
    // Step 1: dates in Polish format regardless of browser language; missing facts block the step.
    await type("#admitted", e.admitted_on.split("-").reverse().join(""));
    assert.equal(await evaluate("document.getElementById('admitted').value"), pl(e.admitted_on));
    await set("#discharged", pl(e.discharged_on));
    await click("#step-next");
    assert.equal(await evaluate("document.querySelector('[data-step=\"0\"]').hidden"), false);
    assert.equal(await evaluate("document.getElementById('age').getAttribute('aria-invalid')"), "true");
    assert.ok(await evaluate("!document.getElementById('step-errors').hidden"));
    await shot("grouping-errors-" + name, false);
    await set("#age", String(e.age_completed_years));
    // Owner review: fields in one row start at the same height.
    const top = selector => evaluate(`Math.round(document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect().top)`);
    if (width > 900) {
      assert.equal(await top("#admitted"), await top("#discharged"));
      assert.equal(await top("#age"), await top("#discharge-select"));
    }
    // Explanations are tooltips, not text between label and field.
    await click(".tip[aria-label='Wyjaśnienie: wiek w dniu przyjęcia']");
    assert.ok(await evaluate("!document.getElementById('tooltip').hidden && document.getElementById('tooltip').textContent.includes('Ukończone lata')"));
    await click("h2#step-0-title");
    assert.ok(await evaluate("document.getElementById('tooltip').hidden"));
    // Official discharge modes; the calendar speaks Polish whatever the browser language.
    await wait("document.querySelector(\"#discharge-select option[value='9']\")");
    assert.ok(await evaluate("document.querySelector(\"#discharge-select option[value='9']\").textContent.includes('zgon pacjenta')"));
    await click("#admitted + .picker-button");
    assert.ok(await evaluate("!document.getElementById('picker').hidden && /^(styczeń|luty|marzec|kwiecień|maj|czerwiec|lipiec|sierpień|wrzesień|październik|listopad|grudzień) \\d{4}$/.test(document.querySelector('.picker-title').textContent)"));
    assert.ok(await evaluate("Array.from(document.querySelectorAll('.picker-weekday')).map(c => c.textContent).join(' ') === 'pn wt śr cz pt so nd'"));
    await shot("grouping-calendar-" + name, false);
    await click(".picker-cell[data-value$='-15']");
    assert.ok(await evaluate("/^15\\.\\d{2}\\.\\d{4}$/.test(document.getElementById('admitted').value) && document.getElementById('picker').hidden"));
    await set("#admitted", pl(e.admitted_on));
    assert.ok(await evaluate(`document.getElementById('service-discharge-label').textContent.includes(${JSON.stringify(pl(e.discharged_on))})`));
    if (body.service_date === e.discharged_on) await click("input[name=service-mode][value=discharge]");
    else { await click("input[name=service-mode][value=other]"); await set("#service-date", pl(body.service_date)); }
    await click("input[name=month-mode][value=discharge]");
    await shot("grouping-hospitalization-" + name, false);
    await next(1);
    // Step 2: the first stay starts with the episode dates.
    assert.equal(await evaluate("document.querySelector('#wards input.date-pl').value"), pl(e.admitted_on));
    // Specialty search by name; hospital wards rank first.
    await set("#wards input[role=combobox]", "wewnętrz");
    await evaluate("document.querySelector('#wards input[role=combobox]').focus()");
    await wait("document.querySelector('#wards [role=option]')");
    assert.equal(await evaluate("document.querySelector('#wards [role=option] .code-chip').textContent"), "4000");
    await shot("grouping-specialty-" + name, false);
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowDown" });
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter" });
    assert.equal(await evaluate("document.querySelector('#wards input[role=combobox]').value"), "4000");
    assert.ok(await evaluate("document.getElementById('case-summary').textContent.includes('Oddział chorób wewnętrznych')"));
    await set("#wards input[role=combobox]", e.stays[0].department);
    await next(2);
    // Step 3: keyboard code search with complete suggestion labels, then the exact documented code.
    await set("#principal input", "E11.0");
    await evaluate("document.querySelector('#principal input').focus()");
    await wait("document.querySelector('#principal [role=option]')");
    assert.ok(await evaluate("document.querySelector('#principal [role=option]').textContent.length > 8"));
    assert.ok(await evaluate("Array.from(document.querySelectorAll('#principal .suggestion-label')).every(label => label.scrollWidth <= label.clientWidth + 1)"));
    await shot("grouping-search-" + name, false);
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowDown" });
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter" });
    assert.equal(await evaluate("document.querySelector('#principal input').value"), "E11.0");
    await set("#principal input", e.principal_diagnosis.code);
    await evaluate("const p=document.querySelector('#principal select'); p.value='episode'; p.dispatchEvent(new Event('change',{bubbles:true}))");
    await next(3);
    // Step 4: one procedure occurrence attributed to the first stay, then the product report.
    await click("#add-procedure");
    await set("#procedures .search-box input", e.procedures[0].code);
    await set("#procedures input.date-pl", pl(e.procedures[0].performed_on));
    await evaluate("const s=document.querySelector('#procedures select'); s.selectedIndex=1; s.dispatchEvent(new Event('change',{bubbles:true}))");
    await click("input[name=product-report][value=empty]");
    if (width > 900) assert.equal(await top("#procedures select"), await top("#procedures input.date-pl"));
    await shot("grouping-procedures-" + name, false);
    await next(4);
    await wait(`document.querySelector('.verdict-group[data-group="${expected}"]')`);
    assert.equal(await evaluate("location.pathname + location.search"), "/gruper");
    assert.equal(await evaluate("localStorage.length + sessionStorage.length"), 0);
    assert.equal(await evaluate("document.documentElement.scrollWidth > innerWidth"), false);
    assert.ok(await evaluate(`document.querySelector('.verdict-group[data-group="${expected}"] .explain').open`));
    assert.ok(await evaluate(`document.querySelector('.verdict-group[data-group="${expected}"] .rule-tree').textContent.includes(${JSON.stringify(e.procedures[0].code)})`));
    assert.ok(await evaluate("document.getElementById('case-summary').textContent.includes(" + JSON.stringify(e.principal_diagnosis.code) + ")"));
    await evaluate("window.scrollTo(0,0)");
    await wait("document.getAnimations().every(a => a.playState !== 'running')");
    await shot("grouping-result-" + name);
    // A modifier is confirmed from the group's own possibilities and re-evaluates the case.
    if (await evaluate(`!!document.querySelector('.verdict-group[data-group="${expected}"] .modifier input')`)) {
      await click(`.verdict-group[data-group="${expected}"] .modifier input`);
      await wait(`(() => { const box = document.querySelector('.verdict-group[data-group="${expected}"] .modifier input'); return box && box.checked && document.getElementById('group-status').className === 'visually-hidden' && document.getElementById('case-summary').textContent.includes('Potwierdzone modyfikatory'); })()`);
      await shot("grouping-modifier-" + name, false);
      await click(`.verdict-group[data-group="${expected}"] .modifier input`);
      await wait(`(() => { const box = document.querySelector('.verdict-group[data-group="${expected}"] .modifier input'); return box && !box.checked; })()`);
    }
    // Out-of-scope groups arrive once per reason and render as catalog code lists.
    const skipped = "Array.from(document.querySelectorAll('.result-technical details')).find(d => d.querySelector('summary').textContent.startsWith('Grupy poza oceną'))";
    await evaluate(`document.querySelector('.result-technical').open = true; (${skipped}).open = true`);
    assert.ok(await evaluate(`Number((${skipped}).querySelector('summary').textContent.match(/\\((\\d+)\\)/)[1]) > 0`));
    assert.ok(await evaluate(`Array.from((${skipped}).querySelectorAll('p.group-help')).some(p => /^1ae?: \\S/.test(p.textContent))`));
    await evaluate("document.querySelector('.result-technical').open = false");

    // Hold a fresh evaluation, edit the case, and release the obsolete answer.
    hold = true;
    await click("[data-goto='3']"); await step(3); await next(4);
    for (let i = 0; i < 200 && !held; i++) await new Promise(r => setTimeout(r, 50));
    assert.ok(held);
    await click("[data-goto='0']"); await step(0);
    await set("#age", String(e.age_completed_years + 1));
    await send("Fetch.continueRequest", { requestId: held }).catch(() => {}); held = null;
    await new Promise(r => setTimeout(r, 600));
    assert.equal(await evaluate("document.querySelectorAll('.verdict-group').length"), 0);
    await set("#age", String(e.age_completed_years));
    await click("[data-goto='3']"); await step(3);
    await click("input[name=product-report][value=present]");
    await next(4);
    await wait("document.getElementById('group-answer').textContent.includes('nie jest jeszcze obsługiwany')");
    assert.equal(await evaluate("document.querySelectorAll('.verdict-group').length"), 0);
    await click("#clear-case");
    await step(0);
    assert.equal(await evaluate("document.querySelector('#principal input').value"), "");
    assert.equal(await evaluate("document.getElementById('admitted').value"), "");
  }
  assert.deepEqual(errors, []);
  // Chromium records the date input's built-in data: calendar icon as a request.
  // It never leaves the browser. Every actual network request stays same-origin.
  assert.deepEqual(requests.filter(r => new URL(r).protocol !== "data:" && (new URL(r).origin !== new URL(url).origin || new URL(r).search)), []);
  console.log("Grouping browser passed: desktop/mobile steps, Polish dates, validation, keyboard code search, modifier confirmation, trace, stale response refusal, product refusal, clear and storage privacy.");
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
