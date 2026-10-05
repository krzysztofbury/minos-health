import { readFile } from "node:fs/promises";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

export async function waitForDevToolsPort(browser, profile, timeoutMs = 60000) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 60000) {
    throw new Error("Chromium port startup timeout is unsupported");
  }
  const deadline = performance.now() + timeoutMs;
  let spawnFailed = false;
  const onError = () => { spawnFailed = true; };
  browser.once("error", onError);
  const requireRunning = () => {
    if (spawnFailed || browser.exitCode !== null || browser.signalCode !== null) {
      throw new Error("Chromium exited before starting CDP");
    }
  };
  try {
    for (let attempt = 0; attempt < 1200 && performance.now() < deadline; attempt++) {
      requireRunning();
      let content;
      try {
        content = await readFile(path.join(profile, "DevToolsActivePort"), "utf8");
      } catch (error) {
        if (error.code !== "ENOENT") throw new Error("Chromium port file could not be read");
      }
      requireRunning();
      if (performance.now() >= deadline) break;
      if (content !== undefined) {
        if (content.length > 4096) throw new Error("Chromium port file is unsupported");
        const newline = content.indexOf("\n");
        // Chrome creates/truncates this file before writing its complete port line.
        // Existence, an empty read or a partial number is not readiness.
        if (newline > 0) {
          const port = content.slice(0, newline);
          if (!/^[1-9][0-9]{0,4}$/.test(port) || Number(port) > 65535) {
            throw new Error("Chromium port file is unsupported");
          }
          return port;
        }
      }
      await sleep(Math.min(50, Math.max(0, deadline - performance.now())));
    }
    throw new Error("Chromium port startup deadline exceeded");
  } finally {
    browser.removeListener("error", onError);
  }
}
