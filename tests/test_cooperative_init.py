"""Regression tests for the cooperative-super()-chain conversion of AlpacaDome, AlpacaFocuser,
and AlpacaTelescope.

Each of these classes used to make separate, explicit calls to its Module/Base and mixin
__init__ methods with unfiltered kwargs, instead of a single cooperative super().__init__()
call. Against pyobs-core's cooperative __init__ chains, that pattern either drops kwargs a
sibling mixin declares (leaking them to object.__init__()) or re-threads already-consumed
kwargs through a second, redundant call -- both raise TypeError. If that happens here, these
constructor calls themselves raise instead of returning.
"""

import inspect
from collections.abc import Callable
from typing import Any

from pyobs.mixins.follow import FollowMixin
from pyobs.object import Object
from pyobs.utils.enums import MotionStatus

from pyobs_alpaca import AlpacaDome, AlpacaFocuser, AlpacaTelescope
from pyobs_alpaca.device import DEVICE_INIT_KWARGS, OBJECT_SHARED_KWARGS, AlpacaDevice

_DEVICE_ARGS = dict(server="localhost", port=11111, device_type="dome", device=0)


def _param_names(func: Callable[..., Any]) -> set[str]:
    return {name for name in inspect.signature(func).parameters if name not in ("self", "kwargs")}


def test_device_init_kwargs_matches_alpaca_device_signature() -> None:
    """Keeps DEVICE_INIT_KWARGS honest: if AlpacaDevice's constructor ever gains/loses a param
    without updating this set, callers threading a module's **kwargs through both AlpacaDevice
    and the module's own super().__init__() chain would silently start leaking kwargs to
    object.__init__() again -- exactly the bug this whole conversion fixes."""
    assert DEVICE_INIT_KWARGS == _param_names(AlpacaDevice.__init__)


def test_object_shared_kwargs_matches_object_signature() -> None:
    """Same invariant as above, for the Object-level kwargs AlpacaDevice forwards cooperatively
    (and that the module chain also needs, e.g. comm/vfs)."""
    assert OBJECT_SHARED_KWARGS == _param_names(Object.__init__)


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
    assert telescope._FitsNamespaceMixin__namespaces == {"instrument": None}  # type: ignore[attr-defined]
