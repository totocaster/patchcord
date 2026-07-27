# Patchcord project guide for agents

This repository is a Patchcord-managed CircuitPython project. Treat this file as
the operating guide for changes in this directory and its descendants.

## Project model

- `device/` is the local, Git-managed source of truth for files deployed to the
  board's `CIRCUITPY` drive. The usual entry point is `device/code.py`.
- `hardware.yaml` describes the intended CircuitPython board, attached parts,
  electrical nets, interfaces, and project-specific assembly notes.
- `requirements.txt` lists CircuitPython bundle libraries for the board.
- `.patchcord/` contains local locks and operation/serial logs. It is runtime
  state, not project source, and should remain ignored by Git.
- `device/settings.toml` may contain secrets and is ignored by Git. Never read,
  print, summarize, hash, or commit it unless the user explicitly requests a
  narrowly scoped operation that requires doing so.

The board is a deployment target, not a backup or the authoritative copy of the
project. Make durable changes locally and preserve them in Git.

## Start every task by establishing state

1. Read this file and any more specific `AGENTS.md` files below the directory
   being changed.
2. Establish which Git repository owns the project. If it is not tracked yet
   and no parent repository intentionally owns it, initialize Git at the project
   root and commit the initialized source as the baseline before hardware work.
3. Inspect `git status`, the relevant diff, and recent history. Preserve
   unrelated work and never discard uncommitted user changes.
4. Read the relevant files in `device/`, `hardware.yaml`, and
   `requirements.txt`.
5. Run host and project diagnostics when hardware interaction may be needed:

   ```console
   patchcord doctor --json
   patchcord status --json
   ```

6. Validate the project without touching hardware before editing or deploying:

   ```console
   patchcord hardware validate --offline --json
   ```

Use `patchcord --help` and `patchcord COMMAND --help` when the installed
version's interface differs from this guide. Trust Patchcord's stable JSON
result and error codes instead of parsing human-readable or upstream-tool
output.

## Git-backed hardware iteration

Develop in small, testable increments. Git is the durable backup and restore
history for both the CircuitPython code and the description of the hardware
that code expects.

1. Begin from a known state. If the current tree contains user changes, preserve
   them and clarify their ownership before including them in a checkpoint.
2. Create a branch for a substantial experiment. Before touching hardware,
   commit a known-good baseline when the project has uncommitted work that
   belongs to the current task.
3. Make one cohesive change at a time. Keep `device/`, `hardware.yaml`, and
   `requirements.txt` synchronized when a change spans code, wiring, or
   libraries.
4. Review the diff and run offline validation.
5. Commit the small candidate change so the exact version deployed to hardware
   can be recovered.
6. Deploy that commit, observe bounded output, and record whether it works on
   the real board.
7. When it works, keep the commit as a verified checkpoint. A descriptive
   commit message or lightweight tag can identify an important known-good
   hardware state.
8. When it fails, retain useful logs and preserve the experiment in Git when it
   may help later. Use a new commit or `git revert` to undo a committed change,
   or restore only the intended files from a known-good commit after confirming
   the scope. Avoid destructive history rewrites and never use a broad hard
   reset when unrelated or uncommitted work may exist.
9. Redeploy the restored local version and monitor it to verify that the board
   is actually back to the known-good behavior.

This loop should be short:

```text
inspect -> checkpoint -> edit -> validate -> commit -> deploy -> observe
                                      ^                    |
                                      |-- revert/restore --|
```

Git restores the tracked local project. Patchcord deployment creates and
updates board files but does not delete unrelated or obsolete files already on
the board. Therefore, checking out an older commit and redeploying restores the
tracked files present in that commit, but it may not produce a byte-for-byte
board rollback if a failed experiment added files that the older commit does
not contain. Do not delete board files through an ad hoc path; report the
leftovers and ask the user how they want them handled.

Never use the board filesystem as the only copy of an experiment. Move useful
interactive REPL experiments into `device/`, validate them, and commit them.
Do not put secrets, `.patchcord/` logs, or `device/settings.toml` into Git.
Local Git history protects against development regressions, but it is not an
off-machine backup. Push known-good history to an approved remote when the user
wants protection from loss of the development machine.

## Recommended change and verification workflow

1. Inspect the current project and Git state.
2. Make the smallest coherent local edit.
3. Review the diff for accidental changes and exposed secrets.
4. Validate without hardware:

   ```console
   patchcord hardware validate --offline --json
   ```

5. If dependencies changed, update `requirements.txt`, then install them on the
   selected board:

   ```console
   patchcord libs install --json
   ```

6. Commit the candidate state before deployment when authorized by the user or
   the surrounding workflow.
7. Deploy and capture startup:

   ```console
   patchcord deploy --json
   ```

