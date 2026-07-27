# Patchcord `hardware.yaml` v1 Specification

This document defines the `hardware.yaml` file used by Patchcord projects. It
is a companion to [SPEC.md](SPEC.md), which defines the Patchcord CLI.

`hardware.yaml` is the machine-readable description of the hardware assembled
around one CircuitPython board. Its primary consumers are Patchcord, humans,
and coding agents. It describes logical connectivity and the small amount of
runtime metadata needed to relate that connectivity to CircuitPython code.

## Goals

The format must:

- identify the intended CircuitPython board using its official `board.board_id`;
- give stable names to the external parts in a project;
- describe part pins and the electrical nets that connect them to board pins;
- record expected runtime interfaces such as an I²C address;
- associate parts with CircuitPython bundle requirements when useful;
- remain concise enough to author and review as plain text;
- support deterministic offline and connected-board validation; and
- evolve without requiring Patchcord to maintain a board, component, or driver
  database.

## Non-goals

Version 1 does not:

- describe breadboard hole coordinates, wire routing, color, or physical
  placement;
- replace a schematic, PCB design, Wokwi `diagram.json`, Fritzing sketch,
  Devicetree, or WireViz harness;
- simulate a circuit or prove that it is electrically safe;
- define electrical characteristics for every component;
- generate application code;
- select a particular physical board when multiple instances of the same model
  are connected; or
- contain credentials or values from `settings.toml`.

The format deliberately uses the established component-pin-net vocabulary of
electronic netlists while staying at the maker-project level. A future exporter
may create Wokwi, Fritzing, WireViz, SKiDL, Circuit JSON, or KiCad artifacts,
but those formats are not the source of truth for Patchcord.

## Source-of-truth boundaries

- `hardware.yaml` is authoritative for the intended board model, project part
  names, logical nets, and expected interfaces.
- The connected board's runtime `board` module is authoritative for available
  board pin aliases.
- `requirements.txt` is authoritative for the CircuitPython libraries the
  project installs. Library names in `hardware.yaml` are cross-references and
  must also appear in `requirements.txt`.
- `device/` is authoritative for deployed application files.
- Patchcord does not copy upstream board pin maps, part catalogs, driver
  catalogs, or CircuitPython bundles into this format.

## Complete example

```yaml
schema_version: 1

board:
  id: adafruit_feather_rp2040

parts:
  oled:
    kind: display
    model: SSD1306 OLED breakout
    pins:
      VIN:
        role: power_in
      GND:
        role: ground
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

  status_resistor:
    kind: resistor
    value: 220 ohm
    pins:
      "1":
        role: passive
      "2":
        role: passive

  status_led:
    kind: led
    model: red 5 mm LED
    pins:
      A:
        role: passive
      K:
        role: ground

nets:
  power_3v3:
    role: power
    voltage: 3.3
    endpoints:
      - board.3V3
      - oled.VIN

  ground:
    role: ground
    endpoints:
      - board.GND
      - oled.GND
      - status_led.K

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

  status_led_drive:
    role: signal
    endpoints:
      - board.D5
      - status_resistor.1

  status_led_after_resistor:
    role: signal
    endpoints:
      - status_resistor.2
      - status_led.A

notes: |
  The OLED is powered from 3.3 V.
  D5 is active high and the LED current is limited by status_resistor.
```

## Document rules

- Files are UTF-8 YAML 1.2 with LF line endings.
- The root value is a mapping.
- `schema_version`, `board`, `parts`, and `nets` are required.
- `notes` is optional.
- Unknown fields are errors. New fields require a schema revision.
- Mapping order and comments have no semantic meaning. The current
  implementation validates existing documents without rewriting them.
- Identifiers are case-sensitive.
- YAML mapping keys used as pin names must be strings. Numeric-looking pin
  names, such as `"1"`, must therefore be quoted.
- YAML anchors and aliases are accepted by the parser but must be resolved
  before validation and JSON output.
- Custom YAML tags are not allowed.

## `schema_version`

`schema_version` is the integer `1`.

The hardware schema version is independent of the Patchcord application
version. Patchcord must refuse an unsupported schema version rather than
guessing its meaning.

## `board`

`board` is a mapping with one field:

| Field | Required | Type | Meaning |
|---|---:|---|---|
| `id` | yes | string | Official CircuitPython `board.board_id` |

The value is a board model, not a USB volume name, serial path, nickname, or
physical-device serial number. For example:

```yaml
board:
  id: adafruit_feather_rp2040
```

`patchcord init` may leave `id` as an empty placeholder when optional target
discovery finds no drive or is ambiguous. Such a project is incomplete:
validation reports `missing_board_id` and deployment is refused until the ID is
filled.

