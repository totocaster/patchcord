# Patchcord v0.1 Specification

Patchcord is an agent-facing CLI control plane for humans and coding agents to
work with CircuitPython boards without a dedicated IDE. It provides one safe,
stable interface for board discovery, deployment, serial feedback, REPL
probes, library management, and hardware-description validation. Patchcord
coordinates existing CircuitPython tools; it is not a replacement for them.

## Scope

Patchcord v0.1 supports CircuitPython's USB mass-storage and USB serial
workflow. The local project is always the source of truth; the removable
`CIRCUITPY` drive is only a deployment target.

Patchcord can:

- identify selected CircuitPython drives and serial ports and report available
  board and firmware metadata;
- deploy board files from a Git-managed local project;
- stream serial output to the terminal and a persistent host-side log;
- interrupt, reset, and open an interactive REPL;
- run bounded code, list board pins, and scan the default I²C bus when an
  execution backend has passed the acceptance gate;
- install and freeze board libraries through `circup`; and
- validate a structured `hardware.yaml` offline and, when an execution backend
  is accepted, against a connected board.

## Project layout

```text
.
├── device/              # Files deployed to CIRCUITPY
│   └── code.py
├── hardware.yaml        # Canonical board, parts, nets, and project notes
├── requirements.txt     # CircuitPython libraries installed on the board
└── .patchcord/          # Logs, locks, and state; gitignored
```

## General behavior

- Automatic selection never chooses among multiple CircuitPython drives or
  multiple serial ports. Depending on the command, a mount, a port, or both may
  be required; initialization and connected validation inspect optional target
  components as well.
- `--mount PATH` and `--port PATH` override automatic selection.
- Patchcord v0.1 does not prove that a selected USB drive and serial port belong
  to the same physical board. When more than one candidate exists, the user
  must provide the appropriate override or overrides.
- Bounded data-producing commands support `--json`; human-readable output is
  the default. Interactive REPL sessions do not support JSON output.
- Only one Patchcord process in a project may own its selected serial port at a
  time. Projects are expected to be dedicated to one board workflow.
- Commands return a non-zero status for discovery, validation, transport,
  deployment, or board-execution failures.
- Patchcord-owned operations never interpret or print credentials or the
  contents of `settings.toml`. With `deploy --allow-settings`, Patchcord copies
  that file as opaque bytes and omits its digest and size from results. Explicit
  code run through `repl --eval`, `repl --file`, or an interactive REPL is
  trusted user code; its arbitrary output cannot be guaranteed secret-free and
  is captured like other board output.

## Implementation and dependency policy

Patchcord is a thin orchestration layer, not a new CircuitPython toolchain. It
coordinates established tools and adds only the project-level workflow they do
not provide together: selecting explicit target components, coordinating drive
and serial operations, safe deployment, persistent logs, agent-friendly JSON,
and `hardware.yaml`.

Patchcord must use maintained public APIs or documented CLIs where available.
It must not vendor their code, depend on private internals, or reimplement their
catalogs and compatibility logic. Dependencies are locked in `uv.lock`, and
diagnostic output includes Patchcord, Python, CircuitPython, and relevant
dependency versions.

### Adapter-first rule

Every capability that communicates with a board or interprets CircuitPython
metadata must be implemented behind an adapter. Before writing a new adapter
implementation, the maintainer must evaluate existing implementations in this
order:

1. the standard CircuitPython workflow or an official CircuitPython/Adafruit
   tool;
2. a maintained CircuitPython-specific tool with a documented CLI or public
   API;
3. a maintained general-purpose library for the underlying transport or file
   operation; and
4. a Patchcord-owned implementation only when the preceding choices cannot
   satisfy the required safety, portability, or machine-interface contract.

Patchcord-owned code may compose operations, enforce policy, normalize results,
and add durable evidence. It must not duplicate a specialized protocol,
catalog, dependency solver, terminal emulator, device database, or hardware
knowledge base merely to avoid an external process or dependency.

An exception at step 4 must be recorded in an architecture decision before the
implementation is merged. The decision must name:

- the capability and required behavior;
- the upstream tools and versions evaluated;
- the documented CLI or public API tested;
- the unsupported, unsafe, or non-portable behavior that prevented reuse;
- the smallest Patchcord-owned surface needed;
- conformance tests that prevent the exception from expanding into a competing
  general-purpose tool; and
- a condition for re-evaluating the decision when an upstream tool changes.

