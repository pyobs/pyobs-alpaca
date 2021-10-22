import logging
import threading
import time
from typing import List, Dict, Tuple, Any, Optional

from pyobs.modules import Module
from pyobs.interfaces import IFocuser, IFitsHeaderProvider
from pyobs.mixins import MotionStatusMixin
from pyobs.modules import timeout
from pyobs.utils.enums import MotionStatus
from pyobs.utils.threads import LockWithAbort
from .device import AlpacaDevice

log = logging.getLogger(__name__)


class AlpacaFocuser(MotionStatusMixin, IFocuser, IFitsHeaderProvider, Module):
    __module__ = 'pyobs_alpaca'

    def __init__(self, **kwargs: Any):
        Module.__init__(self, **kwargs)

        # device
        self._device = AlpacaDevice(**kwargs)
        self.add_child_object(self._device)
        
        # variables
        self._focus_offset = 0.

        # allow to abort motion
        self._lock_motion = threading.Lock()
        self._abort_motion = threading.Event()

        # init mixins
        MotionStatusMixin.__init__(self, motion_status_interfaces=['IFocuser'])

    def open(self) -> None:
        """Open module."""
        Module.open(self)

        # open mixins
        MotionStatusMixin.open(self)

        # init status
        self._change_motion_status(MotionStatus.IDLE, interface='IFocuser')

    def init(self, **kwargs: Any) -> None:
        """Initialize device.

        Raises:
            ValueError: If device could not be initialized.
        """
        pass

    def park(self, **kwargs: Any) -> None:
        """Park device.

        Raises:
            ValueError: If device could not be parked.
        """
        pass

    def get_fits_headers(self, namespaces: Optional[List[str]] = None, **kwargs: Any) -> Dict[str, Tuple[Any, str]]:
        """Returns FITS header for the current status of this module.

        Args:
            namespaces: If given, only return FITS headers for the given namespaces.

        Returns:
            Dictionary containing FITS headers.
        """

        # get pos and step size
        # StepSize is in microns, so multiply with 1000
        try:
            pos = self._device.get('Position')
            step = self._device.get('StepSize') * 1000.

            # return header
            return {
                'TEL-FOCU': (pos / step, 'Focus of telescope [mm]')
            }

        except ValueError as e:
            log.warning('Could not determine focus position: %s', e)
            return {}

    @timeout(60000)
    def set_focus(self, focus: float, **kwargs: Any) -> None:
        """Sets new focus.

        Args:
            focus: New focus value.
        """

        # set focus + offset
        self._set_focus(focus + self._focus_offset)

    def set_focus_offset(self, offset: float, **kwargs: Any) -> None:
        """Sets focus offset.

        Args:
            offset: New focus offset.

        Raises:
            InterruptedError: If focus was interrupted.
        """

        # get current focus (without offset)
        focus = self.get_focus()

        # set offset
        self._focus_offset = offset

        # go to focus
        self._set_focus(focus + self._focus_offset)

    def _set_focus(self, focus: float) -> None:
        """Actually sets new focus.

        Args:
            focus: New focus value.
        """

        # acquire lock
        with LockWithAbort(self._lock_motion, self._abort_motion):
            # get step size
            step = self._device.get('StepSize')

            # calculating new focus and move it
            log.info('Moving focus to %.2fmm...', focus)
            self._change_motion_status(MotionStatus.SLEWING, interface='IFocuser')
            foc = int(focus * step * 1000.)
            self._device.put('Move', Position=foc)

            # wait for it
            while abs(self._device.get('Position') - foc) > 10:
                # abort?
                if self._abort_motion.is_set():
                    log.warning('Setting focus aborted.')
                    return

                # sleep a little
                time.sleep(0.1)

            # finished
            log.info('Reached new focus of %.2fmm.', self._device.get('Position') / step / 1000.)
            self._change_motion_status(MotionStatus.POSITIONED, interface='IFocuser')

    def get_focus(self, **kwargs: Any) -> float:
        """Return current focus.

        Returns:
            Current focus.
        """

        # get pos and step size
        # StepSize is in microns, so multiply with 1000
        pos = float(self._device.get('Position'))
        step = float(self._device.get('StepSize')) * 1000.

        # return current focus - offset
        return pos / step - self._focus_offset

    def get_focus_offset(self, **kwargs: Any) -> float:
        """Return current focus offset.

        Returns:
            Current focus offset.
        """
        return self._focus_offset

    def stop_motion(self, device: Optional[str] = None, **kwargs: Any) -> None:
        """Stop the motion.

        Args:
            device: Name of device to stop, or None for all.
        """

        # stop motion
        self._device.put('Halt')

    def is_ready(self, **kwargs: Any) -> bool:
        """Returns the device is "ready", whatever that means for the specific device.

        Returns:
            True, if telescope is initialized and not in an error state.
        """

        # check that motion is not in one of the states listed below
        return self._device.connected and \
               self.get_motion_status() not in [MotionStatus.PARKED, MotionStatus.INITIALIZING,
                                                MotionStatus.PARKING, MotionStatus.ERROR, MotionStatus.UNKNOWN]


__all__ = ['AlpacaFocuser']
