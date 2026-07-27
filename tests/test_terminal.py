from __future__ import annotations

from collections.abc import Sequence

import pytest

from patchcord.adapters.terminal import run_miniterm
from patchcord.errors import TransportError


def test_miniterm_uses_documented_module_cli() -> None:
    recorded: list[str] = []

    def runner(argv: Sequence[str]) -> int:
        recorded.extend(argv)
        return 0

    assert run_miniterm("/dev/test", runner=runner) == 0
    assert recorded[1:4] == ["-m", "serial.tools.miniterm", "--raw"]
    assert recorded[-2:] == ["/dev/test", "115200"]


def test_miniterm_failure_is_normalized() -> None:
    with pytest.raises(TransportError) as raised:
        run_miniterm("COM1", runner=lambda _argv: 2)

    assert raised.value.code == "interactive_repl_failed"
