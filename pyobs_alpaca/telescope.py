import logging
import threading
from astropy.coordinates import SkyCoord, ICRS
from astropy import units as u
import numpy as np
from pyobs.mixins import FitsNamespaceMixin

from pyobs.interfaces import IFitsHeaderProvider, IMotion, IRaDecOffsets, ISyncTarget
from pyobs.modules import timeout
from pyobs.modules.telescope.basetelescope import BaseTelescope
from pyobs.utils.threads import LockWithAbort
from .device import AlpacaDevice

log = logging.getLogger('pyobs')


class AlpacaTelescope(BaseTelescope, FitsNamespaceMixin, IFitsHeaderProvider, IRaDecOffsets, ISyncTarget, AlpacaDevice):
    def __init__(self, settle_time: float = 3.0, *args, **kwargs):
        """Initializes a new ASCOM Alpaca telescope.

        Args:
            settle_time: Time in seconds to wait after slew before finishing.
        """
        BaseTelescope.__init__(self, *args, **kwargs, motion_status_interfaces=['ITelescope'])
        AlpacaDevice.__init__(self, *args, **kwargs)

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
        if self.get('AtPark'):
            self._change_motion_status(IMotion.Status.PARKED)
        if self.get('Slewing'):
            self._change_motion_status(IMotion.Status.SLEWING)
        if self.get('Tracking'):
            self._change_motion_status(IMotion.Status.TRACKING)
        else:
            self._change_motion_status(IMotion.Status.IDLE)

    @timeout(60000)
    def init(self, *args, **kwargs):
        """Initialize telescope.

        Raises:
            ValueError: If telescope could not be initialized.
        """

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # park telescope
            log.info('Initializing telescope...')
            self._change_motion_status(IMotion.Status.INITIALIZING)
            self._move_altaz(30, 0, self._abort_move)
            self._change_motion_status(IMotion.Status.IDLE)
            log.info('Telescope initialized.')

    @timeout(60000)
    def park(self, *args, **kwargs):
        """Park telescope.

        Raises:
            ValueError: If telescope could not be parked.
        """

        # acquire lock
        with LockWithAbort(self._lock_moving, self._abort_move):
            # park telescope
            log.info('Parking telescope...')
            self._change_motion_status(IMotion.Status.PARKING)
            self.put('Park')
            self._change_motion_status(IMotion.Status.PARKED)
            log.info('Telescope parked.')

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

        # correct azimuth by 180 degrees
        az += 180
        if az > 360:
            az -= 360

        # start slewing
        self.put('Tracking', Tracking=False)
        self.put('SlewToAltAzAsync', Azimuth=az, Altitude=alt)

        # wait for it
        while self.get('Slewing'):
            abort_event.wait(1)
        self.put('Tracking', Tracking=False)

        # wait settle time
        abort_event.wait(self._settle_time)

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

        # to skycoords
        ra_dec = SkyCoord(ra * u.deg, dec * u.deg, frame=ICRS)

        # start slewing
        self.put('Tracking', Tracking=True)
        self.put('SlewToCoordinatesAsync', RightAscension=ra / 15., Declination=dec)

        # wait for it
        while self.get('Slewing'):
            abort_event.wait(1)
        self.put('Tracking', Tracking=True)

        # wait settle time
        abort_event.wait(self._settle_time)

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
            # start slewing
            self._change_motion_status(IMotion.Status.SLEWING)
            log.info('Setting telescope offsets to dRA=%.2f", dDec=%.2f"...', dra * 3600., ddec * 3600.)

            # get current coordinates (with old offsets)
            ra, dec = self.get_radec()

            # store offsets
            self._offset_ra = dra
            self._offset_dec = ddec

            # add offset
            ra += float(self._offset_ra / np.cos(np.radians(dec)))
            dec += float(self._offset_dec)

            # start slewing
            self.put('Tracking', Tracking=True)
            self.put('SlewToCoordinatesAsync', Azimuth=ra / 15., Altitude=dec)

            # wait for it
            while self.get('Slewing'):
                self._abort_move.wait(1)
            self.put('Tracking', Tracking=True)

            # wait settle time
            self._abort_move.wait(self._settle_time)

            # finish slewing
            self._change_motion_status(IMotion.Status.TRACKING)
            log.info('Reached destination.')

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

        # get position
        ra, dec = self.get('RightAscension'), self.get('Declination')

        # correct ra offset by decl
        ra_off = self._offset_ra / np.cos(np.radians(dec))

        # return coordinates without offsets
        return float(ra * 15 - ra_off), float(dec - self._offset_dec)

    def get_altaz(self, *args, **kwargs) -> (float, float):
        """Returns current Alt and Az.

        Returns:
            Tuple of current Alt and Az in degrees.
        """

        # correct azimuth by 180 degrees
        az = self.get('Azimuth') + 180
        if az > 360:
            az -= 360

        # create sky coordinates
        return self.get('Altitude'), self.get('Azimuth')

    def stop_motion(self, device: str = None, *args, **kwargs):
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        # stop telescope
        self.put('AbortSlew')
        self.put('Tracking', Tracking=False)

    def is_ready(self, *args, **kwargs) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """
        return True

    def sync_target(self, *args, **kwargs):
        """Synchronize telescope on current target using current offsets."""

        # get current RA/Dec without offsets
        ra, dec = self.get_radec()

        # sync
        self.put('SyncToCoordinates', RightAscension=ra / 15., Declination=dec)

    def get_fits_headers(self, namespaces: list = None, *args, **kwargs) -> dict:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """

        # get headers from base
        hdr = BaseTelescope.get_fits_headers(self)

        # get offsets
        ra_off, dec_off = self.get_radec_offsets()

        # define values to request
        hdr['RAOFF'] = (ra_off, 'RA offset [deg]')
        hdr['DECOFF'] = (dec_off, 'Dec offset [deg]')

        # return it
        return self._filter_fits_namespace(hdr, namespaces, **kwargs)


__all__ = ['AlpacaTelescope']
