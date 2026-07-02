import asyncio
import logging
import time
from typing import Any

from pyobs.events import RoofClosingEvent, RoofOpenedEvent
from pyobs.interfaces import AltAzState, IPointingAltAz, IReady, ReadyState
from pyobs.mixins import FollowMixin
from pyobs.modules import timeout
from pyobs.modules.roof import BaseDome
from pyobs.utils import exceptions as exc
from pyobs.utils.enums import MotionStatus
from pyobs.utils.parallel import event_wait
from pyobs.utils.threads import LockWithAbort

from .device import AlpacaDevice

log = logging.getLogger("pyobs")


class AlpacaDome(FollowMixin, BaseDome):
    __module__ = "pyobs_alpaca"

    def __init__(self, tolerance: float = 3, park_az: float = 180, follow: str | None = None, **kwargs: Any):
        """Initializes a new ASCOM Alpaca dome.

        Args:
            tolerance: Tolerance for azimuth.
            park_az: Azimuth for park position.
            follow: Name of other device (e.g. telescope) to follow.
        """
        BaseDome.__init__(self, **kwargs, motion_status_interfaces=["IDome"])

        # device
        self._device = self.add_child_object(AlpacaDevice, **kwargs)

        # store
        self._tolerance = tolerance
        self._park_az = park_az

        # move locks
        self._lock_shutter = asyncio.Lock()
        self._abort_shutter = asyncio.Event()
        self._lock_move = asyncio.Lock()
        self._abort_move = asyncio.Event()

        # status
        self._shutter = None
        self._altitude = 0.0
        self._azimuth = 0.0
        self._set_az = 0.0

        # start thread
        self.add_background_task(self._update_status)

        # mixins
        FollowMixin.__init__(
            self, device=follow, interval=10, tolerance=tolerance, mode=IPointingAltAz, only_follow_when_ready=False
        )

    async def open(self) -> None:
        """Open module."""
        await BaseDome.open(self)
        await self._change_motion_status(MotionStatus.IDLE)
        await self.comm.set_state(IReady, ReadyState(ready=self._device.connected))

    async def _send_open_dome(self) -> None:
        """Send command to open dome."""
        try:
            await self._device.put("OpenShutter")
        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise exc.InitError("Could not open dome.")

    @timeout(1200000)
    async def init(self, **kwargs: Any) -> None:
        """Open dome."""

        if not self.is_weather_good():
            raise exc.InitError("Weather seems to be bad.")

        if self.motion_status() == MotionStatus.INITIALIZING:
            return

        async with LockWithAbort(self._lock_shutter, self._abort_shutter):
            log.info("Opening dome...")
            await self._change_motion_status(MotionStatus.INITIALIZING)

            await self._send_open_dome()
            time_attempt = time.time()

            status = None
            while status != 0:
                if status == 4:
                    log.error("Could not open dome.")
                    await self._change_motion_status(MotionStatus.UNKNOWN)
                    return

                await event_wait(self._abort_shutter, 1)
                try:
                    status = await self._device.get("ShutterStatus")
                except ConnectionError:
                    await self._change_motion_status(MotionStatus.UNKNOWN)
                    raise exc.InitError("Could not open dome.")

                if time.time() - time_attempt > 10:
                    await self._send_open_dome()
                    time_attempt = time.time()

            log.info("Dome opened.")
            await self._change_motion_status(MotionStatus.POSITIONED)
            await self.comm.send_event(RoofOpenedEvent())

    async def _send_close_dome(self) -> None:
        """Send command to close dome."""
        try:
            await self._device.put("CloseShutter")
            await self._device.put("SlewToAzimuth", Azimuth=0)
        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise exc.ParkError("Could not close dome.")

    @timeout(1200000)
    async def park(self, **kwargs: Any) -> None:
        """Close dome."""

        if self.motion_status() == MotionStatus.PARKING:
            return

        async with LockWithAbort(self._lock_shutter, self._abort_shutter):
            log.info("Closing dome...")
            await self._change_motion_status(MotionStatus.PARKING)
            await self.comm.send_event(RoofClosingEvent())

            await self._send_close_dome()
            time_attempt = time.time()

            status = None
            while status != 1:
                if status == 4:
                    log.error("Could not close dome.")
                    await self._change_motion_status(MotionStatus.UNKNOWN)
                    raise exc.ParkError("Could not close dome.")

                await event_wait(self._abort_shutter, 1)
                try:
                    status = await self._device.get("ShutterStatus")
                except ConnectionError:
                    await self._change_motion_status(MotionStatus.UNKNOWN)
                    raise exc.ParkError("Could not close dome.")

                if time.time() - time_attempt > 10:
                    await self._send_close_dome()
                    time_attempt = time.time()

            log.info("Dome closed.")
            await self._change_motion_status(MotionStatus.PARKED)

    async def _move(self, az: float, abort: asyncio.Event) -> None:
        """Move the dome to the given azimuth."""

        try:
            await self._device.put("SlewToAzimuth", Azimuth=self._adjust_azimuth(az))
        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise exc.MoveError("Could not move dome.")

        log_timer = 0
        while 180 - abs(abs(az - self._azimuth) - 180) > self._tolerance:
            if abort.is_set():
                raise InterruptedError("Moving dome aborted.")

            if log_timer == 0:
                log.info(
                    "Moving dome from current az=%.2f° to %.2f° (%.2f° left)...",
                    self._azimuth,
                    az,
                    180 - abs(abs(az - self._azimuth) - 180),
                )
            log_timer = (log_timer + 1) % 10

            await event_wait(abort, 1)

        log.info("Moved to az=%.2f.", az)

    @timeout(1200000)
    async def move_altaz(self, alt: float, az: float, **kwargs: Any) -> None:
        """Moves to given coordinates."""

        # do nothing if not ready
        bad_states = [
            MotionStatus.PARKED,
            MotionStatus.INITIALIZING,
            MotionStatus.PARKING,
            MotionStatus.ERROR,
            MotionStatus.UNKNOWN,
        ]
        if not self._device.connected or self.motion_status() in bad_states:
            return

        if az == self._set_az:
            return
        self._set_az = az

        large_move = abs(az - self._azimuth) > 2.0 * self._tolerance
        tracking = self.is_following and not large_move

        async with LockWithAbort(self._lock_move, self._abort_move):
            self._altitude = alt
            await self._change_motion_status(MotionStatus.TRACKING if tracking else MotionStatus.SLEWING)
            await self._move(az, self._abort_move)
            await self._change_motion_status(MotionStatus.TRACKING if self.is_following else MotionStatus.POSITIONED)

    async def stop_motion(self, device: str | None = None, **kwargs: Any) -> None:
        """Stop the motion."""
        pass

    async def _update_status(self) -> None:
        """Update status from dome."""
        while True:
            try:
                self._azimuth = self._adjust_azimuth(await self._device.get("Azimuth"))
                await self.comm.set_state(IPointingAltAz, AltAzState(alt=self._altitude, az=self._azimuth))
            except ConnectionError:
                pass

            bad_states = [
                MotionStatus.PARKED,
                MotionStatus.INITIALIZING,
                MotionStatus.PARKING,
                MotionStatus.ERROR,
                MotionStatus.UNKNOWN,
            ]
            ready = self._device.connected and self.motion_status() not in bad_states
            await self.comm.set_state(IReady, ReadyState(ready=ready))

            await asyncio.sleep(2)

    @staticmethod
    def _adjust_azimuth(az: float) -> float:
        """Baader measures azimuth as West of South — convert both ways."""
        az += 180
        if az >= 360:
            az -= 360
        return az


__all__ = ["AlpacaDome"]
