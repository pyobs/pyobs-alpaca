import asyncio
import logging
from typing import Any

import numpy as np
from pyobs.events import OffsetsRaDecEvent
from pyobs.interfaces import (
    AltAzState,
    FitsHeaderEntry,
    IFitsHeaderBefore,
    IOffsetsRaDec,
    IPointingAltAz,
    IPointingRaDec,
    IReady,
    ISyncTarget,
    RaDecOffsetState,
    RaDecState,
    ReadyState,
)
from pyobs.mixins import FitsNamespaceMixin
from pyobs.modules import timeout
from pyobs.modules.telescope.basetelescope import BaseTelescope
from pyobs.utils import exceptions as exc
from pyobs.utils.enums import MotionStatus
from pyobs.utils.parallel import acquire_lock, event_wait
from pyobs.utils.threads import LockWithAbort

from .device import AlpacaDevice

log = logging.getLogger("pyobs")


class AlpacaTelescope(BaseTelescope, FitsNamespaceMixin, IFitsHeaderBefore, IOffsetsRaDec, ISyncTarget):
    __module__ = "pyobs_alpaca"

    def __init__(self, settle_time: float = 3.0, park_position: tuple[float, float] = (180.0, 15.0), **kwargs: Any):
        """Initializes a new ASCOM Alpaca telescope.

        Args:
            settle_time: Time in seconds to wait after slew before finishing.
            park_position: Alt/Az park position.
        """
        BaseTelescope.__init__(self, **kwargs, motion_status_interfaces=["ITelescope"])

        # device
        self._device = self.add_child_object(AlpacaDevice, **kwargs)

        # variables
        self._settle_time = settle_time
        self._park_position = park_position

        # offsets in ra/dec
        self._offset_ra = 0.0
        self._offset_dec = 0.0

        # cached position for sync property
        self._cached_ra: float | None = None
        self._cached_dec: float | None = None

        # mixins
        FitsNamespaceMixin.__init__(self, **kwargs)

        # background position polling
        self.add_background_task(self._update_position)

    @property
    def _position_radec(self) -> tuple[float, float] | None:
        if self._cached_ra is None or self._cached_dec is None:
            return None
        return self._cached_ra, self._cached_dec

    async def open(self) -> None:
        """Open module."""
        await BaseTelescope.open(self)

        status = await self._get_status()
        if status == MotionStatus.UNKNOWN:
            log.error("Could not fetch initial status from telescope.")
        await self._change_motion_status(status)

    async def _get_status(self) -> MotionStatus:
        """Get status of telescope."""
        try:
            if await self._device.get("AtPark"):
                return MotionStatus.PARKED
            elif await self._device.get("Slewing"):
                return MotionStatus.SLEWING
            elif await self._device.get("Tracking"):
                return MotionStatus.TRACKING
            else:
                return MotionStatus.IDLE
        except ConnectionError:
            return MotionStatus.UNKNOWN

    async def _update_position(self) -> None:
        """Periodically poll position from telescope and publish state."""
        while True:
            try:
                ra_raw = await self._device.get("RightAscension")
                dec = await self._device.get("Declination")
                ra_off = self._offset_ra / np.cos(np.radians(dec))
                ra = float(ra_raw * 15 - ra_off)
                dec = float(dec - self._offset_dec)

                self._cached_ra = ra
                self._cached_dec = dec
                await self.comm.set_state(IPointingRaDec, RaDecState(ra=ra, dec=dec))

                az_raw = await self._device.get("Azimuth") + 180
                if az_raw > 360:
                    az_raw -= 360
                alt = await self._device.get("Altitude")
                await self.comm.set_state(IPointingAltAz, AltAzState(alt=alt, az=az_raw))
            except ConnectionError:
                self._cached_ra = None
                self._cached_dec = None

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

    @timeout(60000)
    async def init(self, **kwargs: Any) -> None:
        """Initialize telescope."""

        if not self.is_weather_good():
            raise exc.InitError("Weather seems to be bad.")

        if self.motion_status() in [MotionStatus.INITIALIZING, MotionStatus.ERROR]:
            return

        async with LockWithAbort(self._lock_moving, self._abort_move):
            if not self._device.connected:
                raise exc.InitError("Not connected to ASCOM.")

            log.info("Initializing telescope...")
            await self._change_motion_status(MotionStatus.INITIALIZING)

            try:
                await self._move_altaz(30, 180.0, self._abort_move)
                await self._change_motion_status(MotionStatus.IDLE)
                log.info("Telescope initialized.")
            except ConnectionError:
                await self._change_motion_status(MotionStatus.UNKNOWN)
                raise exc.InitError("Could not init telescope.")
            except InterruptedError:
                await self._change_motion_status(MotionStatus.UNKNOWN)

    @timeout(60000)
    async def park(self, **kwargs: Any) -> None:
        """Park telescope."""

        if self.motion_status() in [MotionStatus.PARKING, MotionStatus.ERROR]:
            return

        async with LockWithAbort(self._lock_moving, self._abort_move):
            if not self._device.connected:
                raise exc.ParkError("Not connected to ASCOM.")

            log.info("Parking telescope...")
            await self._change_motion_status(MotionStatus.PARKING)

            try:
                await self._move_altaz(self._park_position[1], self._park_position[0], self._abort_move)
                await self._device.put("Park", timeout=60)
                await self._change_motion_status(MotionStatus.PARKED)
                log.info("Telescope parked.")
            except ConnectionError:
                await self._change_motion_status(MotionStatus.UNKNOWN)
                raise exc.ParkError("Could not park telescope.")
            except InterruptedError:
                await self._change_motion_status(MotionStatus.UNKNOWN)

    async def _move_altaz(self, alt: float, az: float, abort_event: asyncio.Event) -> None:
        """Move to Alt/Az coordinates."""

        self._offset_ra, self._offset_dec = 0, 0

        try:
            await self._device.put("Tracking", Tracking=False)
            await self._device.put("SlewToAltAzAsync", Azimuth=az, Altitude=alt)

            while await self._device.get("Slewing"):
                if await event_wait(abort_event, 1):
                    raise InterruptedError("Alt/Az movement aborted.")

            await self._device.put("Tracking", Tracking=False)
            await event_wait(abort_event, self._settle_time)

        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            await self.stop_motion()
            raise exc.MoveError("Could not move telescope to Alt/Az.")

    async def _move_radec(self, ra: float, dec: float, abort_event: asyncio.Event) -> None:
        """Start tracking on RA/Dec coordinates."""

        self._offset_ra, self._offset_dec = 0, 0

        try:
            await self._device.put("Tracking", Tracking=True)
            await self._device.put("SlewToCoordinatesAsync", RightAscension=ra / 15.0, Declination=dec)

            while await self._device.get("Slewing"):
                if await event_wait(abort_event, 1):
                    raise InterruptedError("RA/Dec movement aborted.")
            await self._device.put("Tracking", Tracking=True)
            await event_wait(abort_event, self._settle_time)

        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            await self.stop_motion()
            raise exc.MoveError("Could not move telescope to RA/Dec.")

    @timeout(10000)
    async def set_offsets_radec(self, dra: float, ddec: float, **kwargs: Any) -> None:
        """Move an RA/Dec offset."""

        if self.motion_status() != MotionStatus.TRACKING:
            log.warning("Can only set offset when tracking.")
            return

        if not await acquire_lock(self._lock_moving, 5):
            log.warning("Could not acquire lock for setting offset.")
            return

        try:
            if not self._device.connected:
                raise exc.MoveError("Not connected to ASCOM.")

            await self._change_motion_status(MotionStatus.SLEWING)
            log.info('Setting telescope offsets to dRA=%.2f", dDec=%.2f"...', dra * 3600.0, ddec * 3600.0)
            await self.comm.send_event(OffsetsRaDecEvent(ra=dra, dec=ddec))

            # get current coordinates from device (without old offsets)
            ra_raw = await self._device.get("RightAscension")
            dec_raw = await self._device.get("Declination")
            old_ra_off = self._offset_ra / np.cos(np.radians(dec_raw))
            ra = float(ra_raw * 15 - old_ra_off)
            dec = float(dec_raw - self._offset_dec)

            # store new offsets
            self._offset_ra = dra
            self._offset_dec = ddec

            # apply new offsets
            ra += float(self._offset_ra / np.cos(np.radians(dec)))
            dec += float(self._offset_dec)

            await self._device.put("Tracking", Tracking=True)
            await self._device.put("SlewToCoordinatesAsync", RightAscension=ra / 15.0, Declination=dec)

            while await self._device.get("Slewing"):
                if await event_wait(self._abort_move, 1):
                    log.info("RA/Dec offset movement aborted.")
                    return

            await self._device.put("Tracking", Tracking=True)
            await event_wait(self._abort_move, self._settle_time)

            await self._change_motion_status(MotionStatus.TRACKING)
            await self.comm.set_state(IOffsetsRaDec, RaDecOffsetState(ra=dra, dec=ddec))
            log.info("Reached destination.")

        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise exc.MoveError("Could not move telescope to RA/Dec offset.")

        finally:
            self._lock_moving.release()

    async def stop_motion(self, device: str | None = None, **kwargs: Any) -> None:
        """Stop the motion."""
        try:
            self._abort_move.set()
            await self._device.put("AbortSlew")
            await self._device.put("Tracking", Tracking=False)
            await self._change_motion_status(MotionStatus.IDLE)
        except ConnectionError:
            await self._change_motion_status(MotionStatus.UNKNOWN)
            raise exc.MoveError("Could not stop telescope.")

    async def sync_target(self, **kwargs: Any) -> None:
        """Synchronize telescope on current target using current offsets."""
        if self._cached_ra is None or self._cached_dec is None:
            raise exc.MoveError("No position available for sync.")
        await self._device.put("SyncToCoordinates", RightAscension=self._cached_ra / 15.0, Declination=self._cached_dec)

    async def get_fits_header_before(
        self, namespaces: list[str] | None = None, **kwargs: Any
    ) -> dict[str, FitsHeaderEntry]:
        """Returns FITS header for the current status of this module."""

        hdr = await BaseTelescope.get_fits_header_before(self)

        try:
            hdr["RAOFF"] = FitsHeaderEntry(self._offset_ra, "RA offset [deg]")
            hdr["DECOFF"] = FitsHeaderEntry(self._offset_dec, "Dec offset [deg]")
            return self._filter_fits_namespace(hdr, namespaces=namespaces, **kwargs)
        except ConnectionError:
            return {}


__all__ = ["AlpacaTelescope"]
