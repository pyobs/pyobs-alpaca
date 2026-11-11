import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pyobs.interfaces import AltAzState, IPointingAltAz, IPointingRaDec, RaDecState
from pyobs.utils.enums import MotionStatus

from pyobs_alpaca import AlpacaTelescope

_TELESCOPE_ARGS: dict[str, Any] = dict(server="localhost", port=11111, device_type="telescope", device=0)


@pytest.mark.asyncio
async def test_update_position_preserves_north_zero_azimuth_and_wraps_modulo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_update_position publishes North-zero ASCOM Azimuth directly by default and supports az_offset."""
    telescope = AlpacaTelescope(az_offset=0.0, **_TELESCOPE_ARGS)
    telescope.comm.set_state = AsyncMock()  # type: ignore[method-assign]

    async def mock_get(prop: str) -> Any:
        if prop == "RightAscension":
            return 0.1  # 1.5 deg
        if prop == "Declination":
            return 0.0
        if prop == "Azimuth":
            return 180.0
        if prop == "Altitude":
            return 45.0
        return 0.0

    async def stop_loop(_: float) -> None:
        raise asyncio.CancelledError

    telescope._device.get = mock_get  # type: ignore[assignment]
    monkeypatch.setattr(asyncio, "sleep", stop_loop)

    with pytest.raises(asyncio.CancelledError):
        await telescope._update_position()

    altaz_calls = [
        call.args[1]
        for call in telescope.comm.set_state.await_args_list
        if call.args[0] is IPointingAltAz and isinstance(call.args[1], AltAzState)
    ]
    assert len(altaz_calls) == 1
    assert altaz_calls[0].az == pytest.approx(180.0)
    assert altaz_calls[0].alt == pytest.approx(45.0)


@pytest.mark.asyncio
async def test_update_position_wraps_ra_and_az_across_360_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_update_position wraps negative offset-adjusted RA and 360° Azimuth into [0, 360)."""
    telescope = AlpacaTelescope(az_offset=0.0, **_TELESCOPE_ARGS)
    telescope._offset_ra = 3.0  # deg on sky at dec=0 -> subtracts 3.0 deg from raw RA=1.5 deg -> 358.5 deg
    telescope.comm.set_state = AsyncMock()  # type: ignore[method-assign]

    async def mock_get(prop: str) -> Any:
        if prop == "RightAscension":
            return 0.1  # 1.5 deg
        if prop == "Declination":
            return 0.0
        if prop == "Azimuth":
            return 360.0
        if prop == "Altitude":
            return 30.0
        return 0.0

    async def stop_loop(_: float) -> None:
        raise asyncio.CancelledError

    telescope._device.get = mock_get  # type: ignore[assignment]
    monkeypatch.setattr(asyncio, "sleep", stop_loop)

    with pytest.raises(asyncio.CancelledError):
        await telescope._update_position()

    assert telescope._cached_ra == pytest.approx(358.5)
    assert telescope._cached_dec == pytest.approx(0.0)

    radec_calls = [
        call.args[1]
        for call in telescope.comm.set_state.await_args_list
        if call.args[0] is IPointingRaDec and isinstance(call.args[1], RaDecState)
    ]
    assert len(radec_calls) == 1
    assert radec_calls[0].ra == pytest.approx(358.5)

    altaz_calls = [
        call.args[1]
        for call in telescope.comm.set_state.await_args_list
        if call.args[0] is IPointingAltAz and isinstance(call.args[1], AltAzState)
    ]
    assert len(altaz_calls) == 1
    assert altaz_calls[0].az == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_move_altaz_and_move_radec_wrap_coordinates_and_apply_az_offset() -> None:
    """_move_altaz and _move_radec normalize azimuth and RA into [0, 360) / [0, 24) and apply az_offset."""
    telescope = AlpacaTelescope(settle_time=0.0, az_offset=90.0, **_TELESCOPE_ARGS)
    put_calls: list[tuple[str, dict[str, Any]]] = []

    async def mock_get(prop: str) -> Any:
        if prop == "Slewing":
            return False
        return 0.0

    async def mock_put(action: str, **kwargs: Any) -> None:
        put_calls.append((action, kwargs))

    telescope._device.get = mock_get  # type: ignore[assignment]
    telescope._device.put = mock_put  # type: ignore[assignment]

    abort = asyncio.Event()
    await telescope._move_altaz(alt=40.0, az=30.0, abort_event=abort)
    await telescope._move_radec(ra=360.0, dec=25.0, abort_event=abort)

    slew_altaz = [kwargs for action, kwargs in put_calls if action == "SlewToAltAzAsync"]
    assert len(slew_altaz) == 1
    assert slew_altaz[0]["Azimuth"] == pytest.approx(300.0)
    assert slew_altaz[0]["Altitude"] == pytest.approx(40.0)

    slew_radec = [kwargs for action, kwargs in put_calls if action == "SlewToCoordinatesAsync"]
    assert len(slew_radec) == 1
    assert slew_radec[0]["RightAscension"] == pytest.approx(0.0)
    assert slew_radec[0]["Declination"] == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_set_offsets_radec_and_sync_target_wrap_ra() -> None:
    """set_offsets_radec and sync_target wrap RA into [0, 24) hours across the 0h/24h boundary."""
    telescope = AlpacaTelescope(settle_time=0.0, **_TELESCOPE_ARGS)
    telescope._device._connected = True
    await telescope._change_motion_status(MotionStatus.TRACKING)
    telescope.comm.send_event = AsyncMock()  # type: ignore[method-assign]
    telescope.comm.set_state = AsyncMock()  # type: ignore[method-assign]

    put_calls: list[tuple[str, dict[str, Any]]] = []

    async def mock_get(prop: str) -> Any:
        if prop == "RightAscension":
            return 23.9  # 358.5 deg
        if prop == "Declination":
            return 0.0
        if prop == "Slewing":
            return False
        return 0.0

    async def mock_put(action: str, **kwargs: Any) -> None:
        put_calls.append((action, kwargs))

    telescope._device.get = mock_get  # type: ignore[assignment]
    telescope._device.put = mock_put  # type: ignore[assignment]

    # Adding +3.0 deg offset to 358.5 deg wraps to 1.5 deg (0.1 hours)
    await telescope.set_offsets_radec(dra=3.0, ddec=0.5)

    slew_radec = [kwargs for action, kwargs in put_calls if action == "SlewToCoordinatesAsync"]
    assert len(slew_radec) == 1
    assert slew_radec[0]["RightAscension"] == pytest.approx(0.1)
    assert slew_radec[0]["Declination"] == pytest.approx(0.5)

    telescope._cached_ra = 360.0
    telescope._cached_dec = 12.0
    await telescope.sync_target()

    sync_calls = [kwargs for action, kwargs in put_calls if action == "SyncToCoordinates"]
    assert len(sync_calls) == 1
    assert sync_calls[0]["RightAscension"] == pytest.approx(0.0)
    assert sync_calls[0]["Declination"] == pytest.approx(12.0)
