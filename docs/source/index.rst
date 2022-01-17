pyobs-alpaca
############

This is a `pyobs <https://www.pyobs.org>`_ (`documentation <https://docs.pyobs.org>`_) module for ALPACA, which is
a HTTP proxy for ASCOM.


Example configuration
*********************


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