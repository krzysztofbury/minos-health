import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { setTimeout as sleep } from "node:timers/promises";
import { waitForDevToolsPort } from "./devtools-port.mjs";

function browserState() {
  return Object.assign(new EventEmitter(), { exitCode: null, signalCode: null });
}

test("an empty or partial port file is not browser readiness", async () => {
  for (const initial of ["", "12"]) {
    const profile = await mkdtemp(path.join(os.tmpdir(), "minos-port-regression-"));
    const file = path.join(profile, "DevToolsActivePort");
    await writeFile(file, initial);
    const writer = (async () => {
      await sleep(25);
      await writeFile(file, "12345\n/devtools/browser/synthetic\n");
    })();
    try {
      assert.equal(await waitForDevToolsPort(browserState(), profile), "12345");
    } finally {
      await writer;
      await rm(profile, { recursive: true, force: true });
    }
  }
});

test("complete invalid port lines fail without echoing their contents", async () => {
  const profile = await mkdtemp(path.join(os.tmpdir(), "minos-port-invalid-"));
  try {
    for (const content of ["0\n", "65536\n", "PRIVATE_SENTINEL\n", "12.3\n"]) {
      await writeFile(path.join(profile, "DevToolsActivePort"), content);
      await assert.rejects(waitForDevToolsPort(browserState(), profile), {
        message: "Chromium port file is unsupported",
      });
    }
  } finally {
    await rm(profile, { recursive: true, force: true });
  }
});

test("signal or spawn failure stops waiting and removes the error listener", async () => {
  const profile = await mkdtemp(path.join(os.tmpdir(), "minos-port-exit-"));
  try {
    const killed = browserState();
    killed.signalCode = "SIGKILL";
    await assert.rejects(waitForDevToolsPort(killed, profile), /Chromium exited/);
    const failed = browserState();
    const waiting = waitForDevToolsPort(failed, profile);
    failed.emit("error", new Error("PRIVATE_SPAWN_ERROR"));
    await assert.rejects(waiting, { message: "Chromium exited before starting CDP" });
    assert.equal(failed.listenerCount("error"), 0);
  } finally {
    await rm(profile, { recursive: true, force: true });
  }
});

test("missing port times out without leaking the profile", async () => {
  const profile = await mkdtemp(path.join(os.tmpdir(), "minos-port-timeout-"));
  try {
    await assert.rejects(waitForDevToolsPort(browserState(), profile, 30), {
      message: "Chromium port startup deadline exceeded",
    });
  } finally {
    await rm(profile, { recursive: true, force: true });
  }
});

test("a dead browser fails before accepting even a previously valid port", async () => {
  const profile = await mkdtemp(path.join(os.tmpdir(), "minos-port-dead-"));
  try {
    await writeFile(path.join(profile, "DevToolsActivePort"), "12345\n/devtools/browser/synthetic\n");
    const browser = browserState();
    browser.exitCode = 1;
    await assert.rejects(waitForDevToolsPort(browser, profile), /Chromium exited/);
  } finally {
    await rm(profile, { recursive: true, force: true });
  }
});
