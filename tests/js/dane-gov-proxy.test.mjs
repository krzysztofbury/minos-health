// The minos-dane-gov Worker (#349), run under Node with an injected upstream.
import assert from "node:assert/strict";
import { timingSafeEqual } from "node:crypto";
import test from "node:test";

import { createHandler } from "../../infra/cloudflare/dane-gov-proxy/worker.mjs";

const KEY = "k".repeat(43);
const ORIGIN = "https://dane-gov.worker.test";
const LISTING = "/1.4/datasets/2581/resources?per_page=20&sort=-data_date";
const ENV = { PROXY_KEY: KEY, ALLOWED_COLOS: "WAW" };
const BODY = JSON.stringify({ data: [] });

function request(path = LISTING, { key = KEY, method = "GET", colo = "WAW" } = {}) {
  const headers = new Headers();
  if (key !== null) {
    headers.set("x-minos-proxy-key", key);
  }
  const built = new Request(ORIGIN + path, { method, headers });
  Object.defineProperty(built, "cf", { value: colo === null ? undefined : { colo } });
  return built;
}

function upstream(response) {
  const calls = [];
  const fetchUpstream = async (url, init) => {
    calls.push({ url, init });
    return typeof response === "function" ? response(init) : response.clone();
  };
  return { calls, handle: createHandler({ fetchUpstream, timingSafeEqual, timeoutMs: 50 }) };
}

const OK = new Response(BODY, {
  status: 200,
  headers: {
    "content-type": "application/vnd.api+json",
    "last-modified": "Tue, 10 Nov 2026 10:00:00 GMT",
    etag: '"abc"',
    "set-cookie": "session=1",
    server: "nginx",
    "x-upstream": "1",
  },
});

test("the listing passes through with only the selected headers", async () => {
  const { calls, handle } = upstream(OK);
  const response = await handle(request(), ENV);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), BODY);
  assert.deepEqual(
    [...response.headers.keys()].sort(),
    ["cache-control", "content-type", "etag", "last-modified", "x-minos-colo"],
  );
  assert.equal(response.headers.get("x-minos-colo"), "WAW");
  assert.equal(response.headers.get("cache-control"), "no-store, no-transform");
  assert.equal(calls.length, 1);
  const [{ url, init }] = calls;
  assert.equal(url, "https://api.dane.gov.pl" + LISTING);
  assert.equal(init.redirect, "manual");
  assert.equal(init.headers["accept-encoding"], "identity");
  assert.equal(init.headers["user-agent"], "minos-health/0.0.1 dane-gov-watcher");
  // The key and any caller header never travel upstream.
  assert.equal(JSON.stringify(init).includes(KEY), false);
  assert.equal("cookie" in init.headers, false);
});

for (const [name, key, env] of [
  ["missing key", null, ENV],
  ["wrong key", "x".repeat(43), ENV],
  ["wrong length", KEY + "x", ENV],
  ["unset secret", "", { ALLOWED_COLOS: "WAW" }],
  ["short secret", "short", { PROXY_KEY: "short", ALLOWED_COLOS: "WAW" }],
]) {
  test(`403 without an upstream call: ${name}`, async () => {
    const { calls, handle } = upstream(OK);
    const response = await handle(request(LISTING, { key }), env);
    assert.equal(response.status, 403);
    assert.equal(await response.text(), "");
    assert.equal(calls.length, 0);
  });
}

for (const [name, path, method] of [
  ["other path", "/1.4/datasets/2581", "GET"],
  ["other query", "/1.4/datasets/2581/resources?per_page=100&sort=-data_date", "GET"],
  ["extra query", LISTING + "&x=1", "GET"],
  ["no query", "/1.4/datasets/2581/resources", "GET"],
  ["other method", LISTING, "POST"],
  ["traversal", "/1.4/datasets/2581/resources/../../../x" + LISTING.slice(LISTING.indexOf("?")), "GET"],
]) {
  test(`404 without an upstream call: ${name}`, async () => {
    const { calls, handle } = upstream(OK);
    const response = await handle(request(path, { method }), ENV);
    assert.equal(response.status, 404);
    assert.equal(calls.length, 0);
  });
}

test("a location outside the allowlist answers 503 at once", async () => {
  for (const colo of ["HEL", "FRA", null, "waw"]) {
    const { calls, handle } = upstream(OK);
    const response = await handle(request(LISTING, { colo }), ENV);
    assert.equal(response.status, 503);
    assert.equal(calls.length, 0);
  }
  const { handle } = upstream(OK);
  const response = await handle(request(LISTING, { colo: "HEL" }), {
    ...ENV,
    ALLOWED_COLOS: "WAW, HEL",
  });
  assert.equal(response.status, 200);
});

test("an upstream timeout or network error answers 504", async () => {
  const hang = (init) =>
    new Promise((_, reject) => {
      init.signal.addEventListener("abort", () => reject(new Error("aborted")));
    });
  for (const behaviour of [hang, () => Promise.reject(new TypeError("network"))]) {
    const { handle } = upstream(behaviour);
    const response = await handle(request(), ENV);
    assert.equal(response.status, 504);
    assert.equal(response.headers.get("x-minos-colo"), "WAW");
  }
});

test("upstream refusals and redirects pass through without their headers", async () => {
  for (const status of [301, 403, 404, 500, 503]) {
    const { handle } = upstream(
      new Response(status === 301 ? null : "no", {
        status,
        headers: { location: "https://elsewhere.test/", "set-cookie": "a=1" },
      }),
    );
    const response = await handle(request(), ENV);
    assert.equal(response.status, status);
    assert.equal(response.headers.get("location"), null);
    assert.equal(response.headers.get("set-cookie"), null);
  }
});

test("an oversized upstream body is not relayed", async () => {
  const { handle } = upstream(new Response("x".repeat(1024 * 1024 + 1)));
  const response = await handle(request(), ENV);
  assert.equal(response.status, 502);
});
