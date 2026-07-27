# Patchcord

Patchcord is a command-line workflow for developing CircuitPython projects
without a dedicated IDE. It keeps your application, library list, and hardware
description in a normal Git repository, then gives you one CLI for deploying to
a board, watching serial output, controlling execution, and validating the
project's hardware description.

Patchcord currently supports CircuitPython boards that expose the standard
`CIRCUITPY` USB drive and serial console.

## Install

Patchcord requires:

- Python 3.11 or newer;
- [uv](https://docs.astral.sh/uv/) 0.11.32 or newer; and
- a board with CircuitPython already installed.

Install the latest version directly from GitHub:

```console
uv tool install git+https://github.com/totocaster/patchcord.git
patchcord --version
```

To run Patchcord from a source checkout instead:

```console
git clone https://github.com/totocaster/patchcord.git
cd patchcord
uv sync --locked
uv run patchcord --help
```

The examples below assume `patchcord` is installed as a tool. From a source
checkout, replace it with `uv run patchcord`.

Use `patchcord --help` or `patchcord COMMAND --help` to inspect the available
commands and options.

## Quick start

Connect a CircuitPython board, then initialize a project:

```console
patchcord init my-board
cd my-board
```

Patchcord creates:

```text
my-board/
├── device/
│   └── code.py          # Application files deployed to CIRCUITPY
├── hardware.yaml        # Intended board, parts, nets, and interfaces
├── requirements.txt     # CircuitPython library bundle names
└── .gitignore
```

If discovery is unambiguous, `patchcord init` records the connected board ID in
`hardware.yaml`. Otherwise, fill in `board.id` before deploying.

Project commands can be run from the project root or any directory below it;
Patchcord finds the nearest project automatically. Runtime locks and logs are
created under the Git-ignored `.patchcord/` directory when needed.

Put the project under Git so the local `device/` tree remains your recoverable
source of truth:

```console
git init
git add .
git commit -m "Initialize CircuitPython project"
```

Check the selected board and validate the project:

```console
patchcord status
patchcord hardware validate --offline
```

Edit `device/code.py`, then deploy and watch its output:

```console
patchcord deploy
patchcord monitor --seconds 10
```

That is the core Patchcord loop: edit locally, validate, deploy, and inspect the
board's output.

## The everyday development workflow

### 1. Edit locally

Keep every file that should appear on `CIRCUITPY` under `device/`. Patchcord
copies regular files from this tree and creates their parent directories; it
does not use the board as the source of truth.

```text
device/
├── code.py
├── helpers.py
└── assets/
    └── config.json
```

### 2. Validate the project

Run offline validation before touching the board:

```console
patchcord hardware validate --offline
```

This checks `hardware.yaml`, endpoint references, I²C declarations, and library
cross-references against `requirements.txt`. The `--offline` flag guarantees
that Patchcord will not connect to or interrupt hardware.

### 3. Deploy

```console
patchcord deploy
```

Before copying, Patchcord requires:

- a valid `hardware.yaml`;
- a selected `CIRCUITPY` drive and serial port;
- an exact match between `hardware.yaml`'s `board.id` and the selected drive;
  and
- a `device/code.py` entry point.

Deployment interrupts the running program, copies support files before
`code.py`, soft-resets the board, and captures five seconds of startup output.
It returns a failure if startup contains an uncaught traceback.

Change the capture duration when the program needs longer to start:

```console
patchcord deploy --capture 15
```

Deployment creates or updates files but never deletes unrelated files already
on the board. Root `boot.py` and `settings.toml` require explicit permission:

```console
patchcord deploy --allow-boot
patchcord deploy --allow-settings
```

An allowed `settings.toml` is copied opaquely; Patchcord does not display or
publish a digest of its contents. `device/settings.toml` is ignored by Git when
Patchcord initializes a project.

### 4. Observe and repeat

Stream until you press Ctrl-C:

```console
patchcord monitor
```

Or capture a bounded window:

```console
patchcord monitor --seconds 10
```

All monitor sessions are appended to `.patchcord/logs/serial.log`. To append the
raw board output to another file as well:

```console
patchcord monitor --seconds 30 --output startup.txt
```

Read previous sessions without opening the serial port:

```console
patchcord logs
patchcord logs --tail 50
patchcord logs --since 10m
```

`patchcord logs` shows the last 200 lines by default. Durations accept seconds,
minutes, hours, or days, such as `30s`, `10m`, `2h`, and `1d`.

## Selecting a board

Patchcord selects a drive or serial port automatically only when that target
component is unambiguous. Use global overrides when multiple boards are
attached:

```console
patchcord \
  --mount /Volumes/CIRCUITPY \
  --port /dev/cu.usbmodem101 \
  status
```

Global options go before the command name. The same pattern works with
`deploy`, `monitor`, `reset`, `repl`, `libs`, and `hardware validate`.

Patchcord v0.1 selects the drive and serial port independently; it cannot prove
that two explicit overrides belong to the same physical board. When overriding
both, make sure they identify the same device.

## Serial control and REPL

Stop the running CircuitPython program and capture its console response:

```console
patchcord interrupt
```

Soft-reset the board and capture startup:

```console
patchcord reset
patchcord reset --capture 10
```

Open an interactive serial REPL:

```console
patchcord repl
```

Interactive mode uses pyserial's miniterm and does not save a transcript. Use
`monitor --output` when you need a separate raw output file.

Patchcord also defines bounded execution and probe commands:

```console
patchcord repl --eval "print('hello')"
patchcord repl --file tools/check_sensor.py
patchcord probe pins
patchcord probe i2c
```

In the current v0.1 release, these bounded commands are unavailable because the
pinned execution backend did not pass Patchcord's isolation and reset checks.
They return `execution_backend_unavailable`. Interactive `repl`, deployment,
monitoring, serial control, library management, and offline hardware validation
remain available.

## Managing CircuitPython libraries

List the project's CircuitPython bundle libraries in `requirements.txt`, one
per line:

```text
adafruit_bus_device
adafruit_requests
adafruit_ssd1306
```

Install the complete file on the selected board:

```console
patchcord libs install
```

Install one or more named bundle packages:

```console
patchcord libs install adafruit_requests adafruit_ssd1306
```

Or let circup inspect `code.py` currently deployed on the board:

```console
patchcord libs install --auto
```

Named packages and `--auto` are mutually exclusive. `--auto` examines the
board's deployed code, not the local `device/` tree. A named install does not
edit `requirements.txt`; update the file yourself or freeze the board afterward
to make the environment repeatable.

Record the board's installed libraries back into the project:

```console
patchcord libs freeze
```

This validates circup's generated requirements and atomically replaces
`requirements.txt`.

## Describing and validating hardware

`hardware.yaml` records the intended board and the project-specific parts and
connections around it. A minimal document looks like this:

```yaml
schema_version: 1

board:
  id: adafruit_feather_rp2040

parts: {}
nets: {}
```

A part can declare pins, an expected I²C address, and libraries:

```yaml
parts:
  oled:
    kind: display
    model: SSD1306 OLED
    pins:
      SDA:
        role: i2c_sda
      SCL:
        role: i2c_scl
    interfaces:
      - kind: i2c
        bus: default
        address: 0x3c
    libraries:
      - adafruit_ssd1306

nets:
  i2c_sda:
    role: signal
    endpoints:
      - board.SDA
      - oled.SDA
  i2c_scl:
    role: signal
    endpoints:
      - board.SCL
      - oled.SCL
```

Every library named by a part must also be present in `requirements.txt`.

Use offline validation during editing and in CI:

```console
patchcord hardware validate --offline
patchcord hardware validate --offline --json
```

Without `--offline`, Patchcord also attempts connected pin and I²C checks when a
drive and serial port are selected. Connected validation interrupts and resets
the board. It is currently blocked by the bounded-execution limitation
described above.

The complete format is documented in the
[`hardware.yaml` v1 specification](docs/WIRING_SPEC.md).

## JSON output

Most bounded commands accept `--json`:

```console
patchcord status --json
patchcord deploy --json
patchcord reset --json
patchcord logs --tail 50 --json
patchcord hardware validate --offline --json
```

JSON mode writes one result document to stdout with stable result and error
codes. `monitor --json` must be bounded with `--seconds`:

```console
patchcord monitor --seconds 10 --json
```

Interactive `patchcord repl` does not support JSON output.

## Diagnose a setup

`doctor` performs read-only checks for project files, connected drives and
serial ports, permissions, installed dependencies, and available capabilities:

```console
patchcord doctor
patchcord doctor --json
```

Useful first checks when a command cannot find or open a board are:

1. Confirm that the `CIRCUITPY` drive is mounted.
2. Confirm that the board's serial device is present.
3. Run `patchcord doctor`.
4. Pass explicit `--mount` and `--port` values if discovery is ambiguous.
5. Check the operating system's drive and serial-port permissions.

Patchcord reports missing permissions or drivers but does not install or change
them automatically.

## Further documentation

- [Patchcord v0.1 CLI and behavior contract](docs/SPEC.md)
- [`hardware.yaml` v1 format](docs/WIRING_SPEC.md)
- [`circremote` 0.12.0 acceptance result](docs/architecture/circremote-0.12.0-acceptance.md)
- [Mounted-filesystem deployment decision](docs/architecture/deployment-filesystem-exception.md)

## Development

```console
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv build --no-sources
```

Bug reports and contributions are welcome through
[GitHub Issues](https://github.com/totocaster/patchcord/issues).