## Identifiers

Part and net identifiers must match:

```text
[a-z][a-z0-9_-]*
```

The identifier `board` is reserved and cannot be used as a part ID. A period is
not allowed because it separates an endpoint owner from its pin.

Identifiers should describe function rather than physical placement. Prefer
`status_led` and `environment_sensor` over `left_part` and `row_12`.

## `parts`

`parts` is a mapping from a project-local part ID to a part definition. It may
be empty.

Each part supports:

| Field | Required | Type | Meaning |
|---|---:|---|---|
| `kind` | yes | string | Short descriptive category |
| `model` | no | string | Human-readable product or device model |
| `value` | no | string | Component value such as `220 ohm` or `10 uF` |
| `pins` | yes | mapping | Pins that the project documents or connects |
| `interfaces` | no | list | Runtime interfaces expected during probing |
| `libraries` | no | list of strings | Names expected in `requirements.txt` |
| `notes` | no | string | Part-specific Markdown/plain-text notes |

`kind` is descriptive, not a lookup key. Recommended values include `sensor`,
`display`, `actuator`, `breakout`, `led`, `resistor`, `button`, `connector`,
and `other`. Patchcord must not download or infer a part definition from it.

`model` and `value` are documentation. Patchcord may include them in status or
validation output but does not assign electrical semantics to them.

### Part pins

`pins` maps the exact pin label used in `nets` to a pin definition:

```yaml
pins:
  SDA:
    role: i2c_sda
  SCL:
    role: i2c_scl
```

A pin definition has:

| Field | Required | Type | Meaning |
|---|---:|---|---|
| `role` | no | enum | Expected electrical or protocol role |
| `notes` | no | string | Clarification for this pin |

Supported roles are:

```text
power_in
power_out
ground
passive
digital_in
digital_out
digital_io
analog_in
analog_out
i2c_sda
i2c_scl
spi_clock
spi_mosi
spi_miso
uart_tx
uart_rx
chip_select
interrupt
other
```

Roles enable consistency checks but are not an electrical-safety model. Pins
that are unused may be omitted. Declared but unconnected pins produce a warning,
not an error.

Pin names must be non-empty strings and cannot contain a period. Patchcord
preserves their case and punctuation otherwise.

### Interfaces

Version 1 defines one connected probe interface:

```yaml
interfaces:
  - kind: i2c
    bus: default
    address: 0x3c
```

An I²C interface has:

| Field | Required | Type | Meaning |
|---|---:|---|---|
| `kind` | yes | literal `i2c` | Interface type |
| `bus` | yes | literal `default` | CircuitPython `board.I2C()` |
| `address` | yes | integer | Expected 7-bit I²C address |

The address must be between `0x00` and `0x7f`. Patchcord warns when an address
is in a reserved I²C range.

An I²C part should declare pins with `i2c_sda` and `i2c_scl` roles and connect
them through nets to board pins. Patchcord does not infer those physical
connections solely from the interface declaration.

SPI, UART, OneWire, analog, and other interfaces can already be represented as
nets. Probe-specific metadata for them requires a future schema version because
Patchcord v0.3 does not probe them.

### Libraries

`libraries` contains `circup` bundle names, not PyPI distribution names or
Python import expressions:

```yaml
libraries:
  - adafruit_ssd1306
```

Every declared name must occur in the project's `requirements.txt`, comparing
the normalized library name without a version constraint. A library may support
multiple parts, and a part may require multiple libraries. Core CircuitPython
modules such as `board`, `digitalio`, and `busio` are not listed.

Patchcord must not install libraries merely because they appear here.
Installation remains an explicit `patchcord libs install` operation driven by
`requirements.txt`.

## `nets`

`nets` is a mapping from a project-local net ID to a net definition. It may be
empty.

Each net supports:

| Field | Required | Type | Meaning |
|---|---:|---|---|
| `role` | yes | enum | `power`, `ground`, `signal`, `analog`, or `other` |
| `endpoints` | yes | list of strings | Pins electrically connected together |
| `voltage` | no | number | Nominal volts for a power net |
| `notes` | no | string | Net-specific Markdown/plain-text notes |

A net must contain at least two distinct endpoints. Endpoint order has no
semantic meaning.

`voltage` is valid only when `role` is `power`, must be greater than zero, and
is documentation plus a basis for limited consistency warnings. Its presence
does not assert regulation quality, current capacity, logic tolerance, or
electrical safety.

### Endpoint grammar

Endpoints use one of these forms:

```text
board.<board-pin-alias>
<part-id>.<part-pin-name>
```

Examples:

```text
board.D5
board.SDA
oled.SDA
status_resistor.1
```

