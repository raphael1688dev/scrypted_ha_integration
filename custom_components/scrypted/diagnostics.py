"""Diagnostics for the Scrypted integration."""

from __future__ import annotations

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .entity import exposed_device

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return the entry settings and a summary of what scrypted reports.

    Device names, rooms and ids stay out: only counts by type and plugin are
    needed to see why a device did or did not get entities. The login token is
    never included.
    """
    client = config_entry.runtime_data.client
    summary: dict[str, Any] | None = None
    if client is not None:
        reported: Counter[str] = Counter()
        exposed: Counter[str] = Counter()
        plugins: Counter[str] = Counter()
        if client.sdk is not None:
            system_manager = client.sdk.systemManager
            for device_id in client.device_ids:
                device = system_manager.getDeviceById(device_id)
                if device is None:
                    continue
                device_type = device.type or "Unknown"
                reported[device_type] += 1
                plugins[device.pluginId or "Unknown"] += 1
                if exposed_device(client, device_id) is not None:
                    exposed[device_type] += 1
        summary = {
            "connected": client.connected,
            "reported_device_types": dict(sorted(reported.items())),
            "exposed_device_types": dict(sorted(exposed.items())),
            "plugins": dict(sorted(plugins.items())),
        }

    return {
        "data": async_redact_data(dict(config_entry.data), TO_REDACT),
        "options": dict(config_entry.options),
        "entities_enabled": client is not None,
        "scrypted": summary,
    }