The absence of JSON output alone does not justify reimplementing an upstream
tool. An adapter may execute a documented CLI and normalize its exit status,
stdout, and stderr. For structured board probes, Patchcord should execute a
controlled local probe program that emits a Patchcord-defined framed JSON
record, rather than parse human-oriented or localized prose. If reliable
framing is impossible, the adapter must fail with an unsupported-capability
error instead of guessing.

Adapters must be narrow and replaceable:

- The Patchcord command layer depends on typed adapter interfaces, not on an
  upstream package's private objects or output wording.
- An adapter invokes an explicit mount or serial port chosen by Patchcord. It
  must not independently select "the first" connected device.
- User-level configuration, aliases, search paths, remote URLs, and implicit
  package installation in an upstream tool must be disabled unless the
  Patchcord command explicitly requires them.
- Patchcord exposes an allowlisted operation, not an arbitrary passthrough to
  an upstream CLI. Explicit code supplied to `repl --eval` or `repl --file` is
  permitted input to that operation, but it does not grant access to unrelated
  upstream subcommands. Upstream operations that can erase files, flash
  firmware, reveal settings, or fetch and execute remote code are never
  reachable accidentally through Patchcord.
- Every external operation has a timeout where bounded execution is expected,
  captures exit status and both output streams, and maps failures to stable
  Patchcord error codes.
- An adapter must not silently fall back to a different backend or a custom
  protocol implementation. Missing or incompatible backends are reported
  explicitly.
- Adapter versions and selected capabilities are included in `patchcord
  doctor --json` and relevant diagnostic metadata.
- Upstream stdout, stderr, command names, and exception classes are diagnostic
  details, not Patchcord's public machine interface. Agents branch on
  Patchcord's stable result and error codes.

### Agent-facing result contract

An upstream CLI never inherits Patchcord's stdout. Patchcord captures the child
process and converts its result into internal typed records before rendering
human or machine output. This prevents progress bars, prompts, warnings, and
upstream wording changes from corrupting the agent interface.

For `--json`, stdout contains exactly one JSON document. Human diagnostics and
interactive prompts go to stderr and are not emitted when the command is
declared non-interactive. The top-level JSON shape is:

```json
{
  "schema_version": 1,
  "command": "probe i2c",
  "ok": true,
  "target": {
    "board_id": "adafruit_feather_rp2040",
    "mount": "/Volumes/CIRCUITPY",
    "port": "/dev/cu.usbmodem1101"
  },
  "result": {},
  "errors": [],
  "diagnostics": {
    "backend": "circremote",
    "backend_version": "..."
  }
}
```

The exact fields inside `result` are command-specific. `errors` contains stable
Patchcord codes and structured details. `diagnostics` may identify the selected
adapter and version for reproducibility, but callers must not need to parse raw
upstream stdout or branch on an upstream message. Target fields that do not
apply or are unavailable are `null`.

Raw upstream tool diagnostics may be retained in a host-side operation log when
useful for diagnosis, after removing known credentials and control data. They
are never copied blindly into JSON, and command lines containing credentials
are never logged. Board stdout is different: it is user-program output and is
recorded verbatim in the serial log. Patchcord does not claim that arbitrary
user code or board output is secret-free.

### Adapter acceptance gate

A preferred upstream backend becomes a runtime foundation only after an
acceptance test demonstrates that it:

- can be forced to use Patchcord's selected target;
- behaves consistently on supported macOS, Linux, and Windows hosts;
- supports bounded timeouts and returns control after failure;
- does not perform undeclared dependency installation, remote code retrieval,
  file deletion, firmware flashing, or configuration mutation;
- permits Patchcord to separate board output from tool diagnostics without
  parsing unstable prose;
- leaves the serial port and board in a documented state on success, timeout,
  user interruption, and traceback; and
- passes unit tests with a fake transport plus hardware tests against the
  supported CircuitPython-version matrix.

Failure of an acceptance gate blocks the affected Patchcord capability until an
alternative adapter is selected or a step-4 exception is documented. It is not
implicit permission to implement a raw REPL client.

### Required foundations

