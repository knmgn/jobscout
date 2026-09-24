from __future__ import annotations

from collections.abc import Iterator

import pytest

from jobscout.demo_board import BoardServer, DemoBoard


@pytest.fixture
def board_server() -> Iterator[BoardServer]:
    with BoardServer(DemoBoard()) as server:
        yield server


@pytest.fixture
def expired_board_server() -> Iterator[BoardServer]:
    with BoardServer(DemoBoard(expired=True)) as server:
        yield server
