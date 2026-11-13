// minos-dane-gov: a Polish egress for one dane.gov.pl listing (#349).
//
// Since 2026-11-10 dane.gov.pl answers only Polish source addresses (#344).
// A Worker runs in the Cloudflare location the caller's traffic lands on, and
// its subrequest leaves from there. This Worker answers exactly one request,
// the NFZ watcher's read of the dataset 2581 listing, and nothing else: it is
// not an open proxy. It never logs, and never echoes the key.

const UPSTREAM =
  "https://api.dane.gov.pl/1.4/datasets/2581/resources?per_page=20&sort=-data_date";
const PATH = "/1.4/datasets/2581/resources";
const QUERY = "?per_page=20&sort=-data_date";
const KEY_HEADER = "x-minos-proxy-key";
const COLO_HEADER = "x-minos-colo";
const USER_AGENT = "minos-health/0.0.1 dane-gov-watcher";
const PASSED_HEADERS = ["content-type", "last-modified", "etag"];
const NULL_BODY = new Set([101, 204, 205, 304]);
const TIMEOUT_MS = 20_000;
// A resource guard only; the watcher enforces its own 256 KiB limit.
const MAX_BYTES = 1024 * 1024;
const MIN_KEY_BYTES = 32;

function answer(status, colo, headers = new Headers(), body = null) {
  headers.set("cache-control", "no-store, no-transform");
  if (colo !== null) {
    headers.set(COLO_HEADER, colo);
  }
  return new Response(NULL_BODY.has(status) ? null : body, { status, headers });
}

function keyMatches(given, expected, timingSafeEqual) {
  const encoder = new TextEncoder();
  const left = encoder.encode(given);
  const right = encoder.encode(expected);
  // Never return early on a length difference: compare the input with itself
  // and negate, so the time does not depend on where the inputs differ.
  return left.byteLength === right.byteLength
    ? timingSafeEqual(left, right)
    : !timingSafeEqual(left, left);
}

function allowedColos(value) {
  return new Set(
    String(value ?? "")
      .split(",")
      .map((item) => item.trim())
      .filter((item) => /^[A-Z]{3}$/.test(item)),
  );
}

export function createHandler({
  fetchUpstream = (url, init) => fetch(url, init),
  timingSafeEqual = (left, right) => crypto.subtle.timingSafeEqual(left, right),
  timeoutMs = TIMEOUT_MS,
} = {}) {
  return async function handle(request, env) {
    const reported = request.cf?.colo;
    const colo = typeof reported === "string" && /^[A-Z]{3}$/.test(reported)
      ? reported
      : null;
    const expected = typeof env?.PROXY_KEY === "string" ? env.PROXY_KEY : "";
    const given = request.headers.get(KEY_HEADER) ?? "";
    // The key comes first, so an unauthenticated caller cannot even learn
    // which path is served. A missing Worker secret matches no key.
    if (
      new TextEncoder().encode(expected).byteLength < MIN_KEY_BYTES ||
      !keyMatches(given, expected, timingSafeEqual)
    ) {
      return answer(403, colo);
    }
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== PATH || url.search !== QUERY) {
      return answer(404, colo);
    }
    // Outside the allowlisted locations dane.gov.pl may refuse the call; fail
    // fast with a retryable answer instead of after a 20 s hang.
    if (colo === null || !allowedColos(env?.ALLOWED_COLOS).has(colo)) {
      return answer(503, colo);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let upstream;
    let body;
    try {
      upstream = await fetchUpstream(UPSTREAM, {
        method: "GET",
        headers: {
          accept: "application/vnd.api+json",
          "accept-encoding": "identity",
          "user-agent": USER_AGENT,
        },
        redirect: "manual",
        cache: "no-store",
        signal: controller.signal,
      });
      // Read the whole body under the same deadline. Any transfer coding the
      // runtime negotiated is already decoded here, so the answer carries
      // identity bytes and no content-encoding.
      body = await upstream.arrayBuffer();
    } catch {
      return answer(504, colo);
    } finally {
      clearTimeout(timer);
    }
    if (body.byteLength > MAX_BYTES) {
      return answer(502, colo);
    }
    const headers = new Headers();
    for (const name of PASSED_HEADERS) {
      const value = upstream.headers.get(name);
      if (value !== null) {
        headers.set(name, value);
      }
    }
    // content-length is set by the runtime from the exact bytes returned;
    // set-cookie and every other upstream header are dropped.
    return answer(upstream.status, colo, headers, body);
  };
}

export default { fetch: createHandler() };