| Tool or library | Responsibility in Patchcord |
|---|---|
| CircuitPython USB workflow | Authoritative board filesystem, `boot_out.txt`, serial console, REPL, and runtime `board` module |
| `adafruit-board-toolkit` | Identification of CircuitPython REPL and data serial ports using Adafruit-maintained device knowledge |
| `circup` | CircuitPython library discovery, dependency resolution, bundle and board-version compatibility, installation, updates, and freeze |
| `pyserial` | Serial metadata and transport, serial I/O, control characters, timeouts, and the `serial.tools.miniterm` interactive terminal |
| `psutil` | Portable enumeration of mounted filesystems before checking for standard CircuitPython drive markers |
| `typer` | CLI command tree, option parsing, help, and shell completion |
| `pydantic` | Typed internal models, `hardware.yaml` validation, JSON serialization, and generated schema |
| `ruamel.yaml` | Strict YAML 1.2 parsing; future manifest-editing commands must use its round-trip support |
| `appdirs` | Locate circup's user-level bundle configuration so Patchcord can refuse an invocation it cannot isolate |
| `filelock` | Cross-platform project and serial-port ownership locks |
| `rich` | Human-readable status, tables, progress, and errors; never used for `--json` output |
| Python standard library | File copying, checksums, timestamps, subprocesses, paths, and persistent text logs |
| `uv` and `uv_build` | Python installation, virtual environment, dependencies, lockfile, command execution, testing tools, and package builds |

Development uses `pytest` for tests, `ruff` for formatting and linting, and
`basedpyright` for static type checking, all run through `uv`.

All Python foundations, including `circup`, are declared as project
dependencies and installed by `uv`; users do not install them individually.
`circup` should be invoked through its documented CLI so its board and bundle
support can evolve independently of Patchcord. Patchcord may use the public
APIs of `adafruit-board-toolkit` and `pyserial`, including terminal components,
rather than implementing device-recognition tables, a serial stack, or a
terminal emulator.

### Capability ownership and preferred adapters

| Capability | Upstream or Patchcord owner | v0.1 rule |
|---|---|---|
| USB filesystem, serial console, REPL, `boot_out.txt`, and runtime pin aliases | CircuitPython | Treat the running board as authoritative; do not reproduce firmware behavior or pin maps. |
| CircuitPython serial-port recognition | `adafruit-board-toolkit` | Use its public port-enumeration API; Patchcord filters and selects an explicit serial target. |
| Raw serial transport and control characters | `pyserial` | Use its public API. Patchcord may send the documented `Ctrl-C` and `Ctrl-D` control operations but must not grow this code into a raw-REPL execution engine. |
| Interactive console | `serial.tools.miniterm` | Invoke the documented terminal CLI. Patchcord owns target selection, port locking, and session metadata, not terminal emulation. v0.1 does not capture the interactive transcript. |
| Bounded code and file execution | `circremote`, subject to the adapter acceptance gate | Prefer its documented CLI with an explicit serial port and isolated configuration. If it fails the gate, report the capability as unavailable until another adapter or documented exception is approved. |
| Pin enumeration and I²C scanning | Controlled Patchcord probe programs executed by the bounded-execution adapter | Patchcord owns the small, deterministic probe payload and JSON schema; the execution adapter owns REPL transport. Do not maintain board or device catalogs. |
| CircuitPython library resolution and installation | `circup` | Invoke its documented CLI with the explicit selected mount. Patchcord must not solve dependencies or select bundle artifacts. |
| Deployment copy | Patchcord's deliberately small mounted-filesystem adapter | Enforce Patchcord's protected-file, copy-order, and non-deletion policy. Do not build a general synchronization or remote-filesystem engine. |
| Deployment operation ordering and provenance | Patchcord | These are cross-tool safety and project-workflow responsibilities. Git is the recovery mechanism for the local `device/` source. |
| Mount/serial selection and device ambiguity | Patchcord using upstream metadata | Select each required target component explicitly or only when independently unambiguous; never let a child tool choose a different device. Physical drive/port correlation is not implemented in v0.1. |
| Persistent logs, traceback classification, JSON result envelopes, and host-side JSONL operation evidence | Patchcord | Normalize upstream behavior into a stable agent contract and retain enough evidence for a later agent to diagnose a run. |
| Project wiring and expected interfaces | `hardware.yaml` and Patchcord validation | Keep project-local integration facts only; defer board pins, library metadata, and component catalogs to their authoritative sources. |

### Bounded-execution adapter

`circremote` is the candidate v0.1 backend for `repl --eval`, `repl --file`,
and connected probes because it already executes CircuitPython code over serial
and supports CircuitPython-specific utility commands. The pinned 0.12.0 release
failed the acceptance gate, and the implementation's exact-version allowlist is
currently empty. Bounded execution and connected probes therefore report
`execution_backend_unavailable`.

