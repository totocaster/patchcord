# `circremote` 0.12.0 acceptance record

Status: **rejected for bounded execution**

Patchcord evaluated the documented `circremote` CLI as the backend for bounded
`repl --eval`, `repl --file`, and connected probes. Explicit serial targeting,
timeouts, and Patchcord-owned output framing appeared implementable through the
public CLI.

The isolation requirement did not pass. `circremote` 0.12.0 creates and reads
`~/.circremote` before parsing the command-line `--config` option. A
Patchcord-supplied temporary configuration therefore cannot prevent persistent
configuration changes or user configuration from influencing the invocation.
An invalid user configuration can also fail the command before the isolated
configuration is selected.

The run also contaminated board stdout with tool warnings and did not establish
the required reset behavior. The upstream CLI waits ten seconds before its
final reset, so the tested parent timeout could terminate it first on timeout
or traceback paths.

An upstream test run on macOS with Python 3.14 produced 379 passes, 14 failures,
and 24 skips. Serial-timeout coverage was skipped, and the evaluation did not
establish a complete hardware or cross-platform matrix.

Patchcord consequently blocks this exact backend version and reports the
affected capability as unavailable. Execution uses a default-deny exact-version
allowlist, so an unreviewed future release will remain unavailable too. This is
a capability gate, not permission to add a private/raw-REPL implementation.

Re-evaluate a newer `circremote` release when it can honor an explicit
configuration before reading or creating the default user configuration. The
new version must then pass the isolation, timeout, output-framing,
cross-platform, and representative-hardware checks in [SPEC.md](../SPEC.md).
