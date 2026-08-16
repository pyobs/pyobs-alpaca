"""Smoke tests: import every public module and instantiate each driver class with a
missing/mocked hardware connection, asserting it loads and advertises the interfaces it
claims.

No real hardware or ASCOM server is involved: instantiation only registers background
tasks, which never start until open() is called.
"""

from pyobs.interfaces import (
    IDome,
    IFitsHeaderBefore,
    IFocuser,
    IOffsetsRaDec,
    ISyncTarget,
    ITelescope,
)
from pyobs.object import Object

from pyobs_alpaca import AlpacaDome, AlpacaFocuser, AlpacaTelescope
from pyobs_alpaca.device import AlpacaDevice

_TELESCOPE_ARGS = dict(server="localhost", port=11111, device_type="telescope", device=0)


def test_import_device_and_instantiate() -> None:
    device = AlpacaDevice(**_TELESCOPE_ARGS)
    assert isinstance(device, Object)
    assert not device.connected


def test_import_telescope_and_instantiate() -> None:
    telescope = AlpacaTelescope(**_TELESCOPE_ARGS)
    assert isinstance(telescope, ITelescope)
    assert isinstance(telescope, IFitsHeaderBefore)
    assert isinstance(telescope, IOffsetsRaDec)
    assert isinstance(telescope, ISyncTarget)


def test_import_focuser_and_instantiate() -> None:
    focuser = AlpacaFocuser(server="localhost", port=11111, device_type="focuser", device=0)
    assert isinstance(focuser, IFocuser)
    assert isinstance(focuser, IFitsHeaderBefore)


def test_import_dome_and_instantiate() -> None:
    dome = AlpacaDome(server="localhost", port=11111, device_type="dome", device=0)
    assert isinstance(dome, IDome)
