import logging
from typing import Any

import pytest
from pyobs.utils import exceptions as exc
from pyobs.utils.enums import MotionStatus

from pyobs_alpaca.focuser import AlpacaFocuser

_FOCUSER_ARGS = dict(server="localhost", port=11111, device_type="focuser", device=0)


def test_focuser_default_move_timeout() -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS)
    assert focuser._move_timeout == 60.0


def test_focuser_custom_move_timeout() -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=30.0)
    assert focuser._move_timeout == 30.0


@pytest.mark.asyncio
async def test_normal_movement_ismoving_transitions_to_false() -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=1.0)
    target_steps = 500  # (10.0mm * 1000 um/mm) / 20.0 um/step

    moving_states = [True, True, False]

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            if moving_states:
                return moving_states.pop(0)
            return False
        if prop == "Position":
            return target_steps
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        pass

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    await focuser._set_focus(10.0)

    assert focuser.motion_status() == MotionStatus.POSITIONED
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_position_in_tolerance_while_moving_continues_waiting() -> None:
    """Do not declare success while IsMoving=True even if Position is within tolerance."""
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=1.0)
    target_steps = 500

    poll_count = 0

    async def mock_get(prop: str) -> Any:
        nonlocal poll_count
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            poll_count += 1
            return poll_count <= 2
        if prop == "Position":
            return target_steps
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        pass

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    await focuser._set_focus(10.0)

    assert poll_count >= 3
    assert focuser.motion_status() == MotionStatus.POSITIONED
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_stopped_outside_tolerance_raises_move_error() -> None:
    """If device stops (IsMoving=False) outside tolerance, raise MoveError immediately."""
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=1.0)

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            return False
        if prop == "Position":
            return 400  # Target is 500, diff is 100 > 10 steps
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        pass

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    with pytest.raises(exc.MoveError, match="outside tolerance"):
        await focuser._set_focus(10.0)

    assert focuser.motion_status() == MotionStatus.ERROR
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_device_remains_moving_past_deadline_raises_timeout() -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=0.05)

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            return True  # Never stops moving
        return 0

    halt_called = False

    async def mock_put(action: str, **kwargs: Any) -> None:
        nonlocal halt_called
        if action == "Halt":
            halt_called = True

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    with pytest.raises(exc.MoveError, match="timed out waiting for position"):
        await focuser._set_focus(10.0)

    assert halt_called is True
    assert focuser.motion_status() == MotionStatus.ERROR
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_halt_failure_during_timeout_logged_and_raises_timeout_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=0.05)

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            return True
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        if action == "Halt":
            raise RuntimeError("Halt network error")

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        with pytest.raises(exc.MoveError, match="timed out waiting for position"):
            await focuser._set_focus(10.0)

    assert "Failed to halt focuser after timeout" in caplog.text
    assert focuser.motion_status() == MotionStatus.ERROR
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_already_at_requested_position_succeeds_immediately() -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=1.0)
    target_steps = 500

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            return False  # Already stopped
        if prop == "Position":
            return target_steps
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        pass

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    await focuser._set_focus(10.0)

    assert focuser.motion_status() == MotionStatus.POSITIONED
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_unit_conversion_command_and_state() -> None:
    """Verify physical formula: steps = (focus_mm * 1000) / step_size_um."""
    focuser = AlpacaFocuser(**_FOCUSER_ARGS)
    sent_move_pos = None

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0  # 20 um per step
        if prop == "Position":
            return 1250  # 25.0 mm * 1000 / 20 = 1250 steps
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        nonlocal sent_move_pos
        if action == "Move":
            sent_move_pos = kwargs.get("Position")

    published_state = None

    async def mock_set_state(interface: Any, state: Any) -> None:
        nonlocal published_state
        published_state = state

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]
    focuser.comm.set_state = mock_set_state  # type: ignore[assignment]

    await focuser.set_focus(25.0)

    # 25.0 mm * 1000.0 um/mm / 20.0 um/step = 1250 steps
    assert sent_move_pos == 1250
    assert published_state is not None
    assert pytest.approx(published_state.focus, 1e-4) == 25.0


@pytest.mark.asyncio
async def test_unit_conversion_fits_header() -> None:
    """Verify FITS header calculation: focus_mm = (Position * StepSize) / 1000.0."""
    focuser = AlpacaFocuser(**_FOCUSER_ARGS)

    async def mock_get(prop: str) -> Any:
        if prop == "Position":
            return 1250  # steps
        if prop == "StepSize":
            return 20.0  # um
        return 0

    focuser._device.get = mock_get  # type: ignore[assignment]

    hdr = await focuser.get_fits_header_before()
    assert "TEL-FOCU" in hdr
    assert pytest.approx(hdr["TEL-FOCU"].value, 1e-4) == 25.0


@pytest.mark.asyncio
async def test_unit_conversion_set_focus_offset() -> None:
    """Verify focus offset calculation with unit conversion."""
    focuser = AlpacaFocuser(**_FOCUSER_ARGS)
    sent_move_pos = None

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "Position":
            return 1250 if sent_move_pos is None else sent_move_pos
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        nonlocal sent_move_pos
        if action == "Move":
            sent_move_pos = kwargs.get("Position")

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    await focuser.set_focus_offset(2.5)

    assert focuser._focus_offset == 2.5
    # current focus: (1250 * 20) / 1000 - 0 = 25.0 mm
    # new focus: 25.0 + 2.5 = 27.5 mm -> (27.5 * 1000) / 20 = 1375 steps
    assert sent_move_pos == 1375


@pytest.mark.asyncio
async def test_focuser_move_abort() -> None:
    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=1.0)

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            focuser._abort_motion.set()
            return True
        return 0

    halt_called = False

    async def mock_put(action: str, **kwargs: Any) -> None:
        nonlocal halt_called
        if action == "Halt":
            halt_called = True

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    with pytest.raises(InterruptedError, match="Setting focus aborted"):
        await focuser._set_focus(10.0)

    assert halt_called is True
    assert focuser.motion_status() == MotionStatus.POSITIONED
    assert not focuser._lock_motion.locked()


@pytest.mark.asyncio
async def test_position_read_after_deadline_raises_timeout() -> None:
    """Ensure deadline checks cannot accept an operation as successful if position read finishes after deadline."""
    import asyncio

    focuser = AlpacaFocuser(**_FOCUSER_ARGS, move_timeout=0.05)
    target_steps = 500

    async def mock_get(prop: str) -> Any:
        if prop == "StepSize":
            return 20.0
        if prop == "IsMoving":
            return False  # Reports stopped
        if prop == "Position":
            await asyncio.sleep(0.06)  # Exceed deadline during position read
            return target_steps
        return 0

    async def mock_put(action: str, **kwargs: Any) -> None:
        pass

    focuser._device.get = mock_get  # type: ignore[assignment]
    focuser._device.put = mock_put  # type: ignore[assignment]

    with pytest.raises(exc.MoveError, match="timed out waiting for position"):
        await focuser._set_focus(10.0)

    assert focuser.motion_status() == MotionStatus.ERROR
    assert not focuser._lock_motion.locked()
