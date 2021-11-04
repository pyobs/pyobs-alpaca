import logging
import threading
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

from pyobs.events import OffsetsRaDecEvent
from pyobs.mixins import FitsNamespaceMixin
from pyobs.interfaces import IFitsHeaderBefore, IOffsetsRaDec, ISyncTarget
from pyobs.modules import timeout
from pyobs.modules.telescope.basetelescope import BaseTelescope
from pyobs.utils.enums import MotionStatus
from pyobs.utils.threads import LockWithAbort
from .device import AlpacaDevice

log = logging.getLogger('pyobs')


class AlpacaTelescope(BaseTelescope, FitsNamespaceMixin, IFitsHeaderBefore, IOffsetsRaDec, ISyncTarget):
    __module__ = 'pyobs_alpaca'

    def __init__(self, settle_time: float = 3.0, **kwargs: Any):
        """Initializes a new ASCOM Alpaca telescope.

        Args:
            settle_time: Time in seconds to wait after slew before finishing.
        """
        BaseTelescope.__init__(self, **kwargs, motion_status_interfaces=['ITelescope'])

        # device
        self._device = AlpacaDevice(**kwargs)
        self.add_child_object(self._device)

        # variables
        self._settle_time = settle_time

        # offsets in ra/dec
        self._offset_ra = 0.
        self._offset_dec = 0.

        # mixins
        FitsNamespaceMixin.__init__(self, **kwargs)

    def open(self) -> None:
        """Open module.

        Raises:
            ValueError: If cannot connect to device.
        """
        BaseTelescope.open(self)

        # initial status
        status = self._get_status()
        if status == MotionStatus.UNKNOWN:
            log.error('Could not fetch initial status from telescope.')
        self._change_motion_status(status)

    def _get_status(self) -> MotionStatus:
        """Get status of telescope."""

        try:
            if self._device.get('AtPark'):
                return MotionStatus.PARKED
            elif self._device.get('Slewing'):
                return MotionStatus.SLEWING
            elif self._device.get('Tracking'):
                return MotionStatus.TRACKING
            else:
                return MotionStatus.IDLE

        except ValueError:
            return MotionStatus.UNKNOWN

    def _check_status_thread(self) -> None:
        """Periodically check status of telescope."""

        while not self.closing.is_set():
            # only check, if status is unknown
            if self.get_motion_status() == MotionStatus.UNKNOWN:
                self._change_motion_status(self._get_status())

            # wait a little
            self.closing.wait(5)

    @timeout(60000)
    def init(self, **kwargs: Any) -> None:
        """Initialize telescope.

        Raises:
            ValueError: If telescope could not be initialized.
        """

        # if already initializing, ignore
        if self.get_motion_status() == MotionStatus.INITIALIZING:
            return

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # change status
            log.info('Initializing telescope...')
            self._change_motion_status(MotionStatus.INITIALIZING)

            # move to init position
            try:
                self._move_altaz(30, 180., self._abort_move)
                self._change_motion_status(MotionStatus.IDLE)
                log.info('Telescope initialized.')

            except ValueError:
                self._change_motion_status(MotionStatus.UNKNOWN)
                raise ValueError('Could not init telescope.')

    @timeout(60000)
    def park(self, **kwargs: Any) -> None:
        """Park telescope.

        Raises:
            ValueError: If telescope could not be parked.
        """

        # if already parking, ignore
        if self.get_motion_status() == MotionStatus.PARKING:
            return

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # change status
            log.info('Parking telescope...')
            self._change_motion_status(MotionStatus.PARKING)

            # park telescope
            try:
                self._device.put('Park', timeout=60)
                self._change_motion_status(MotionStatus.PARKED)
                log.info('Telescope parked.')

            except ValueError:
                self._change_motion_status(MotionStatus.UNKNOWN)
                raise ValueError('Could not park telescope.')

    def _move_altaz(self, alt: float, az: float, abort_event: threading.Event) -> None:
        """Actually moves to given coordinates. Must be implemented by derived classes.

        Args:
            alt: Alt in deg to move to.
            az: Az in deg to move to.
            abort_event: Event that gets triggered when movement should be aborted.

        Raises:
            Exception: On error.
        """

        # reset offsets
        self._offset_ra, self._offset_dec = 0, 0

        try:
            # start slewing
            self._device.put('Tracking', Tracking=False)
            self._device.put('SlewToAltAzAsync', Azimuth=az, Altitude=alt)

            # wait for it
            while self._device.get('Slewing'):
                abort_event.wait(1)
            self._device.put('Tracking', Tracking=False)

            # wait settle time
            abort_event.wait(self._settle_time)

        except ValueError:
            self._change_motion_status(MotionStatus.UNKNOWN)
            raise ValueError('Could not move telescope to Alt/Az.')

    def _move_radec(self, ra: float, dec: float, abort_event: threading.Event) -> None:
        """Actually starts tracking on given coordinates. Must be implemented by derived classes.

        Args:
            ra: RA in deg to track.
            dec: Dec in deg to track.
            abort_event: Event that gets triggered when movement should be aborted.

        Raises:
            Exception: On any error.
        """

        # reset offsets
        self._offset_ra, self._offset_dec = 0, 0

        try:
            # start slewing
            self._device.put('Tracking', Tracking=True)
            self._device.put('SlewToCoordinatesAsync', RightAscension=ra / 15., Declination=dec)

            # wait for it
            while self._device.get('Slewing'):
                abort_event.wait(1)
            self._device.put('Tracking', Tracking=True)

            # wait settle time
            abort_event.wait(self._settle_time)

        except ValueError:
            self._change_motion_status(MotionStatus.UNKNOWN)
            raise ValueError('Could not move telescope to RA/Dec.')

    @timeout(10000)
    def set_offsets_radec(self, dra: float, ddec: float, **kwargs: Any) -> None:
        """Move an RA/Dec offset.

        Args:
            dra: RA offset in degrees.
            ddec: Dec offset in degrees.

        Raises:
            ValueError: If offset could not be set.
        """

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # start slewing
            self._change_motion_status(MotionStatus.SLEWING)
            log.info('Setting telescope offsets to dRA=%.2f", dDec=%.2f"...', dra * 3600., ddec * 3600.)
            self.comm.send_event(OffsetsRaDecEvent(ra=dra, dec=ddec))

            # get current coordinates (with old offsets)
            ra, dec = self.get_radec()

            # store offsets
            self._offset_ra = dra
            self._offset_dec = ddec

            # add offset
            ra += float(self._offset_ra / np.cos(np.radians(dec)))
            dec += float(self._offset_dec)

            try:
                # start slewing
                self._device.put('Tracking', Tracking=True)
                self._device.put('SlewToCoordinatesAsync', RightAscension=ra / 15., Declination=dec)

                # wait for it
                while self._device.get('Slewing'):
                    self._abort_move.wait(1)
                self._device.put('Tracking', Tracking=True)

                # wait settle time
                self._abort_move.wait(self._settle_time)

                # finish slewing
                self._change_motion_status(MotionStatus.TRACKING)
                log.info('Reached destination.')

            except ValueError:
                self._change_motion_status(MotionStatus.UNKNOWN)
                raise ValueError('Could not move telescope to RA/Dec offset.')

    def get_offsets_radec(self, **kwargs: Any) -> Tuple[float, float]:
        """Get RA/Dec offset.

        Returns:
            Tuple with RA and Dec offsets.
        """
        return self._offset_ra, self._offset_dec

    def get_radec(self, **kwargs: Any) -> Tuple[float, float]:
        """Returns current RA and Dec.

        Returns:
            Tuple of current RA and Dec in degrees.
        """

        try:
            # get position
            ra, dec = self._device.get('RightAscension'), self._device.get('Declination')

            # correct ra offset by decl
            ra_off = self._offset_ra / np.cos(np.radians(dec))

            # return coordinates without offsets
            return float(ra * 15 - ra_off), float(dec - self._offset_dec)

        except ValueError:
            raise ValueError('Could not fetch Alt/Az.')

    def get_altaz(self, **kwargs: Any) -> Tuple[float, float]:
        """Returns current Alt and Az.

        Returns:
            Tuple of current Alt and Az in degrees.
        """

        try:
            # correct az
            az = self._device.get('Azimuth') + 180
            if az > 360:
                az -= 360

            # create sky coordinates
            return self._device.get('Altitude'), az

        except ValueError:
            raise ValueError('Could not fetch Alt/Az.')

    def stop_motion(self, device: Optional[str] = None, **kwargs: Any) -> None:
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        try:
            # stop telescope
            self._abort_move.set()
            self._device.put('AbortSlew')
            self._device.put('Tracking', Tracking=False)
            self._change_motion_status(MotionStatus.IDLE)

        except ValueError:
            self._change_motion_status(MotionStatus.UNKNOWN)
            raise ValueError('Could not stop telescope.')

    def is_ready(self, **kwargs: Any) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """

        # check that motion is not in one of the states listed below
        return self._device.connected and \
               self.get_motion_status() not in [MotionStatus.PARKED, MotionStatus.INITIALIZING,
                                                MotionStatus.PARKING, MotionStatus.ERROR, MotionStatus.UNKNOWN]

    def sync_target(self, **kwargs: Any) -> None:
        """Synchronize telescope on current target using current offsets."""

        # get current RA/Dec without offsets
        ra, dec = self.get_radec()

        # sync
        self._device.put('SyncToCoordinates', RightAscension=ra / 15., Declination=dec)

    def get_fits_header_before(self, namespaces: Optional[List[str]] = None, **kwargs: Any) -> Dict[str, Tuple[Any, str]]:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """

        # get headers from base
        hdr = BaseTelescope.get_fits_header_before(self)

        try:
            # get offsets
            ra_off, dec_off = self.get_offsets_radec()

            # define values to request
            hdr['RAOFF'] = (ra_off, 'RA offset [deg]')
            hdr['DECOFF'] = (dec_off, 'Dec offset [deg]')

            # return it
            return self._filter_fits_namespace(hdr, namespaces=namespaces, **kwargs)

        except ValueError:
            return {}


__all__ = ['AlpacaTelescope']
