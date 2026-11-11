import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pyobs.interfaces import AltAzState, IPointingAltAz
from pyobs.utils.enums import MotionStatus

from pyobs_alpaca import AlpacaDome

_DOME_ARGS: dict[str, Any] = dict(server="localhost", port=11111, device_type="dome", device=0)


@pytest.mark.asyncio
async def test_dome_default_north_zero_azimuth_and_modulo(monkeypatch: pytest.MonkeyPatch) -> None:
    """AlpacaDome uses North-zero azimuth (az_offset=0.0) by default and wraps 360° -> 0°."""
    dome = AlpacaDome(az_offset=0.0, **_DOME_ARGS)
    dome.comm.set_state = AsyncMock()  # type: ignore[method-assign]

    async def mock_get(prop: str) -> Any:
        if prop == "Azimuth":
            return 360.0
        return 0.0

    async def stop_loop(_: float) -> None:
        raise asyncio.CancelledError

    dome._device.get = mock_get  # type: ignore[assignment]
    monkeypatch.setattr(asyncio, "sleep", stop_loop)

    with pytest.raises(asyncio.CancelledError):
        await dome._update_status()

    assert dome._azimuth == pytest.approx(0.0)
    altaz_calls = [
        call.args[1]
        for call in dome.comm.set_state.await_args_list
        if call.args[0] is IPointingAltAz and isinstance(call.args[1], AltAzState)
    ]
    assert len(altaz_calls) == 1
    assert altaz_calls[0].az == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_dome_custom_az_offset_read_write_and_park() -> None:
    """AlpacaDome applies az_offset symmetrically when reading, slewing, and parking."""
    dome = AlpacaDome(tolerance=3.0, park_az=180.0, az_offset=90.0, **_DOME_ARGS)
    dome._device._connected = True
    await dome._change_motion_status(MotionStatus.POSITIONED)

    put_calls: list[tuple[str, dict[str, Any]]] = []

    async def mock_put(action: str, **kwargs: Any) -> None:
        put_calls.append((action, kwargs))
        if action == "SlewToAzimuth":
            dome._azimuth = dome._from_device_azimuth(kwargs["Azimuth"])

    dome._device.put = mock_put  # type: ignore[assignment]

    # Device reports 0 deg -> North-zero azimuth is 90 deg
    assert dome._from_device_azimuth(0.0) == pytest.approx(90.0)

    # Moving to North-zero 30 deg -> sends (30 - 90) % 360 = 300 deg to device
    await dome.move_altaz(alt=45.0, az=30.0)
    slew_calls = [kwargs for action, kwargs in put_calls if action == "SlewToAzimuth"]
    assert len(slew_calls) == 1
    assert slew_calls[0]["Azimuth"] == pytest.approx(300.0)
    assert dome._azimuth == pytest.approx(30.0)

    # Closing dome slews to _to_device_azimuth(park_az=180.0) = 90.0 deg
    put_calls.clear()
    await dome._send_close_dome()
    assert put_calls == [
        ("CloseShutter", {}),
        ("SlewToAzimuth", {"Azimuth": pytest.approx(90.0)}),
    ]


def test_dome_angular_distance_wraps_across_zero() -> None:
    """_angular_distance computes shortest distance across the 0°/360° boundary."""
    assert AlpacaDome._angular_distance(359.0, 1.0) == pytest.approx(2.0)
    assert AlpacaDome._angular_distance(1.0, 359.0) == pytest.approx(2.0)
    assert AlpacaDome._angular_distance(0.0, 180.0) == pytest.approx(180.0)


def test_dome_and_telescope_default_to_south_zero_offset() -> None:
    from pyobs_alpaca.telescope import AlpacaTelescope

    dome = AlpacaDome(**_DOME_ARGS)
    assert dome._az_offset == 180.0
    assert dome._to_device_azimuth(180.0) == 0.0  # close/park slews to device az 0 as before
    assert dome._from_device_azimuth(0.0) == 180.0
    telescope = AlpacaTelescope(server="localhost", port=11111, device_type="telescope", device=0)
    assert telescope._az_offset == 180.0
