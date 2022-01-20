pyobs-alpaca
############

This is a `pyobs <https://www.pyobs.org>`_ (`documentation <https://docs.pyobs.org>`_) module for ALPACA, which is
a HTTP proxy for ASCOM.


Example configuration
*********************


This is an example configuration for a telescope::

    class: pyobs_alpaca.AlpacaTelescope
    name: ASCOM Telescope
    server: 1.2.3.4
    port: 11111
    device_type: telescope
    device: 0
    wait_for_dome: dome
    weather: weather
    alive_parameter: Name

    # communication
    comm:
      jid: test@example.com
      password: ***

And for a focussing unit (without the ``comm`` block)::

    class: pyobs_alpaca.AlpacaFocuser
    name: ASCOM Focuser
    server: 1.2.3.4
    port: 11111
    device_type: focuser
    device: 0
    alive_parameter: Position

And finally, for a dome::

    class: pyobs_alpaca.AlpacaDome
    server: 1.2.3.4
    port: 11111
    device_type: dome
    device: 0
    follow: telescope
    weather: weather


Available classes
*****************

These classes are meant more as a means of an example for own implementations. :ref:`AlpacaTelescope` is an
implementation for telescopes, while :ref:`AlpacaFocuser` works for focusing devices and :ref:`AlpacaDome` can
operate a dome.

AlpacaTelescope
===============
.. autoclass:: pyobs_alpaca.AlpacaTelescope
   :members:
   :show-inheritance:

AlpacaFocuser
=============
.. autoclass:: pyobs_alpaca.AlpacaFocuser
   :members:
   :show-inheritance:

AlpacaDome
==========
.. autoclass:: pyobs_alpaca.AlpacaDome
   :members:
   :show-inheritance: