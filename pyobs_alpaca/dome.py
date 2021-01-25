import logging
import threading
from typing import Tuple

from requests import ConnectTimeout

from pyobs.events import RoofOpenedEvent, RoofClosingEvent
from pyobs.mixins import FollowMixin

from pyobs.interfaces import IMotion, IAltAz
from pyobs.modules import timeout
from pyobs.modules.roof import BaseDome
from pyobs.utils.threads import LockWithAbort
from .device import AlpacaDevice

log = logging.getLogger('pyobs')


class AlpacaDome(FollowMixin, BaseDome, AlpacaDevice):
    def __init__(self, tolerance: float = 3, park_az: float = 180, follow: str = None,
                 *args, **kwargs):
        """Initializes a new ASCOM Alpaca telescope.

        Args:
            tolerance: Tolerance for azimuth.
            park_az: Azimuth for park position.
            follow: Name of other device (e.g. telescope) to follow.
        """
        BaseDome.__init__(self, *args, **kwargs, motion_status_interfaces=['IDome'])
        AlpacaDevice.__init__(self, *args, **kwargs)

        # store
        self._tolerance = tolerance
        self._park_az = park_az

        # move locks
        self._lock_shutter = threading.RLock()
        self._abort_shutter = threading.Event()
        self._lock_move = threading.RLock()
        self._abort_move = threading.Event()

        # status
        self._shutter = None
        self._altitude = 0
        self._azimuth = 0
        self._set_az = 0

        # start thread
        self._add_thread_func(self._update_status)

        # mixins
        FollowMixin.__init__(self, device=follow, interval=10, tolerance=tolerance, mode=IAltAz)

    def open(self):
        """Open module."""
        BaseDome.open(self)

        # init status to IDLE
        self._change_motion_status(IMotion.Status.IDLE)

    @timeout(1200000)
    def init(self, *args, **kwargs):
        """Open dome.

        Raises:
            ValueError if dome cannot be opened.
        """

        # acquire lock
        with LockWithAbort(self._lock_shutter, self._abort_shutter):
            # log
            log.info('Opening dome...')
            self._change_motion_status(IMotion.Status.INITIALIZING)

            # execute command
            self.put('OpenShutter')

            # set new status
            log.info('Dome opened.')
            self._change_motion_status(IMotion.Status.POSITIONED)
            self.comm.send_event(RoofOpenedEvent())

    @timeout(1200000)
    def park(self, *args, **kwargs):
        """Close dome.

        Raises:
            ValueError if dome cannot be opened.
        """

        # acquire lock
        with LockWithAbort(self._lock_shutter, self._abort_shutter):
            # log
            log.info('Closing dome...')
            self._change_motion_status(IMotion.Status.PARKING)
            self.comm.send_event(RoofClosingEvent())

            # send command for closing shutter
            self.put('CloseShutter')

            # set new status
            log.info('Dome closed.')
            self._change_motion_status(IMotion.Status.PARKED)

    def _move(self, az: float, abort: threading.Event):
        """Move the roof and wait for it.

        Args:
            az: Azimuth to move to.
            abort: Abort event.
        """

        # execute command
        self.put('SlewToAzimuth', Azimuth=az)

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
            abort.wait(1)

        # finished
        log.info('Moved to az=%.2f.', az)

    @timeout(1200000)
    def move_altaz(self, alt: float, az: float, *args, **kwargs):
        """Moves to given coordinates.

        Args:
            alt: Alt in deg to move to.
            az: Az in deg to move to.

        Raises:
            ValueError: If device could not move.
        """

        # only move, when ready
        if not self.is_ready():
            log.warning('Dome not ready, ignoring slew command.')
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
        with LockWithAbort(self._lock_move, self._abort_move):
            # store altitude
            self._altitude = alt

            # change status to TRACKING or SLEWING, depending on whether we're tracking
            self._change_motion_status(IMotion.Status.TRACKING if tracking else IMotion.Status.SLEWING)

            # move dome
            self._move(az, self._abort_move)

            # change status to TRACKING or POSITIONED, depending on whether we're tracking
            self._change_motion_status(IMotion.Status.TRACKING if self.is_following else IMotion.Status.POSITIONED)

    def get_altaz(self, *args, **kwargs) -> Tuple[float, float]:
        """Returns current Alt and Az.

        Returns:
            Tuple of current Alt and Az in degrees.
        """
        return self._altitude, self._azimuth

    def stop_motion(self, device: str = None, *args, **kwargs):
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        # not supported, but don't want to raise an exception
        pass

    def is_ready(self, *args, **kwargs) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """

        # check that motion is not in one of the states listed below
        return self.get_motion_status() not in [IMotion.Status.PARKED, IMotion.Status.INITIALIZING,
                                                IMotion.Status.PARKING, IMotion.Status.ERROR, IMotion.Status.UNKNOWN]

    def _update_status(self):
        """Update status from dome."""

        # loop forever
        while not self.closing.is_set():
            # get azimuth
            try:
                self._azimuth = self.get('Azimuth')
            except (ValueError, ConnectTimeout):
                # ignore it
                pass

            # sleep a little
            self.closing.wait(2)

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