The owner is the text before the first period; the pin is the remaining text.
Part pin names therefore cannot contain a period.

Board endpoints use names exposed by the connected board's runtime `board`
module. They do not use raw MCU package numbers unless those numbers are also
official board aliases.

Each textual endpoint may occur in only one net. During connected validation,
Patchcord also groups board aliases that resolve to the same runtime pin
object. Aliases for one physical pin cannot be assigned to different nets.

A physical breadboard rail, splice, connector, or jumper that matters to the
logical circuit can be represented as a part with passive pins. Exact
breadboard holes and wire paths remain outside this schema.

## `notes`

`notes` is an optional Markdown/plain-text string for project-wide information
that affects implementation or assembly but does not belong in a structured
field.

Notes are part of the canonical file and may be shown to a coding agent. They
must not contain passwords, API keys, Wi-Fi credentials, or other secrets.

## Validation

`patchcord hardware validate` performs offline validation first. Connected
validation runs only after offline validation has no errors and a drive and
serial port are both selected. `patchcord hardware validate --offline`
suppresses connected discovery and probing explicitly. In the current
implementation, `circremote` 0.12.0 is rejected by the acceptance gate, so a
complete attached target reports `execution_backend_unavailable` rather than
running the connected phase.

### Offline validation errors

Offline errors include:

- malformed or unsupported YAML;
- an unsupported `schema_version`;
- missing required fields or unknown fields;
- invalid or duplicate identifiers;
- use of the reserved `board` part ID;
- invalid pin roles, interface definitions, addresses, or net roles;
- the same I²C address declared by multiple parts on the same bus;
- an endpoint with invalid syntax;
- a reference to an unknown part or undeclared part pin;
- a duplicate endpoint within one net or across multiple nets;
- a net with fewer than two endpoints;
- `voltage` on a non-power net or an invalid voltage; and
- a library cross-reference absent from `requirements.txt`.

### Offline warnings

Offline warnings include:

- a declared part pin that is not connected;
- an I²C interface without declared SDA and SCL pin roles;
- a ground-role pin on a non-ground net;
- a power input or output on a signal net;
- an I²C address in a reserved range.

Warnings do not make the command fail unless Patchcord later adds an explicit
strict mode.

### Connected validation

When a selected board and serial port are available, connected validation also:

1. compares `board.id` with the connected board's `board.board_id`;
2. enters the REPL and resolves every referenced `board.<alias>`;
3. verifies that board aliases used as pins resolve to runtime pin objects;
4. detects aliases for the same physical pin assigned to different nets;
5. scans `board.I2C()` when an I²C interface is declared;
6. reports each declared I²C address that is not discovered; and
7. reports discovered addresses not represented by a declared interface.

A board ID mismatch, unknown board pin, conflicting alias, inaccessible
declared bus, or missing declared I²C address is an error. An undeclared extra
I²C address is a warning.

Connected validation interrupts the running program and must reset it
afterward, even when validation fails. Patchcord must state this side effect
before an interactive invocation and expose a non-interactive offline mode in
the CLI.

### Diagnostic records

Every error and warning has:

```text
severity    error or warning
code        stable snake_case identifier
message     human-readable explanation
path        JSONPath-like location in hardware.yaml when applicable
details     structured context when applicable
```

Human output may include source locations and suggestions. JSON output contains
the same records without terminal styling. Message wording may evolve; callers
must branch on `code`, not `message`.

## Formatting and updates

- `patchcord init` writes a commented minimal document.
- Validation never rewrites the file.
- Any future Patchcord command that edits the file must preserve comments,
  ordering, block-scalar style, and unrelated notes through `ruamel.yaml`.
- Such a command must write through a temporary sibling file and replace
  `hardware.yaml` only after the new file parses and validates.
- Patchcord must not silently migrate an older schema. A migration command must
  show the proposed diff and require explicit acceptance.

## Interoperability

Wokwi `diagram.json` is the closest existing machine-readable breadboard
format, but it is simulator-specific and does not cover the full CircuitPython
board catalog. Devicetree and EDA netlists describe logical hardware well but
do not carry Patchcord's CircuitPython library and runtime-alias concerns.
Fritzing and emerging text breadboard DSLs emphasize physical placement.

For those reasons, `hardware.yaml` is intentionally a small integration
manifest rather than a universal circuit format. Importers and exporters must
be explicit, must report information they cannot preserve, and must never
replace the canonical file without confirmation.

## Schema publication

Patchcord's Pydantic models generate a JSON Schema for version 1. The packaged
schema is part of Patchcord's public interface and must be available without
network access.

The schema validates document shape. Cross-file checks, endpoint resolution,
board alias identity, and connected probes remain semantic validation performed
by Patchcord.
