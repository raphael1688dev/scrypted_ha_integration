"""The bundled scrypted-camera-card Lovelace card.

Live video on a dashboard comes from this card rather than from the camera
entities, which are snapshot-only. The card is developed in its own repository
(raphael1688dev/scrypted-camera-card, a fork of digaus/scrypted-camera-card)
and stays usable on its own, with this integration or with koush/ha_scrypted.
The built file of one tagged release is vendored under frontend/ and refreshed
with scripts/update_card.py, which also rewrites the two constants below.

A copy the user installed themselves always wins: while the card is registered
as a dashboard resource the bundled copy is not loaded. That keeps a separately
installed or locally built card working unchanged, and means the two never
load together (the card defines its custom elements unconditionally, so a
second copy would fail).
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import DOMAIN as LL_DOMAIN
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Rewritten by scripts/update_card.py; do not edit by hand.
CARD_VERSION = "0.5.10"
CARD_SHA256 = "56892fa9477c1297641339e302626cc12ca10a02c8646e90964a39c9812798c1"

CARD_FILENAME = "scrypted-camera-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / CARD_FILENAME
# Outside /api/scrypted/, which is the proxy to the scrypted server.
CARD_URL = f"/{DOMAIN}_static/{CARD_FILENAME}"
# The version query is what gets a new card past the browser cache, since the
# static path is served with long-lived cache headers.
CARD_MODULE_URL = f"{CARD_URL}?v={CARD_VERSION}"

# Whether this integration currently has the card on the frontend's extra
# module list. Kept here rather than read back from the frontend's own data,
# which is not part of its public API.
DATA_CARD_LOADED: HassKey[bool] = HassKey(f"{DOMAIN}_card_loaded")


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled card. Whether pages load it is async_sync_card's call."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )


async def _async_standalone_card_urls(hass: HomeAssistant) -> list[str]:
    """Return dashboard resource URLs that load a separately installed card."""
    lovelace_data = hass.data.get(LL_DOMAIN)
    if not lovelace_data or not lovelace_data.resources:
        return []
    resources = lovelace_data.resources
    # Loads a storage collection on first use; YAML collections are static.
    await resources.async_get_info()
    return [
        url
        for item in resources.async_items()
        if CARD_FILENAME in (url := item.get(CONF_URL) or "")
        and not url.startswith(CARD_URL)
    ]


async def async_sync_card(hass: HomeAssistant) -> None:
    """Load the bundled card on frontend pages unless the user has their own.

    Loaded as an extra module URL rather than a Lovelace resource: that works
    the same in storage and YAML dashboard modes and leaves nothing behind to
    clean up. Called on every entry (re)load, so adding or removing a resource
    takes effect after reloading the integration and the page.
    """
    loaded = hass.data.get(DATA_CARD_LOADED, False)
    standalone = await _async_standalone_card_urls(hass)
    if standalone:
        if loaded:
            remove_extra_js_url(hass, CARD_MODULE_URL)
            hass.data[DATA_CARD_LOADED] = False
        _LOGGER.info(
            "Not loading the bundled scrypted-camera-card %s: the card is "
            "registered as a dashboard resource (%s), which takes precedence",
            CARD_VERSION,
            ", ".join(standalone),
        )
    elif not loaded:
        add_extra_js_url(hass, CARD_MODULE_URL)
        hass.data[DATA_CARD_LOADED] = True
