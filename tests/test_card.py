"""Tests for the bundled scrypted-camera-card."""

from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.components.lovelace.const import DOMAIN as LL_DOMAIN
from homeassistant.const import CONF_ID, CONF_URL

from custom_components.scrypted import card
from tests.conftest import setup_entry
from tests.test_init import FakeStorageResources

STANDALONE_URL = "/local/community/scrypted-camera-card/scrypted-camera-card.js"


def _bundled_card_loaded(hass) -> bool:
    return card.CARD_MODULE_URL in hass.data[DATA_EXTRA_MODULE_URL].urls


def test_vendored_bundle_matches_recorded_release():
    """The file on disk is the release card.py says it is.

    A bundle replaced by hand, or a version bumped without the file, would
    otherwise ship a card that misreports itself and defeats the cache-busting
    query string.
    """
    bundle = card.CARD_PATH.read_bytes()
    assert hashlib.sha256(bundle).hexdigest() == card.CARD_SHA256
    assert card.CARD_VERSION.encode() in bundle
    assert card.CARD_MODULE_URL.endswith(f"?v={card.CARD_VERSION}")
    assert (card.CARD_PATH.parent / "LICENSE.scrypted-camera-card").is_file()


async def test_card_is_served_and_loaded_on_every_page(hass, hass_client):
    """Setup serves the bundle and registers it as a frontend module."""
    entry = await setup_entry(hass)

    assert _bundled_card_loaded(hass)
    # Not under the proxy to the scrypted server.
    assert not card.CARD_URL.startswith("/api/")

    client = await hass_client()
    response = await client.get(card.CARD_MODULE_URL)
    assert response.status == 200
    assert await response.read() == card.CARD_PATH.read_bytes()

    # A reload neither drops the module nor registers it a second time.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _bundled_card_loaded(hass)


async def test_standalone_card_takes_precedence(hass, caplog):
    """A card the user installed themselves stops the bundled copy loading.

    Covers the standalone install from the Marketplace and a locally built card
    under development alike: both are dashboard resources.
    """
    await setup_entry(hass)
    assert _bundled_card_loaded(hass)

    resources = FakeStorageResources(
        [
            {CONF_ID: 1, CONF_URL: f"{STANDALONE_URL}?hacstag=123"},
            {CONF_ID: 2, CONF_URL: "/local/other-card.js"},
            # A resource entry without a URL must not break the scan.
            {CONF_ID: 3},
        ]
    )
    hass.data[LL_DOMAIN] = SimpleNamespace(resources=resources)

    with caplog.at_level(logging.INFO):
        await card.async_sync_card(hass)
    assert resources.loaded
    assert not _bundled_card_loaded(hass)
    assert STANDALONE_URL in caplog.text

    # Still standing down on the next reload, with nothing left to remove.
    await card.async_sync_card(hass)
    assert not _bundled_card_loaded(hass)

    # Removing the resource hands the job back to the bundled card.
    await resources.async_delete_item(1)
    await card.async_sync_card(hass)
    assert _bundled_card_loaded(hass)


async def test_bundled_card_url_as_a_resource_is_not_standalone(hass):
    """The integration's own URL registered as a resource does not count."""
    await setup_entry(hass)
    resources = FakeStorageResources([{CONF_ID: 1, CONF_URL: f"{card.CARD_URL}?v=1"}])
    resources.loaded = True
    hass.data[LL_DOMAIN] = SimpleNamespace(resources=resources)

    await card.async_sync_card(hass)
    assert _bundled_card_loaded(hass)


async def test_no_lovelace_resources_loads_the_bundled_card(hass):
    """Without any dashboard resources there is nothing to defer to."""
    await setup_entry(hass)
    hass.data[LL_DOMAIN] = SimpleNamespace(resources=None)
    await card.async_sync_card(hass)
    assert _bundled_card_loaded(hass)

    hass.data.pop(LL_DOMAIN)
    await card.async_sync_card(hass)
    assert _bundled_card_loaded(hass)
