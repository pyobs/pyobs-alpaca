import logging
import threading
import numpy as np

from pyobs.mixins import FitsNamespaceMixin

from pyobs.interfaces import IFitsHeaderProvider, IMotion, IRaDecOffsets, ISyncTarget
from pyobs.modules import timeout
from pyobs.modules.telescope.basetelescope import BaseTelescope
from pyobs.utils.threads import LockWithAbort
from .device import AlpacaDevice

log = logging.getLogger('pyobs')


class AlpacaTelescope(BaseTelescope, FitsNamespaceMixin, IFitsHeaderProvider, IRaDecOffsets, ISyncTarget):
    def __init__(self, settle_time: float = 3.0, *args, **kwargs):
        """Initializes a new ASCOM Alpaca telescope.

        Args:
            settle_time: Time in seconds to wait after slew before finishing.
        """
        BaseTelescope.__init__(self, *args, **kwargs, motion_status_interfaces=['ITelescope'])

        # device
        self._device = AlpacaDevice(*args, **kwargs)
        self._add_child_object(self._device)

        # variables
        self._settle_time = settle_time

        # offsets in ra/dec
        self._offset_ra = 0
        self._offset_dec = 0

        # mixins
        FitsNamespaceMixin.__init__(self, *args, **kwargs)

    def open(self):
        """Open module.

        Raises:
            ValueError: If cannot connect to device.
        """
        BaseTelescope.open(self)

        # initial status
        status = self._get_status()
        if status == IMotion.Status.UNKNOWN:
            log.error('Could not fetch initial status from telescope.')
        self._change_motion_status(status)

    def _get_status(self) -> IMotion.Status:
        """Get status of telescope."""

        try:
            if self._device.get('AtPark'):
                return IMotion.Status.PARKED
            elif self._device.get('Slewing'):
                return IMotion.Status.SLEWING
            elif self._device.get('Tracking'):
                return IMotion.Status.TRACKING
            else:
                return IMotion.Status.IDLE

        except ValueError:
            return IMotion.Status.UNKNOWN

    def _check_status_thread(self):
        """Periodically check status of telescope."""

        while not self.closing.is_set():
            # only check, if status is unknown
            if self.get_motion_status() == IMotion.Status.UNKNOWN:
                self._change_motion_status(self._get_status())

            # wait a little
            self.closing.wait(5)

    @timeout(60000)
    def init(self, *args, **kwargs):
        """Initialize telescope.

        Raises:
            ValueError: If telescope could not be initialized.
        """

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # change status
            log.info('Initializing telescope...')
            self._change_motion_status(IMotion.Status.INITIALIZING)

            # move to init position
            try:
                self._move_altaz(30, 180., self._abort_move)
                self._change_motion_status(IMotion.Status.IDLE)
                log.info('Telescope initialized.')

            except ValueError:
                self._change_motion_status(IMotion.Status.UNKNOWN)
                raise ValueError('Could not init telescope.')


    @timeout(60000)
    def park(self, *args, **kwargs):
        """Park telescope.

        Raises:
            ValueError: If telescope could not be parked.
        """

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # change status
            log.info('Parking telescope...')
            self._change_motion_status(IMotion.Status.PARKING)

            # park telescope
            try:
                self._device.put('Park')
                self._change_motion_status(IMotion.Status.PARKED)
                log.info('Telescope parked.')

            except ValueError:
                self._change_motion_status(IMotion.Status.UNKNOWN)
                raise ValueError('Could not park telescope.')

    def _move_altaz(self, alt: float, az: float, abort_event: threading.Event):
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
            self._change_motion_status(IMotion.Status.UNKNOWN)
            raise ValueError('Could not move telescope to Alt/Az.')

    def _move_radec(self, ra: float, dec: float, abort_event: threading.Event):
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
            self._change_motion_status(IMotion.Status.UNKNOWN)
            raise ValueError('Could not move telescope to RA/Dec.')

    @timeout(10000)
    def set_radec_offsets(self, dra: float, ddec: float, *args, **kwargs):
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
            self._change_motion_status(IMotion.Status.SLEWING)
            log.info('Setting telescope offsets to dRA=%.2f", dDec=%.2f"...', dra * 3600., ddec * 3600.)

            # get current coordinates (with old offsets)
            ra, dec = self._device.get_radec()

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
                self._change_motion_status(IMotion.Status.TRACKING)
                log.info('Reached destination.')

            except ValueError:
                self._change_motion_status(IMotion.Status.UNKNOWN)
                raise ValueError('Could not move telescope to RA/Dec offset.')

    def get_radec_offsets(self, *args, **kwargs) -> (float, float):
        """Get RA/Dec offset.

        Returns:
            Tuple with RA and Dec offsets.
        """
        return self._offset_ra, self._offset_dec

    def get_radec(self, *args, **kwargs) -> (float, float):
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

    def get_altaz(self, *args, **kwargs) -> (float, float):
        """Returns current Alt and Az.

        Returns:
            Tuple of current Alt and Az in degrees.
        """

        try:
            # create sky coordinates
            return self._device.get('Altitude'), self._device.get('Azimuth')

        except ValueError:
            raise ValueError('Could not fetch Alt/Az.')

    def stop_motion(self, device: str = None, *args, **kwargs):
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        try:
            # stop telescope
            self._abort_move.set()
            self._device.put('AbortSlew')
            self._device.put('Tracking', Tracking=False)
            self._change_motion_status(IMotion.Status.IDLE)

        except ValueError:
            self._change_motion_status(IMotion.Status.UNKNOWN)
            raise ValueError('Could not stop telescope.')

    def is_ready(self, *args, **kwargs) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """

        # check that motion is not in one of the states listed below
        return self._device.connected and \
               self.get_motion_status() not in [IMotion.Status.PARKED, IMotion.Status.INITIALIZING,
                                                IMotion.Status.PARKING, IMotion.Status.ERROR, IMotion.Status.UNKNOWN]

    def sync_target(self, *args, **kwargs):
        """Synchronize telescope on current target using current offsets."""

        # get current RA/Dec without offsets
        ra, dec = self._device.get_radec()

        # sync
        self._device.put('SyncToCoordinates', RightAscension=ra / 15., Declination=dec)

    def get_fits_headers(self, namespaces: list = None, *args, **kwargs) -> dict:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """

        # get headers from base
        hdr = BaseTelescope.get_fits_headers(self)

        try:
            # get offsets
            ra_off, dec_off = self.get_radec_offsets()

            # define values to request
            hdr['RAOFF'] = (ra_off, 'RA offset [deg]')
            hdr['DECOFF'] = (dec_off, 'Dec offset [deg]')

            # return it
            return self._filter_fits_namespace(hdr, namespaces=namespaces, **kwargs)

        except ValueError:
            return {}


__all__ = ['AlpacaTelescope']
