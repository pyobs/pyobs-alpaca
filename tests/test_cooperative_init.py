"""Regression tests for the cooperative-super()-chain conversion of AlpacaDome, AlpacaFocuser,
and AlpacaTelescope.

Each of these classes used to make separate, explicit calls to its Module/Base and mixin
__init__ methods with unfiltered kwargs, instead of a single cooperative super().__init__()
call. Against pyobs-core's cooperative __init__ chains, that pattern either drops kwargs a
sibling mixin declares (leaking them to object.__init__()) or re-threads already-consumed
kwargs through a second, redundant call -- both raise TypeError. If that happens here, these
constructor calls themselves raise instead of returning.
"""

from pyobs.mixins.follow import FollowMixin
from pyobs.utils.enums import MotionStatus

from pyobs_alpaca import AlpacaDome, AlpacaFocuser, AlpacaTelescope

_DEVICE_ARGS = dict(server="localhost", port=11111, device_type="dome", device=0)


def test_dome_constructor_threads_kwargs_cooperatively() -> None:
    dome = AlpacaDome(tolerance=5, follow="telescope", **_DEVICE_ARGS)
    assert dome.motion_status() == MotionStatus.UNKNOWN
    assert isinstance(dome, FollowMixin)
    assert dome.is_following is True
    assert dome._tolerance == 5


def test_focuser_constructor_threads_kwargs_cooperatively() -> None:
    focuser = AlpacaFocuser(**{**_DEVICE_ARGS, "device_type": "focuser"})
    assert focuser.motion_status() == MotionStatus.UNKNOWN


def test_telescope_constructor_threads_kwargs_cooperatively() -> None:
    telescope = AlpacaTelescope(
        settle_time=1.5,
        fits_namespaces={"instrument": None},
        **{**_DEVICE_ARGS, "device_type": "telescope"},
    )
    assert telescope.motion_status() == MotionStatus.UNKNOWN
    assert telescope._FitsNamespaceMixin__namespaces == {"instrument": None}
