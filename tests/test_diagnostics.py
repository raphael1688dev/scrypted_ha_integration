"""Tests for the Scrypted diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from custom_components.scrypted.const import CONF_ENABLE_ENTITIES, DOMAIN
from custom_components.scrypted.diagnostics import async_get_config_entry_diagnostics
from tests.conftest import setup_entry, state


async def test_diagnostics_summarise_devices_and_redact_credentials(hass, fake_sdk):
    """Counts by type and plugin are reported; credentials and the token are not."""
    # A state entry scrypted can no longer resolve is skipped, not counted.
    fake_sdk.systemManager.systemState["ghost"] = state(name="Ghost", type="Camera")
    real_get = fake_sdk.systemManager.getDeviceById
    fake_sdk.systemManager.getDeviceById = lambda device_id: (
        None if device_id == "ghost" else real_get(device_id)
    )
    entry = await setup_entry(hass, device_types=["Camera"])

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["data"][CONF_PASSWORD] == "**REDACTED**"
    assert result["data"][CONF_USERNAME] == "**REDACTED**"
    assert result["data"][CONF_HOST] == entry.data[CONF_HOST]
    assert entry.runtime_data.token not in str(result)
    assert result["entities_enabled"] is True
    summary = result["scrypted"]
    assert summary["connected"] is True
    reported, exposed = (
        summary["reported_device_types"],
        summary["exposed_device_types"],
    )
    assert sum(reported.values()) == len(fake_sdk.systemManager.systemState) - 1
    # Only the allowlisted type (plus smart sensors) is exposed.
    assert exposed["Camera"] <= reported["Camera"]
    assert set(exposed) <= {"Camera", "Sensor"}
    assert sum(summary["plugins"].values()) == sum(reported.values())

    # A dropped connection still yields a report.
    entry.runtime_data.client.sdk = None
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["scrypted"]["reported_device_types"] == {}


async def test_diagnostics_without_entities(hass):
    """With entities off there is no client to summarise."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "1.2.3.4",
            "username": "u",
            "password": "p",
            "name": "Scrypted",
            "icon": "mdi:memory",
        },
        options={CONF_ENABLE_ENTITIES: False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entities_enabled"] is False
    assert result["scrypted"] is None
