# Isolated Conditions Bundle Measurement

## Observed Boundary

On 2026-10-04 the installed API revision
`3f62ca46dd2f5329ad595db212674b5c5878b5dc` opened the full pinned conditions
bundle in an additional isolated container on the validation VPS. The live
Compose file, service container identities, image identities and restart counts
were checked before and after the observation and were unchanged.

The probe used no network or published ports, no image pull, a read-only
filesystem and bundle mount, dropped capabilities, 512 MiB RAM with swap
disabled, one CPU and a 64-process limit. CPU and RAM limits match the live API;
the live API's swap allowance was intentionally not used by this probe.

- Dataset: `cb0cc740f0cf509fd248c4a1f2bde8a3f94691f49d210ce56a2e56891930268b`
- Database: `bbd4bd51597a12ca60a802ebad81d6d8c9ca88b6b0bf04ad98636a734dfba66c`
- Image digest: `4d81ee27e69fef7f87f4782d6cd1215091849b7efecaf0296b4d9d09bb822de1`

## Checked Runner Result

| Observation | Result |
| --- | --- |
| Startup plus first probes | 109.99 s |
| API RSS and observed process high-water mark | 188.88 MiB |
| Container memory after first probes | 212.68 MiB |
| Host available RAM before / during | 1352 / 1173 MiB |
| Warm readiness | 200, 26.42 ms |
| Warm F72 conditions API | 200, 49.15 ms, fully interpreted |
| Warm K40 dated membership page | 200, 53.17 ms |

An earlier successful observation took 96.02 s with 188.71 MiB RSS. These are
individual observations, not a latency distribution or a representative load
benchmark. Cgroup peak memory was unavailable on this host; the report does not
substitute current cgroup usage for a measured cgroup peak.

Initial retries failed to reach readiness within the smaller startup budget.
They did not establish an application crash or OOM. The runner now disables
HTTP proxy discovery for container-loopback requests, reports bounded failure
categories without response bodies, permits a 120-second readiness window,
checks both cold and warm responses, and bounds the outer SSH call.

## Reproduce Without Activation

```sh
agent-scratch --root /tmp/opencode -- \
  uv run python -m scripts.measure_reference \
  --bundle /path/to/verified-bundle \
  --output /path/to/new-measurement.json
```

The output must not already exist. Run only with authorized VPS access and
adequate headroom. Each invocation verifies the local bundle, pins the installed
API digest, allocates an exact owned host directory and container, and removes
the container before its mounted files. A cleanup failure is an error, not a
passing measurement. Host credentials and paths are not included in the receipt.

## Rollout Limits

This proves that this bundle can open and answer sequential reference requests
within these bounds. It does not prove concurrent-load suitability, startup on
a cold host cache, search relevance, legal modifier completeness or eligibility
for an encounter. No public activation or deployment was performed. A future
conditions deployment must allow for the observed startup time and retain the
last known-good bundle until readiness and dated condition probes pass.
