import logging
from typing import Any, NamedTuple
import requests
from requests import ConnectTimeout, ReadTimeout
from urllib3.exceptions import ConnectTimeoutError

from pyobs.object import Object

log = logging.getLogger('pyobs')


class ServerPutResponse(NamedTuple):
    ClientTransactionID: int
    ErrorMessage: str
    ErrorNumber: int
    ServerTransactionID: int


class ServerGetResponse(NamedTuple):
    ClientTransactionID: int
    ErrorMessage: str
    ErrorNumber: int
    ServerTransactionID: int
    Value: Any


class AlpacaDevice(Object):
    def __init__(self, server: str, port: int, type: str, device: int, version: str = 'v1',
                 alive_parameter: str = 'Connected', **kwargs: Any):
        """Initializes a new ASCOM Alpaca device.

        Args:
            server: Name or IP of Alpaca remote server.
            port: Port of Alpaca remote server
            type: Type of device.
            device: Device number.
            version: Alpaca version.
            alive_parameter: Name of parameter to request in alive ping.
        """
        Object.__init__(self, **kwargs)

        # variables
        self._server = server
        self._port = port
        self._type = type
        self._device = device
        self._version = version
        self._alive_param = alive_parameter

        # do we have a connection to the ASCOM Remote server?
        self._connected = False

        # add thread
        self.add_thread_func(self._check_connected_thread)

        # check version
        if version != 'v1':
            raise ValueError('Only Alpaca v1 is supported.')

        # create session
        self._session = requests.session()

    @property
    def connected(self) -> bool:
        return self._connected

    def open(self) -> None:
        """Open device."""
        Object.open(self)

        # check connected
        self._check_connected()
        if not self._connected:
            log.warning('Could not connect to ASCOM server.')

    def _check_connected_thread(self) -> None:
        """Periodically check, whether we're connected to ASCOM."""
        while not self.closing.is_set():
            self._check_connected()
            self.closing.wait(5)

    def _check_connected(self) -> None:
        """Check, whether we're connected to ASCOM"""

        # get new status
        try:
            self._get(self._alive_param)
            connected = True
        except ValueError:
            connected = False

        # did it change?
        if connected != self._connected:
            if connected:
                log.info('Connected to ASCOM server.')
            else:
                log.warning('Lost connection to ASCOM server.')

        # store new status
        self._connected = connected

    def _build_alpaca_url(self, name: str) -> str:
        """Build URL for Alpaca server.

        Args:
            name: Name of Alpaca variable

        Returns:
            Full Alpaca URL
        """
        return 'http://%s:%d/api/%s/%s/%d/%s' % (self._server, self._port, self._version,
                                                 self._type, self._device, name.lower())

    def _get(self, name: str) -> Any:
        """Calls GET on Alpaca server, which returns value for variable with given name.

        Args:
            name: Name of variable.

        Returns:
            Value of variable.
        """

        # get url
        url = self._build_alpaca_url(name)

        try:
            # request it
            res = self._session.get(url, timeout=5)
            if res.status_code != 200:
                raise ValueError('Could not contact server.')
            response = ServerGetResponse(**res.json())

        except Exception as e:
            raise ValueError('Could not connect to server: ' + str(e))

        # check error
        if response.ErrorNumber != 0:
            raise ValueError('Server error: %s' % response.ErrorMessage)

        # return value
        return response.Value

    def get(self, name: str) -> Any:
        """Calls GET on Alpaca server, which returns value for variable with given name.

        Args:
            name: Name of variable.

        Returns:
            Value of variable.
        """

        # only do it, if connected
        if not self._connected:
            raise ValueError('Not connected to ASCOM.')
        return self._get(name)

    def put(self, name: str, timeout: float = 5, **values: Any) -> None:
        """Calls PUT on Alpaca server with given variable, which might set a variable or call a method.

        Args:
            name: Name of variable.
            timeout: Time in sec for request.
            values: Values to set.
        """

        # only do it, if connected
        if not self._connected:
            raise ValueError('Not connected to ASCOM.')

        # get url
        url = self._build_alpaca_url(name)

        try:
            # request it
            res = self._session.put(url, data=values, timeout=timeout)
            if res.status_code != 200:
                raise ValueError('Could not contact server.')
            response = ServerPutResponse(**res.json())

        except Exception as e:
            raise ValueError('Could not connect to server: ' + str(e))

        # check error
        if response.ErrorNumber != 0:
            raise ValueError('Server error: %s' % response.ErrorMessage)


__all__ = ['AlpacaDevice']
