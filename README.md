ASCOM Alpaca wrapper for *pyobs*
================================

This is a [pyobs](https://www.pyobs.org) module for wrapping devices accessible via
[ASCOM Alpaca](https://ascom-standards.org/AlpacaDeveloper/), a HTTP proxy for ASCOM. It provides
telescope, focuser and dome implementations.


Install *pyobs-alpaca*
-----------------------
Clone the repository:

    git clone https://github.com/pyobs/pyobs-alpaca.git
    cd pyobs-alpaca

Install it with [uv](https://docs.astral.sh/uv/):

    uv sync

Alternatively, with plain `venv`/`pip`:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install .


Configuration
-------------
This is an example configuration for a telescope:

    class: pyobs_alpaca.AlpacaTelescope
    name: ASCOM Telescope
    server: 1.2.3.4
    port: 11111
    device_type: telescope
    device: 0
    wait_for_dome: dome
    weather: weather
    alive_parameter: Name

And for a focusing unit:

    class: pyobs_alpaca.AlpacaFocuser
    name: ASCOM Focuser
    server: 1.2.3.4
    port: 11111
    device_type: focuser
    device: 0
    alive_parameter: Position

And finally, for a dome:

    class: pyobs_alpaca.AlpacaDome
    server: 1.2.3.4
    port: 11111
    device_type: dome
    device: 0
    follow: telescope
    weather: weather


Dependencies
------------
* [pyobs-core](https://github.com/pyobs/pyobs-core) for the core functionality.
* [NumPy](https://numpy.org/) for array handling.
