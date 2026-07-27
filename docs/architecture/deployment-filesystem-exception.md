# Mounted-filesystem deployment exception

Status: **accepted for v0.1**

Evaluated: 2026-07-27

## Capability and required behavior

Patchcord must deploy every regular file under `device/` to the explicitly
selected `CIRCUITPY` mount without deleting unrelated board files. It must
validate the complete plan before board I/O, gate root `boot.py` and
`settings.toml`, reject unsafe paths, copy support files before `code.py`, flush
writes, and return a typed created/updated/skipped/refused manifest. The command
layer separately owns mount and serial selection, locks, interrupt, reset,
startup capture, and traceback classification.

## Upstream evaluation

- [CircuitPython 10.1.1 USB workflow](https://docs.circuitpython.org/en/10.1.1/docs/workflows.html):
  the official workflow exposes `CIRCUITPY` as host-writable USB mass storage,
  but does not define a project-deployment CLI or the required safety policy.
- [`circdeploy` 1.0.0](https://pypi.org/project/circdeploy/): evaluated its
  published CLI and ran `circdeploy --help` from an isolated 1.0.0 install.
  The documented surface is `--source`, `--destination`, `--no-delete`,
  `--no-gitignore`, `--no-cache`, `--reset-cache`, and `--dry-run`; no public
  deployment API is documented.
- [`circup` 3.0.4](https://pypi.org/project/circup/): ran its documented
  `--version` and `--help` commands. Its public commands manage libraries and
  bundle examples, not an arbitrary project tree, so it remains Patchcord's
  library backend rather than its deployment backend.
- CPython 3.14.6 standard `pathlib`, file I/O, `hashlib`, and `os.fsync` APIs:
  selected for the portable filesystem primitives; they do not supply
  Patchcord's deployment policy.

`circdeploy` is not reusable for this contract. Its documented default deletes
unmatched Python files, and even its non-deleting invocation copies only
`.py`/`.pyc` files. Its public contract also provides no protected-file gate,
`code.py`-last guarantee, explicit mount selection and command-layer serial
coordination, reset and startup capture, or typed checksum manifest. Wrapping
its whole-tree operation would still require Patchcord to implement the
safety-critical plan and ordering.

## Smallest Patchcord-owned surface

`patchcord.adapters.deployment` may only:

- preflight a source tree and selected mount, rejecting symlinks, path escapes,
  case-insensitive collisions, unsupported files, and unapproved protected
  files;
- order ordinary file copies with `code.py` last;
- create directories, copy and flush files, and calculate checksums; and
- return the typed manifest without deleting board files.

It must not add synchronization, ignore rules, backup/restore, remote
filesystems, dependency installation, target discovery, or serial transport.

## Conformance boundary

The exception is guarded by these named tests:

- `test_deploy_copies_support_files_before_code_and_never_deletes`
- `test_protected_files_require_exact_flag`
- `test_settings_copy_is_opaque_and_never_publishes_a_digest`
- `test_source_symlink_is_refused`
- `test_target_symlink_is_refused`
- `test_case_insensitive_source_collision_is_refused`
- `test_deploy_rejects_board_identity_mismatch_before_serial_io`
- `test_deploy_attempts_recovery_when_interrupt_capture_fails`

Re-evaluate this decision before each major Patchcord release, and whenever a
newer `circdeploy` or another maintained CircuitPython deployment tool exposes
an explicit-target, non-deleting, all-regular-file operation with protected-file
gating or hooks, deterministic `code.py`-last ordering, and a machine-readable
checksum manifest. Any replacement must first pass these conformance tests on
the supported macOS, Linux, and Windows matrix.