When the `circremote` adapter is enabled, it must:

- pass the serial port selected and locked by Patchcord;
- use an isolated configuration so user aliases, device defaults, and search
  paths cannot change an operation;
- use the documented `--config`, `--skip-circup`, `--timeout`, and `--quiet`
  options as applicable; the isolated configuration contains no device aliases,
  command aliases, remote search paths, or variable defaults;
- disable `circremote`'s automatic `circup` integration because Patchcord's
  library adapter owns package changes, and treat any unexpected interactive
  prompt as a failure rather than auto-confirming it;
- run only a packaged Patchcord probe or a temporary local script materialized
  from the code explicitly supplied to `repl --eval` or `repl --file`;
- disable URL and GitHub command loading;
- never expose destructive commands or commands that read
  `settings.toml`;
- frame the script's result with a versioned Patchcord sentinel and JSON
  payload so tool commentary cannot be mistaken for board data;
- enforce a Patchcord timeout and capture tool diagnostics separately from the
  framed board result; and
- reset and release the board according to the calling Patchcord command's
  documented postcondition.

Patchcord must not import undocumented `circremote` internals. It may invoke the
documented CLI as a subprocess. A future public API may replace the subprocess
adapter after passing the same acceptance tests.

### Deployment adapter decision

Existing deployment tools were evaluated before the mounted-filesystem adapter
was implemented. For v0.1, `circdeploy` is not the deployment backend because
its documented contract does not provide Patchcord's protected-file gating,
support-files-before-`code.py` ordering, selected mount plus command-layer
serial coordination, reset, startup capture, and traceback classification. Its
default behavior can also delete unmatched Python files. Wrapping it with
non-deleting options does not remove the need for Patchcord to implement and
verify the safety-critical ordering around the copy.

The permitted Patchcord implementation is consequently limited to ordinary
filesystem operations against the already selected CircuitPython mount:

- calculate the copy plan and checksums;
- enforce protected paths and the non-deletion rule;
- create directories and copy support files;
- copy `code.py` last;
- flush and close host-side file handles before reset; and
- return a typed manifest of created, updated, skipped, and refused paths.

It must not implement bidirectional synchronization, ignore-file semantics,
remote filesystem protocols, dependency installation, or a generalized deploy
framework. If a maintained upstream backend later satisfies Patchcord's safety
contract, adapter-first policy requires re-evaluating this exception.

The complete versioned evaluation and conformance boundary are recorded in
[`architecture/deployment-filesystem-exception.md`](architecture/deployment-filesystem-exception.md).

Other broad deployment/execution projects may be assessed through an
architecture decision, but experimental, pre-alpha, non-public-source, or
host-incompatible packages cannot be required foundations. MicroPython tools
such as `mpremote` or `pyboard.py` are not assumed to support CircuitPython
unless the exact CircuitPython behavior is documented and passes Patchcord's
acceptance tests.

### Current upstream evaluation record

This table records the implemented v0.1 decisions; versions and capabilities
must be checked again before each major release.

