from unittest.mock import patch

import pytest
from pyobs.interfaces import FitsHeaderEntry
from pyobs.modules.telescope.basetelescope import BaseTelescope

from pyobs_alpaca import AlpacaTelescope


@pytest.mark.asyncio
@pytest.mark.parametrize("sender, namespaces", [("camera", None), ("other", ["camera"]), (None, ["camera"])])
async def test_header_override_forwards_context_and_filters_added_cards(sender, namespaces):
    telescope = AlpacaTelescope(
        server="localhost",
        port=11111,
        device_type="telescope",
        device=0,
        fits_namespaces={"camera": ["TEL-RA", "RAOFF"]},
    )
    seen = []

    async def filtered_base(self, namespaces=None, **kwargs):
        seen.append((namespaces, kwargs["sender"]))
        return self._filter_fits_namespace({"TEL-RA": FitsHeaderEntry(10, "RA")}, namespaces=namespaces, **kwargs)

    kwargs = {} if sender is None else {"sender": sender}
    with patch.object(BaseTelescope, "get_fits_header_before", filtered_base):
        header = await telescope.get_fits_header_before(namespaces=namespaces, **kwargs)
    assert seen == [(namespaces, sender or "")]
    assert set(header) == {"TEL-RA", "RAOFF"}
    assert header["TEL-RA"].value == 10
    assert header["RAOFF"].value == 0