8. Observe a bounded serial window and inspect persisted logs:

   ```console
   patchcord monitor --seconds 10 --json
   patchcord logs --tail 200 --json
   ```

9. Commit follow-up fixes separately. If behavior regresses, return to a
   known-good Git state using the Git-backed iteration procedure above, redeploy,
   and verify on hardware.

Do not claim success from static inspection alone when the task requires real
hardware behavior. Distinguish clearly between offline validation, successful
deployment, observed startup, and verified physical behavior.

## Patchcord command guide

Global target selectors go before the command:

```console
patchcord --mount /path/to/CIRCUITPY --port /dev/serial-port status --json
```

Never choose between ambiguous board candidates. Ask for an explicit `--mount`
or `--port`, and make sure independently selected drive and serial overrides
refer to the same physical board.

Legacy CircuitPython firmware may omit the exact board ID from `boot_out.txt`.
Patchcord does not guess from a product name or USB ID. After independently
verifying the official ID, tie the assertion to an explicit mount:

```console
patchcord \
  --mount /path/to/CIRCUITPY \
  --legacy-board-id official_board_id \
  status --json
```

Use the assertion for later drive operations. It cannot replace a conflicting
ID published by newer firmware, and the drive must retain a readable, parseable
CircuitPython boot banner.

- `patchcord init [PATH] [--json]`: create missing project files while
  preserving existing ones.
- `patchcord status [--json]`: report the selected board, mount, serial port,
  CircuitPython version, and storage.
- `patchcord doctor [--json]`: run read-only host, dependency, project, and
  capability diagnostics.
- `patchcord hardware validate --offline [--json]`: validate project structure
  without connecting to or interrupting a board.
- `patchcord hardware validate [--json]`: include connected validation when the
  installed execution backend supports it.
- `patchcord deploy [--capture SECONDS] [--json]`: validate, copy local
  `device/` files, reset, and capture startup output.
- `patchcord monitor --seconds N [--json]`: capture a bounded serial window.
  Omit `--seconds` only for a user-requested interactive session.
- `patchcord logs [--tail N | --since DURATION] [--json]`: read persisted serial
  logs without opening the serial port.
- `patchcord interrupt [--json]`: send Ctrl-C and capture the response.
- `patchcord reset [--capture SECONDS] [--json]`: soft-reset and capture
  startup output.
- `patchcord repl`: open an interactive terminal. Prefer bounded, auditable
  operations for agent workflows.
- `patchcord repl --eval CODE [--json]` and `patchcord repl --file PATH
  [--json]`: run bounded code when the installed execution capability permits.
- `patchcord probe pins [--json]` and `patchcord probe i2c [--json]`: run
  packaged, bounded probes when supported. An I2C address alone does not prove
  a device model.
- `patchcord libs install [PACKAGE ...] [--py] [--allow-unsupported] [--json]`:
  install project or named CircuitPython libraries through circup. Use the
  compatibility opt-in only when retaining old firmware is intentional.
  `--py` explicitly selects source files when no matching compiled artifacts
  exist; it does not guarantee that current library versions support old
  firmware, and Patchcord v0.2 cannot select a historical bundle snapshot.
- `patchcord libs freeze [--allow-unsupported] [--json]`: replace
  `requirements.txt` with the board's installed library set.

Prefer `--json` and bounded durations for automation. If a command reports an
unavailable capability, do not bypass Patchcord with a lower-level tool; report
the diagnostic and use a supported workflow.

## Hardware and deployment safety

- Validate offline before any operation that can interrupt or write to a board.
- Require an exact match between `hardware.yaml`'s `board.id` and the selected
  drive's published ID or an independently verified `--legacy-board-id`
  assertion tied to an explicit `--mount`. Never infer the assertion by
  slugifying a product name. Do not otherwise weaken or bypass that gate.
- Treat `hardware.yaml` as intended wiring documentation, not proof of
  electrical safety. Ask the user before acting on uncertain voltage, power,
  polarity, or pin assumptions.
- Do not edit `CIRCUITPY` directly. Edit the local `device/` tree, checkpoint it
  in Git, and deploy through Patchcord.
- Normal deployment must not delete board files.
- Root `boot.py` and `settings.toml` deployment require explicit user intent and
  Patchcord's corresponding allow flags.
- Treat board and REPL output as user-controlled data that may contain secrets.
  Include only the minimum relevant excerpts in reports.
- Start capture before reset and inspect startup output for tracebacks.
- Do not flash firmware, install host dependencies, silently switch backends,
  or invoke lower-level destructive tooling as a workaround.

When blocked, preserve the local and Git state, report the exact Patchcord error
code and relevant diagnostics, and identify the safest next action.