| Project | Decision | Reason |
|---|---|---|
| [`circup`](https://github.com/adafruit/circup) | Adopt | It is the upstream CircuitPython library manager and owns bundle, dependency, and firmware-version compatibility. |
| [`adafruit-board-toolkit`](https://github.com/adafruit/Adafruit_Board_Toolkit) | Adopt | It provides Adafruit-maintained CircuitPython serial-port recognition without requiring a Patchcord device table. |
| [`pyserial`](https://github.com/pyserial/pyserial) | Adopt | It provides the portable serial transport, port metadata, timeouts, and reusable miniterm component. |
| [`circremote`](https://github.com/romkey/circremote) | Reject 0.12.0; default-deny other versions | Version 0.12.0 reads and creates user configuration before honoring `--config`, contaminates board output, and did not satisfy reset/timeout requirements. See [`architecture/circremote-0.12.0-acceptance.md`](architecture/circremote-0.12.0-acceptance.md). |
| [`circdeploy`](https://pypi.org/project/circdeploy/) | Do not adopt for v0.1 deployment | Its documented copy/delete contract lacks Patchcord's protected-file policy, copy ordering, explicit target control, reset, capture, and agent result contract. Re-evaluate if those guarantees are added. |
| [`Piku`](https://pypi.org/project/piku/) | Do not adopt as a foundation | Its broad project/deploy/serial workflow overlaps Patchcord but carries early-development and destructive-deployment warnings and does not provide the required agent contract. |
| [`chumicro-deploy-experimental`](https://pypi.org/project/chumicro-deploy-experimental/) | Track, do not require | Its programmatic deployment and recovery model is relevant, but the experimental channel is pre-alpha and does not currently satisfy Patchcord's native-Windows portability requirement. Re-evaluate the stable, publicly auditable package when available. |
| [`mpremote`](https://docs.micropython.org/en/latest/reference/mpremote.html) and [`pyboard.py`](https://docs.micropython.org/en/latest/reference/pyboard.py.html) | Do not assume compatibility | They are MicroPython tools. They remain candidates only if their required operations are documented and verified against the supported CircuitPython matrix. |

### Board-support boundary

- Patchcord does not maintain a board database, VID/PID registry, pin map,
  library catalog, or copy of the Adafruit or Community CircuitPython bundles.
- Board identity and firmware come from the connected board's `boot_out.txt`,
  USB/serial metadata, and bounded REPL queries.
- CircuitPython serial-port recognition comes from `adafruit-board-toolkit`;
  raw serial access comes from `pyserial`.
- Drive and serial candidates are selected independently. v0.1 does not
  correlate them by USB location or physical-device identity.
- Pin names come from the board's runtime `board` module. They are not copied
  into Patchcord.
- Library and CircuitPython-version compatibility comes from `circup` and its
  upstream bundles.
- A board that implements the standard USB drive and serial workflow should
  work without a Patchcord release. A board not yet recognized upstream can
  still be selected explicitly with `--mount` and `--port`.
- Multiple candidates for a target component required by a command require
  explicit selection.
- If upstream tooling does not recognize a board or library, Patchcord reports
  the unsupported capability and dependency version. Board-specific exceptions
  belong upstream, not in Patchcord.

## Commands

### `patchcord init [PATH] [--json]`

Creates `device/code.py`, `hardware.yaml`, `requirements.txt`, and the required
`.gitignore` entries. If an unambiguous CircuitPython drive is selected, records
its board ID. Existing project files are preserved. Implicit discovery
ambiguity leaves the board ID empty; an invalid explicit override is an error.

### `patchcord status [--json]`

Reports the selected board's ID, name, CircuitPython version, mount path, serial
port, and available storage.

### `patchcord doctor [--json]`

Performs read-only host and project diagnostics. It reports Patchcord, Python,
CircuitPython when connected, and adapter versions; available adapter
capabilities; mount and serial permissions; project-file validity; and any
acceptance-gate failure that makes a command unavailable. It never installs,
updates, repairs, or silently switches a dependency. Its JSON uses stable
Patchcord capability and error codes rather than requiring an agent to parse an
upstream version string.

### `patchcord deploy [--capture SECONDS] [--allow-boot] [--allow-settings] [--json]`

Deploys `device/` to the selected board.

- Requires offline-valid `hardware.yaml` and an exact match between its
  `board.id` and the selected drive's `boot_out.txt` board ID.
- Interrupts the running program, copies support files first and `code.py` last,
  then soft-resets once.
- Creates and updates files but never deletes unrelated board files.
- Refuses to write root `boot.py` or `settings.toml` without their explicit
  flags. An allowed `settings.toml` is copied opaquely and is never hashed for
  the result manifest.
- Captures startup output for five seconds by default.
- Fails if captured startup output contains an uncaught traceback.

### `patchcord monitor [--seconds N] [--output PATH] [--json]`

Streams serial output to stdout and appends it to
`.patchcord/logs/serial.log`. Without `--seconds`, it follows until interrupted.
Stopping the monitor releases the port without resetting the board. Serial I/O
uses `pyserial`; Patchcord adds locking, timestamps, persistence, and stable
errors but does not implement a terminal emulator.

With `--json`, `--seconds` is required and stdout contains one result document
after the bounded capture. Human streaming mode does not retain the complete
session in memory.

### `patchcord logs [--tail N | --since DURATION] [--json]`

Reads the host-side serial log without opening the serial port. The default is
the last 200 lines. Durations use forms such as `30s`, `10m`, and `2h`.

### `patchcord interrupt [--json]`

Sends `Ctrl-C` to stop the running program and returns the resulting console
output. This is a bounded `pyserial` control operation, not a general raw-REPL
execution implementation.

### `patchcord reset [--capture SECONDS] [--json]`

Interrupts the program, sends `Ctrl-D` for a soft reset, and captures startup
output for five seconds by default. Serial capture starts before the reset
character is sent.

### `patchcord repl [--eval CODE | --file PATH] [--no-reset] [--timeout SECONDS] [--json]`

Opens an interactive REPL when called without options. `--eval` executes a
snippet; `--file` executes a local script without installing it on the board.
Bounded executions reset afterward unless `--no-reset` is supplied. Bounded
output is also written to the serial log.

Interactive mode invokes the documented `serial.tools.miniterm` CLI with the
selected port under Patchcord's advisory port lock. Patchcord records session
start, end, target, and exit status, but does not capture the interactive
transcript. Agents should use bounded execution or `monitor --output` when
persistent output is required.

`--eval` and `--file` use the bounded-execution adapter; they do not use a
Patchcord-authored raw-REPL client. If no execution adapter passes its
acceptance gate, bounded execution exits with an
`execution_backend_unavailable` error while interactive mode may remain
available.

### `patchcord probe pins [--json]`

Temporarily interrupts `code.py`, lists public aliases from the board module
whose values are runtime `microcontroller.Pin` objects, and resets afterward.
Patchcord executes a versioned, packaged probe program through the
bounded-execution adapter. The probe reads the runtime `board` module and emits
framed JSON; it does not consult or update a Patchcord pin database.

### `patchcord probe i2c [--json]`

Temporarily interrupts `code.py`, scans the default I²C bus, reports hexadecimal
addresses, releases the bus, and resets afterward. A versioned, packaged probe
program performs the scan through the bounded-execution adapter and releases
the bus in a `finally` block. Patchcord does not infer a sensor model solely
from an address.

### `patchcord libs install [PACKAGE ...] [--auto] [--json]`

Runs `circup` against the selected board. Package arguments install named
libraries; no arguments install the project `requirements.txt`; `--auto`
invokes `circup install --auto`, which infers libraries from `code.py` currently
on the selected board. Patchcord does not perform a second local import
analysis. Package arguments and `--auto` are mutually exclusive.

Patchcord invokes the documented CLI with `circup --path SELECTED_MOUNT ...`,
uses an explicit absolute requirements-file path, records the `circup` version
and exit status, and does not duplicate dependency resolution or bundle
selection. Because `circup` does not promise a Patchcord JSON schema, its human
progress text is captured so it cannot corrupt Patchcord JSON; it is shown only
in human-readable mode and is not parsed into public result fields. Patchcord
refuses to run when it detects user-level circup bundle configuration that the
documented CLI cannot disable.

### `patchcord libs freeze [--json]`

Writes the board's installed CircuitPython libraries and available version
information to `requirements.txt` through `circup`'s documented CLI. Patchcord
runs `circup --path SELECTED_MOUNT freeze --requirement` in a temporary working
directory, validates the generated requirements file, and atomically replaces
the project file. It does not derive installed-library versions itself or
scrape the human-readable `circup freeze` table.

### `patchcord hardware validate [--json] [--offline]`

Validates the `hardware.yaml` schema, identifiers, part and net references, and
connection syntax. With a board connected, it also checks the declared board ID
and referenced board pins. `--offline` explicitly suppresses connected probing.
Without attached hardware the command completes using offline validation only.
The current circremote acceptance gate blocks the connected phase, so a complete
attached target produces `execution_backend_unavailable` until a backend
version is accepted. Validation detects inconsistencies but does not guarantee
electrical safety.

## Safety rules

- Never choose among ambiguous target candidates.
- Never delete board files during normal deployment.
- Treat the Git-managed local `device/` tree as the recoverable source of truth.
- Never inspect, log, or expose secrets as part of a Patchcord-owned operation;
  an explicitly allowed `settings.toml` deployment is an opaque byte copy.
- Treat arbitrary REPL and board-program output as user-controlled data that
  may contain secrets.
- Require explicit flags before writing root `boot.py` or `settings.toml`.
- Start serial capture before reset so startup failures are not missed.

## Out of scope for v0.1

- Installing or flashing CircuitPython firmware
- BLE and Web Workflow transports
- Hardware simulation
- Electrical-safety certification
- MCP server support

Patchcord is implemented in Python and managed with `uv`. Board discovery and
transport code must remain portable across macOS, Linux, and Windows; missing
OS permissions or drivers are diagnosed but not installed automatically.
