import asyncio
import logging
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

from pyobs.events import OffsetsRaDecEvent
from pyobs.mixins import FitsNamespaceMixin
from pyobs.interfaces import IFitsHeaderBefore, IOffsetsRaDec, ISyncTarget
from pyobs.modules import timeout
from pyobs.modules.telescope.basetelescope import BaseTelescope
from pyobs.utils.enums import MotionStatus
from pyobs.utils.parallel import event_wait
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
        self._device = self.add_child_object(AlpacaDevice, **kwargs)

        # variables
        self._settle_time = settle_time

        # offsets in ra/dec
        self._offset_ra = 0.
        self._offset_dec = 0.

        # mixins
        FitsNamespaceMixin.__init__(self, **kwargs)

    async def open(self) -> None:
        """Open module.

        Raises:
            ValueError: If cannot connect to device.
        """
        await BaseTelescope.open(self)

        # initial status
        status = await self._get_status()
        if status == MotionStatus.UNKNOWN:
            log.error('Could not fetch initial status from telescope.')
        await self._change_motion_status(status)

    async def _get_status(self) -> MotionStatus:
        """Get status of telescope."""

        try:
            if await self._device.get('AtPark'):
                return MotionStatus.PARKED
            elif await self._device.get('Slewing'):
                return MotionStatus.SLEWING
            elif await self._device.get('Tracking'):
                return MotionStatus.TRACKING
            else:
                return MotionStatus.IDLE

        except ValueError:
            return MotionStatus.UNKNOWN

    async def _check_status_thread(self) -> None:
        """Periodically check status of telescope."""

        while not self.closing.is_set():
            # only check, if status is unknown
            if await self.get_motion_status() == MotionStatus.UNKNOWN:
                await self._change_motion_status(await self._get_status())

            # wait a little
            await asyncio.sleep(5)

    @timeout(60000)
    async def init(self, **kwargs: Any) -> None:
        """Initialize telescope.

        Raises:
            ValueError: If telescope could not be initialized.
        """

        # if already initializing, ignore
        if await self.get_motion_status() == MotionStatus.INITIALIZING:
            return

        # acquire lock
        async with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # change status
            log.info('Initializing telescope...')
            await self._change_motion_status(MotionStatus.INITIALIZING)

            # move to init position
            try:
                await self._move_altaz(30, 180., self._abort_move)
                await self._change_motion_status(MotionStatus.IDLE)
                log.info('Telescope initialized.')

            except ValueError:
                await self._change_motion_status(MotionStatus.UNKNOWN)
                raise ValueError('Could not init telescope.')

    @timeout(60000)
    async def park(self, **kwargs: Any) -> None:
        """Park telescope.

        Raises:
            ValueError: If telescope could not be parked.
        """

        # if already parking, ignore
        if await self.get_motion_status() == MotionStatus.PARKING:
            return

        # acquire lock
        async with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # change status
            log.info('Parking telescope...')
            await self._change_motion_status(MotionStatus.PARKING)

            # park telescope
            try:
                await self._device.put('Park', timeout=60)
                await self._change_motion_status(MotionStatus.PARKED)
                log.info('Telescope parked.')

            except ValueError:
                await self._change_motion_status(MotionStatus.UNKNOWN)
                raise ValueError('Could not park telescope.')

    async def _move_altaz(self, alt: float, az: float, abort_event: asyncio.Event) -> None:
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
            await self._device.put('Tracking', Tracking=False)
            await self._device.put('SlewToAltAzAsync', Azimuth=az, Altitude=alt)

            # wait for it
            while await self._device.get('Slewing'):
                await event_wait(abort_event, 1)
            await self._device.put('Tracking', Tracking=False)

            # wait settle time
            await event_wait(abort_event, self._settle_time)

        except ValueError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise ValueError('Could not move telescope to Alt/Az.')

    async def _move_radec(self, ra: float, dec: float, abort_event: asyncio.Event) -> None:
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
            await self._device.put('Tracking', Tracking=True)
            await self._device.put('SlewToCoordinatesAsync', RightAscension=ra / 15., Declination=dec)

            # wait for it
            while await self._device.get('Slewing'):
                await event_wait(abort_event, 1)
            await self._device.put('Tracking', Tracking=True)

            # wait settle time
            await event_wait(abort_event, self._settle_time)

        except ValueError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise ValueError('Could not move telescope to RA/Dec.')

    @timeout(10000)
    async def set_offsets_radec(self, dra: float, ddec: float, **kwargs: Any) -> None:
        """Move an RA/Dec offset.

        Args:
            dra: RA offset in degrees.
            ddec: Dec offset in degrees.

        Raises:
            ValueError: If offset could not be set.
        """

        # acquire lock
        async with LockWithAbort(self._lock_moving, self._abort_move):
            # not connected
            if not self._device.connected:
                raise ValueError('Not connected to ASCOM.')

            # start slewing
            await self._change_motion_status(MotionStatus.SLEWING)
            log.info('Setting telescope offsets to dRA=%.2f", dDec=%.2f"...', dra * 3600., ddec * 3600.)
            await self.comm.send_event(OffsetsRaDecEvent(ra=dra, dec=ddec))

            # get current coordinates (with old offsets)
            ra, dec = await self.get_radec()

            # store offsets
            self._offset_ra = dra
            self._offset_dec = ddec

            # add offset
            ra += float(self._offset_ra / np.cos(np.radians(dec)))
            dec += float(self._offset_dec)

            try:
                # start slewing
                await self._device.put('Tracking', Tracking=True)
                await self._device.put('SlewToCoordinatesAsync', RightAscension=ra / 15., Declination=dec)

                # wait for it
                while await self._device.get('Slewing'):
                    await event_wait(self._abort_move, 1)
                await self._device.put('Tracking', Tracking=True)

                # wait settle time
                await event_wait(self._abort_move, self._settle_time)

                # finish slewing
                await self._change_motion_status(MotionStatus.TRACKING)
                log.info('Reached destination.')

            except ValueError:
                await self._change_motion_status(MotionStatus.UNKNOWN)
                raise ValueError('Could not move telescope to RA/Dec offset.')

    async def get_offsets_radec(self, **kwargs: Any) -> Tuple[float, float]:
        """Get RA/Dec offset.

        Returns:
            Tuple with RA and Dec offsets.
        """
        return self._offset_ra, self._offset_dec

    async def get_radec(self, **kwargs: Any) -> Tuple[float, float]:
        """Returns current RA and Dec.

        Returns:
            Tuple of current RA and Dec in degrees.
        """

        try:
            # get position
            ra, dec = await self._device.get('RightAscension'), await self._device.get('Declination')

            # correct ra offset by decl
            ra_off = self._offset_ra / np.cos(np.radians(dec))

            # return coordinates without offsets
            return float(ra * 15 - ra_off), float(dec - self._offset_dec)

        except ValueError:
            raise ValueError('Could not fetch Alt/Az.')

    async def get_altaz(self, **kwargs: Any) -> Tuple[float, float]:
        """Returns current Alt and Az.

        Returns:
            Tuple of current Alt and Az in degrees.
        """

        try:
            # correct az
            az = await self._device.get('Azimuth') + 180
            if az > 360:
                az -= 360

            # create sky coordinates
            return await self._device.get('Altitude'), az

        except ValueError:
            raise ValueError('Could not fetch Alt/Az.')

    async def stop_motion(self, device: Optional[str] = None, **kwargs: Any) -> None:
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        try:
            # stop telescope
            self._abort_move.set()
            await self._device.put('AbortSlew')
            await self._device.put('Tracking', Tracking=False)
            await self._change_motion_status(MotionStatus.IDLE)

        except ValueError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise ValueError('Could not stop telescope.')

    async def is_ready(self, **kwargs: Any) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            Whether device is ready
        """

        # check that motion is not in one of the states listed below
        states = [MotionStatus.PARKED, MotionStatus.INITIALIZING,
                  MotionStatus.PARKING, MotionStatus.ERROR, MotionStatus.UNKNOWN]
        return self._device.connected and await self.get_motion_status() not in states

    async def sync_target(self, **kwargs: Any) -> None:
        """Synchronize telescope on current target using current offsets."""

        # get current RA/Dec without offsets
        ra, dec = await self.get_radec()

        # sync
        await self._device.put('SyncToCoordinates', RightAscension=ra / 15., Declination=dec)

    async def get_fits_header_before(self, namespaces: Optional[List[str]] = None, **kwargs: Any) \
            -> Dict[str, Tuple[Any, str]]:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """

        # get headers from base
        hdr = await BaseTelescope.get_fits_header_before(self)

        try:
            # get offsets
            ra_off, dec_off = await self.get_offsets_radec()

            # define values to request
            hdr['RAOFF'] = (ra_off, 'RA offset [deg]')
            hdr['DECOFF'] = (dec_off, 'Dec offset [deg]')

            # return it
            return self._filter_fits_namespace(hdr, namespaces=namespaces, **kwargs)

        except ValueError:
            return {}


__all__ = ['AlpacaTelescope']
