# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries for releases before this file existed were generated from commit subjects.

## [2.0.1] - 2026-09-03

- Add DOMESHUT FITS header; adopt FocuserHeaderMixin for FOCOFF (#872)

## [2.0.0] - 2026-08-26

- Require stable pyobs-core>=2.0.0
- Gate auto-merge on the PR author, not the event actor
- Enable Dependabot auto-merge for patch/minor updates
- Lock DEVICE_INIT_KWARGS/OBJECT_SHARED_KWARGS against real constructor signatures
- Convert AlpacaDome/AlpacaFocuser/AlpacaTelescope to cooperative super().__init__() chains
- Add baseline test suite and CI (pytest, pyrefly), grouped Dependabot
- Upgrade uv.lock to clear open Dependabot alerts
- Require pyobs-core>=2.0.0.dev48
- Add dependabot.yml, targeting develop for PRs
- Add TODO for register_exception rename coming in pyobs-core
- Update to pyobs-core 2.0.0.dev10, apply FitsHeaderEntry to get_fits_header_before
- Add missing docs/requirements.txt to readthedocs config
- Write proper README with install and configuration docs
- refactored Alpaca device class and modules to standardize logging, type hints, and state publishing
- added Ruff GitHub Action
- migrated to Ruff and updated `uv.lock`
- changing old-style type hints to new style
- changed underscore parameters for vfs, comm, etc
- renamed Object parameters (comm, observer, ...) to start with an underscore
- new lock file

## [1.1.2] - 2025-07-07

- .
- readthedocs

## [1.1.1] - 2025-07-03

- github action
- pre-commit

## [1.1.0] - 2025-07-03

- migrated to uv

## [1.0.3] - 2023-07-26

- updated deps

## [1.0.2] - 2023-07-26

- up to Python 3.11

## [1.0.1] - 2022-10-16

- resend open/close command every 10s

## [1.0.0] - 2022-09-13

- added license

## [0.18.0] - 2022-03-18

- changed valid states

## [0.16.6] - 2022-03-03

- check for weather in init()

## [0.16.5] - 2022-03-03

- pyobs version

## [0.16.4] - 2022-03-03

- changed handling of parking and setting offsets, closes #111, closes #101
- removed requests from dependencies

## [0.16.3] - 2022-02-12

- fixed bug
- example config
- added file

## [0.16.2] - 2022-01-18

- fixed all dependencies

## [0.16.1] - 2022-01-18

- fixed docstrings
- added basic docs

## [0.16.0] - 2022-01-14

- added exception handling
- set InterruptedError instead of AbortedError
- replaced AbortedError with builtin InterruptedError
- fixed exceptions
- reacting to abort event

## [0.15.1] - 2022-01-06

- use actual timeout
- added black and pre-commit to dev dependencies
- added .pre-commit-config.yaml
- running black
- added pyproject.toml

## [0.15.0] - 2021-12-29

- changed used Python version to 3.9
- replaced requests with aiohttp, #66
- removed self.closing in all Modules
- fixed bug
- type -> device_type and cleanup
- added some awaits
- new alpha
- added option for get_object & co to create object from type directly
- with -> async with
- alpha1
- asyncio
- github action
- changed to Poetry
- send OffsetsEvents when moving offset
- v0.14
- renamed IFitsHeaderProvider to IFitsHeaderBefore and get_fits_headers() to get_fits_header_before()
- Added type hints
- fixed IPointingAltAz
- renamed set/get_*_offsets to set/get_offsets-*
- renamed some interfaces (IPointing* and IOffsets*)
- only move when ready
- follow on parking
- don't move if in some state
- ignore move commands in certain states
- v0.13
- do nothing in park() if already parking
- allow move always
- allow dome to follow even if closed
- documentation
- added __module__
- made add_thread_func and add_child_func public
- updated docstrings
- fixed docstrings
- moved IMotion.Status to utils.enums.MotionStatus
- removed Module import from global __init__
- fixed types to make mypy happy
- fixed type hints
- set higher timeout for Park command
- correct az by 360
- catch all exceptions on get/put
- added handling ReadTimeout exception
- errors to warnings
- error handling
- customize parameter requested for alive ping
- request Connected instead of DriverVersion in connection check
- run Object.open() in open()
- logging on (dis)connect
- changed exception handling
- removed imports
- check connected on put
- status handling
- AlpacaDevice is not parent class any more but attribute
- rotate dome to az=0 on park
- wait for shutter
- no correction for az
- some error handling
- added timeout
- correct init position
- correct az by 180
- added dome
- don't correct az by 180
- initial commit
