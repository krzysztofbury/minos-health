import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const [, , executable, url, output, conditions] = process.argv;
assert.ok(executable && url && output);
const profile = await mkdtemp(path.join(os.tmpdir(), "js-"));
const host = new URL(url).protocol === "http:" ? ["--host-resolver-rules=MAP minoshealth.pl 127.0.0.1"] : [];
const browser = spawn(executable, [...host, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-background-networking", "--disable-extensions", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "pipe", "pipe"] });
browser.stdout.resume(); browser.stderr.resume();
let socket;
try {
  let target;
  for (let attempt = 0; attempt < 1200; attempt++) {
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
  let id = 0, pausedScript;
  const pending = new Map(), errors = [], requests = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id); pending.delete(message.id);
      if (message.error) callback.reject(new Error("browser protocol failed")); else callback.resolve(message.result);
    } else if (message.method === "Runtime.exceptionThrown") errors.push("runtime exception");
    else if (message.method === "Network.requestWillBeSent") requests.push(message.params.request);
    else if (message.method === "Fetch.requestPaused") pausedScript = message.params.requestId;
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const value = ++id; pending.set(value, { resolve, reject }); socket.send(JSON.stringify({ id: value, method, params })); });
  const evaluate = async expression => {
    const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    assert.ok(!result.exceptionDetails, "browser evaluation failed");
    return result.result.value;
  };
  const wait = async expression => {
    for (let attempt = 0; attempt < 300; attempt++) {
      if (await evaluate(expression)) { await evaluate("document.fonts.ready.then(() => true)"); return; }
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error("group search browser deadline exceeded");
  };
  const key = async (name, code) => {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: name, code: name === " " ? "Space" : name, windowsVirtualKeyCode: code, text: name === "Enter" ? "\r" : name === " " ? " " : "" });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: name, code: name, windowsVirtualKeyCode: code });
  };
  const fitsViewport = async (width, context) => {
    const layout = await evaluate(`({ width: document.documentElement.scrollWidth, overflowing: [...document.querySelectorAll("main *")].filter(node => node.getBoundingClientRect().right > ${width}).slice(0, 8).map(node => ({tag: node.tagName, class: node.className, right: Math.round(node.getBoundingClientRect().right)})) })`);
    assert.ok(layout.width <= width, `${context}: ${JSON.stringify(layout)}`);
  };
  await send("Page.enable"); await send("Runtime.enable"); await send("Network.enable");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
    await send("Page.navigate", { url: "about:blank" });
    await wait('location.href === "about:blank" && !document.querySelector("#query")');
    pausedScript = undefined;
    await send("Fetch.enable", { patterns: [{ urlPattern: "*/search.js", requestStage: "Request" }] });
    await send("Page.navigate", { url: new URL("/", url).href });
    await wait('Boolean(document.querySelector("#query"))');
    for (let attempt = 0; !pausedScript && attempt < 300; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(pausedScript, "Search script must be held before it initializes");
    assert.ok(await evaluate('document.querySelector("#query").disabled && document.querySelector("#search-submit").disabled'), "Search must stay disabled until its script and release metadata are ready");
    await send("Fetch.continueRequest", { requestId: pausedScript });
    await send("Fetch.disable");
    await wait('document.querySelector("#query") && !document.querySelector("#query").disabled');
    assert.equal(await evaluate('document.querySelector("input[value=jgp]").disabled'), false);
    // Focus, Space, Tab and Enter exercise native chip and result navigation.
    await evaluate('document.querySelectorAll("input[name=system]").forEach(box => { if (box.checked) box.click(); }); document.querySelector("input[value=jgp]").focus()');
    await key(" ", 32);
    assert.equal(await evaluate('document.querySelector("input[value=jgp]").checked'), true);
    await evaluate('document.querySelector("#query").focus()');
    await send("Input.insertText", { text: "H21" });
    await wait('!document.querySelector("#suggestions").hidden && document.querySelector("#suggestions").textContent.includes("H21")');
    await key("Escape", 27);
    assert.equal(await evaluate('document.querySelector("#query").value'), "H21", "Closing suggestions must preserve the query");
    await key("Enter", 13);
    await wait('document.querySelector("#group-jgp") && document.querySelector("#group-jgp").textContent.includes("stan na") && !document.querySelector("#search-submit").disabled');
    const heading = await evaluate('document.querySelector("#group-jgp").textContent');
    const [, dd, mm, yyyy] = heading.match(/stan na (\d\d)\.(\d\d)\.(20\d\d)/);
    const day = `${yyyy}-${mm}-${dd}`;
    assert.equal(await evaluate('document.querySelector(".result").getAttribute("href")'), `/jgp/${day}/H21`);
    assert.ok(await evaluate('document.querySelector(".result-meta").textContent.includes("1a") && document.querySelector(".result-meta").textContent.includes("DSOZ")'));
    assert.equal(await evaluate('document.querySelector(".result-group-foot a").getAttribute("href")'), `/jgp/${day}`);
    assert.equal(await evaluate('Boolean(document.querySelector("input[type=date], select[name=catalog], select[name=scope]"))'), false);
    await fitsViewport(width, "Home viewport");
    const image = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(output, `jgp-search-${name}.png`), Buffer.from(image.data, "base64"));
    await evaluate('document.querySelector(".result").focus()');
    await key("Tab", 9);
    assert.equal(await evaluate('document.activeElement.textContent'), "Więcej opcji");
    await evaluate('document.querySelector(".result").focus()');
    await key("Enter", 13);
    await wait(`location.pathname === "/jgp/${day}/H21" && document.querySelector(".jgp-date")`);
    await fitsViewport(width, "Group viewport");
    if (conditions === "conditions") {
      await wait('document.querySelector(".jgp-conditions")');
      const conditionsImage = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      await writeFile(path.join(output, `jgp-search-conditions-${name}.png`), Buffer.from(conditionsImage.data, "base64"));
      const listLink = await evaluate("document.querySelector('.jgp-rules a[href*=\"/listy/\"]').getAttribute('href')");
      assert.ok(listLink.startsWith(`/jgp/${day}/listy/`));
      await send("Page.navigate", { url: new URL(listLink, url).href });
      await wait(`location.pathname === ${JSON.stringify(listLink)} && document.querySelector(".code-list")`);
      const codeLink = await evaluate("document.querySelector('.code-list a[href*=\"/slowniki/\"]').getAttribute('href')");
      assert.ok(codeLink.endsWith(`/jgp/${day}`));
      await send("Page.navigate", { url: new URL(codeLink, url).href });
      await wait(`location.pathname === ${JSON.stringify(codeLink)} && document.querySelector(".jgp-memberships")`);
      await fitsViewport(width, "Reverse lookup viewport");
      assert.ok(await evaluate(`Array.from(document.querySelectorAll(".jgp-memberships a")).some(link => link.getAttribute("href") === "/jgp/${day}/H21")`));
      const reverseImage = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      await writeFile(path.join(output, `jgp-search-reverse-${name}.png`), Buffer.from(reverseImage.data, "base64"));
    }
  }
  assert.deepEqual(errors, []);
  const queries = requests.filter(request => request.url.endsWith("/api/v1/search"));
  assert.ok(queries.length);
  assert.ok(queries.every(request => request.method === "POST" && JSON.parse(request.postData).system === "jgp"));
  assert.ok(requests.every(request => !new URL(request.url).search));
  assert.deepEqual(await evaluate('Object.keys(localStorage)'), ['minos.search.filters.v1']);
  const preference = await evaluate('JSON.parse(localStorage.getItem("minos.search.filters.v1"))');
  assert.deepEqual(Object.keys(preference).sort(), ['expires_at_ms', 'systems', 'version']);
  assert.equal(preference.version, 1);
  assert.deepEqual(preference.systems, ['jgp']);
  const now_ms = await evaluate('Date.now()');
  assert.ok(Number.isSafeInteger(preference.expires_at_ms));
  assert.ok(preference.expires_at_ms > now_ms && preference.expires_at_ms <= now_ms + 90 * 24 * 60 * 60 * 1000);
  assert.equal(await evaluate("Boolean(sessionStorage.length || document.cookie)"), false);
  console.log("JGP search browser passed: desktop/mobile, exact result, dated links, catalog/act, keyboard chips/results, no overflow or query URLs/storage; only filter preferences persist.");
} finally {
  if (socket) socket.close();
  browser.kill("SIGTERM");
  await Promise.race([new Promise(resolve => { if (browser.exitCode !== null || browser.signalCode !== null) resolve(); else browser.once("exit", resolve); }), new Promise(resolve => setTimeout(resolve, 3000))]);
  if (browser.exitCode === null && browser.signalCode === null) {
    browser.kill("SIGKILL");
    await new Promise(resolve => browser.once("exit", resolve));
  }
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
