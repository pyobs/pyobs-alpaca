"""Unit tests for the pure, non-hardware logic in AlpacaDevice.

Hardware I/O (open/close, talking to the ASCOM server) is out of scope here; these cover
URL construction, version validation, the connection guard, and the connected-state
transition logic.
"""

from typing import Any

import pytest

from pyobs_alpaca.device import AlpacaDevice

_ARGS = dict(server="myserver", port=11111, device_type="telescope", device=3)


def test_build_alpaca_url() -> None:
    device = AlpacaDevice(**_ARGS)
    assert device._build_alpaca_url("RightAscension") == ("http://myserver:11111/api/v1/telescope/3/rightascension")


def test_only_v1_supported() -> None:
    with pytest.raises(ValueError):
        AlpacaDevice(**_ARGS, version="v2")


def test_starts_disconnected() -> None:
    device = AlpacaDevice(**_ARGS)
    assert device.connected is False


@pytest.mark.asyncio
async def test_get_requires_connection() -> None:
    device = AlpacaDevice(**_ARGS)
    with pytest.raises(ConnectionError):
        await device.get("RightAscension")


@pytest.mark.asyncio
async def test_put_requires_connection() -> None:
    device = AlpacaDevice(**_ARGS)
    with pytest.raises(ConnectionError):
        await device.put("Tracking", Tracking=False)


@pytest.mark.asyncio
async def test_check_connected_sets_flag_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    device = AlpacaDevice(**_ARGS)

    async def fake_get(name: str) -> Any:
        return True

    monkeypatch.setattr(device, "_get", fake_get)
    await device._check_connected()
    assert device.connected is True


@pytest.mark.asyncio
async def test_check_connected_clears_flag_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    device = AlpacaDevice(**_ARGS)
    device._connected = True

    async def fake_get(name: str) -> Any:
        raise ConnectionError

    monkeypatch.setattr(device, "_get", fake_get)
    await device._check_connected()
    assert device.connected is False
