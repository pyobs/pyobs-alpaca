import asyncio
import logging
from typing import Tuple, Optional, Any

from pyobs.events import RoofOpenedEvent, RoofClosingEvent
from pyobs.mixins import FollowMixin
from pyobs.interfaces import IPointingAltAz
from pyobs.modules import timeout
from pyobs.modules.roof import BaseDome
from pyobs.utils.enums import MotionStatus
from pyobs.utils.parallel import event_wait
from pyobs.utils.threads import LockWithAbort
from .device import AlpacaDevice

log = logging.getLogger('pyobs')


class AlpacaDome(FollowMixin, BaseDome):
    __module__ = 'pyobs_alpaca'

    def __init__(self, tolerance: float = 3, park_az: float = 180, follow: Optional[str] = None, **kwargs: Any):
        """Initializes a new ASCOM Alpaca telescope.

        Args:
            tolerance: Tolerance for azimuth.
            park_az: Azimuth for park position.
            follow: Name of other device (e.g. telescope) to follow.
        """
        BaseDome.__init__(self, **kwargs, motion_status_interfaces=['IDome'])

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
        self._altitude = 0.
        self._azimuth = 0.
        self._set_az = 0.

        # start thread
        self.add_background_task(self._update_status)

        # mixins
        FollowMixin.__init__(self, device=follow, interval=10, tolerance=tolerance, mode=IPointingAltAz,
                             only_follow_when_ready=False)

    async def open(self) -> None:
        """Open module."""
        await BaseDome.open(self)

        # init status to IDLE
        await self._change_motion_status(MotionStatus.IDLE)

    @timeout(1200000)
    async def init(self, **kwargs: Any) -> None:
        """Open dome.

        Raises:
            ValueError: If dome cannot be opened.
        """

        # if already opening, ignore
        if await self.get_motion_status() == MotionStatus.INITIALIZING:
            return

        # acquire lock
        async with LockWithAbort(self._lock_shutter, self._abort_shutter):
            # log
            log.info('Opening dome...')
            await self._change_motion_status(MotionStatus.INITIALIZING)

            # execute command
            await self._device.put('OpenShutter')

            # wait for it
            status = None
            while status != 0:
                # error?
                if status == 4:
                    log.error('Could not open dome.')
                    await self._change_motion_status(MotionStatus.UNKNOWN)
                    return

                # wait a little and update
                await event_wait(self._abort_shutter, 1)
                status = await self._device.get('ShutterStatus')

            # set new status
            log.info('Dome opened.')
            await self._change_motion_status(MotionStatus.POSITIONED)
            await self.comm.send_event(RoofOpenedEvent())

    @timeout(1200000)
    async def park(self, **kwargs: Any) -> None:
        """Close dome.

        Raises:
            ValueError: If dome cannot be opened.
        """

        # if already closing, ignore
        if await self.get_motion_status() == MotionStatus.PARKING:
            return

        # acquire lock
        async with LockWithAbort(self._lock_shutter, self._abort_shutter):
            # log
            log.info('Closing dome...')
            await self._change_motion_status(MotionStatus.PARKING)
            await self.comm.send_event(RoofClosingEvent())

            # send command for closing shutter and rotate to South
            await self._device.put('CloseShutter')
            await self._device.put('SlewToAzimuth', Azimuth=0)

            # wait for it
            status = None
            while status != 1:
                # error?
                if status == 4:
                    log.error('Could not close dome.')
                    await self._change_motion_status(MotionStatus.UNKNOWN)
                    return

                # wait a little and update
                await event_wait(self._abort_shutter, 1)
                status = await self._device.get('ShutterStatus')

            # set new status
            log.info('Dome closed.')
            await self._change_motion_status(MotionStatus.PARKED)

    async def _move(self, az: float, abort: asyncio.Event) -> None:
        """Move the roof and wait for it.

        Args:
            az: Azimuth to move to.
            abort: Abort event.
        """

        # execute command
        await self._device.put('SlewToAzimuth', Azimuth=self._adjust_azimuth(az))

        # wait for it
        log_timer = 0
        while 180 - abs(abs(az - self._azimuth) - 180) > self._tolerance:
            # abort?
            if abort.is_set():
                return

            # log?
            if log_timer == 0:
                log.info('Moving dome from current az=%.2f° to %.2f° (%.2f° left)...',
                         self._azimuth, az, 180 - abs(abs(az - self._azimuth) - 180))
            log_timer += 1
            if log_timer == 10:
                log_timer = 0

            # wait a little
            await event_wait(abort, 1)

        # finished
        log.info('Moved to az=%.2f.', az)

    @timeout(1200000)
    async def move_altaz(self, alt: float, az: float, **kwargs: Any) -> None:
        """Moves to given coordinates.

        Args:
            alt: Alt in deg to move to.
            az: Az in deg to move to.

        Raises:
            ValueError: If device could not move.
        """

        # do nothing, if not ready
        if not await self.is_ready():
            return

        # destination az already set?
        if az == self._set_az:
            return
        self._set_az = az

        # is this a larger move?
        large_move = abs(az - self._azimuth) > 2. * self._tolerance

        # decide, whether we're tracking or just slewing
        tracking = self.is_following and not large_move

        # acquire lock
        async with LockWithAbort(self._lock_move, self._abort_move):
            # store altitude
            self._altitude = alt

            # change status to TRACKING or SLEWING, depending on whether we're tracking
            await self._change_motion_status(MotionStatus.TRACKING if tracking else MotionStatus.SLEWING)

            # move dome
            await self._move(az, self._abort_move)

            # change status to TRACKING or POSITIONED, depending on whether we're tracking
            await self._change_motion_status(MotionStatus.TRACKING if self.is_following else MotionStatus.POSITIONED)

    async def get_altaz(self, **kwargs: Any) -> Tuple[float, float]:
        """Returns current Alt and Az.

        Returns:
            Tuple of current Alt and Az in degrees.
        """
        return self._altitude, self._azimuth

    async def stop_motion(self, device: Optional[str] = None, **kwargs: Any) -> None:
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        # not supported, but don't want to raise an exception
        pass

    async def is_ready(self, **kwargs: Any) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """

        # check that motion is not in one of the states listed below
        states = [MotionStatus.PARKED, MotionStatus.INITIALIZING, MotionStatus.PARKING,
                  MotionStatus.ERROR, MotionStatus.UNKNOWN]
        return self._device.connected and await self.get_motion_status() not in states

    async def _update_status(self) -> None:
        """Update status from dome."""

        # loop forever
        while True:
            # get azimuth
            try:
                self._azimuth = self._adjust_azimuth(await self._device.get('Azimuth'))
            except ValueError:
                # ignore it
                pass

            # sleep a little
            await asyncio.sleep(2)

    @staticmethod
    def _adjust_azimuth(az: float) -> float:
        """Baader measures azimuth as West of South, so we need to convert it. This works both ways.

        Args:
            az: Azimuth.

        Returns:
            Converted azimuth.
        """
        az += 180
        if az >= 360:
            az -= 360
        return az


__all__ = ['AlpacaDome']
