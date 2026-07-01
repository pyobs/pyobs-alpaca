import asyncio
import logging
from typing import Any

from pyobs.interfaces import FocuserState, IFitsHeaderBefore, IFocuser, IReady, ReadyState
from pyobs.mixins import MotionStatusMixin
from pyobs.modules import Module, timeout
from pyobs.utils import exceptions as exc
from pyobs.utils.enums import MotionStatus
from pyobs.utils.threads import LockWithAbort

from .device import AlpacaDevice

log = logging.getLogger(__name__)


class AlpacaFocuser(MotionStatusMixin, IFocuser, IFitsHeaderBefore, Module):
    __module__ = "pyobs_alpaca"

    def __init__(self, **kwargs: Any):
        Module.__init__(self, **kwargs)

        # device
        self._device = self.add_child_object(AlpacaDevice, **kwargs)

        # variables
        self._focus_offset = 0.0

        # allow to abort motion
        self._lock_motion = asyncio.Lock()
        self._abort_motion = asyncio.Event()

        # init mixins
        MotionStatusMixin.__init__(self, motion_status_interfaces=["IFocuser"])

        # register exception
        exc.register_exception(exc.MotionError, 3, timespan=600, callback=self._default_remote_error_callback)

    async def open(self) -> None:
        """Open module."""
        await Module.open(self)

        # open mixins
        await MotionStatusMixin.open(self)

        # init status
        await self._change_motion_status(MotionStatus.IDLE, interface="IFocuser")
        await self.comm.set_state(IReady, ReadyState(ready=self._device.connected))

    async def init(self, **kwargs: Any) -> None:
        """Initialize device."""
        pass

    async def park(self, **kwargs: Any) -> None:
        """Park device."""
        pass

    async def get_fits_header_before(
        self, namespaces: list[str] | None = None, **kwargs: Any
    ) -> dict[str, tuple[Any, str]]:
        """Returns FITS header for the current status of this module."""

        try:
            pos = await self._device.get("Position")
            step = await self._device.get("StepSize") * 1000.0
            return {"TEL-FOCU": (pos / step, "Focus of telescope [mm]")}
        except ConnectionError as e:
            log.warning("Could not determine focus position: %s", e)
            return {}

    @timeout(60000)
    async def set_focus(self, focus: float, **kwargs: Any) -> None:
        """Sets new focus."""
        await self._set_focus(focus + self._focus_offset)

    async def set_focus_offset(self, offset: float, **kwargs: Any) -> None:
        """Sets focus offset."""

        # get current focus (without offset) directly from device
        try:
            pos = float(await self._device.get("Position"))
            step = float(await self._device.get("StepSize")) * 1000.0
            current_focus = pos / step - self._focus_offset
        except ConnectionError:
            raise exc.MoveError("Could not read focus position.")

        self._focus_offset = offset
        await self._set_focus(current_focus + self._focus_offset)

    async def _set_focus(self, focus: float) -> None:
        """Actually sets new focus."""

        async with LockWithAbort(self._lock_motion, self._abort_motion):
            try:
                step = await self._device.get("StepSize")

                log.info("Moving focus to %.2fmm...", focus)
                await self._change_motion_status(MotionStatus.SLEWING, interface="IFocuser")
                foc = int(focus * step * 1000.0)
                await self._device.put("Move", Position=foc)

                while abs(await self._device.get("Position") - foc) > 10:
                    if self._abort_motion.is_set():
                        await self._device.put("Halt")
                        await self._change_motion_status(MotionStatus.POSITIONED, interface="IFocuser")
                        raise InterruptedError("Setting focus aborted.")
                    await asyncio.sleep(0.1)

                pos = await self._device.get("Position")
                log.info("Reached new focus of %.2fmm.", pos / step / 1000.0)
                await self._change_motion_status(MotionStatus.POSITIONED, interface="IFocuser")

                # publish new state
                await self.comm.set_state(
                    IFocuser, FocuserState(focus=pos / step / 1000.0, focus_offset=self._focus_offset)
                )

            except ConnectionError:
                await self._change_motion_status(MotionStatus.ERROR, interface="IFocuser")
                raise exc.MoveError("Could not move focus.")

    async def stop_motion(self, device: str | None = None, **kwargs: Any) -> None:
        """Stop the motion."""
        await self._device.put("Halt")


__all__ = ["AlpacaFocuser"]
