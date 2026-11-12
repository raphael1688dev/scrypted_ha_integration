"""Config flow for Scrypted integration."""

from collections.abc import Mapping
from typing import Any

from aiohttp import ClientError
from scrypted_sdk import ScryptedDeviceType, ScryptedInterface
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_ICON,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    IconSelector,
    IconSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .const import (
    CONF_AUTO_REGISTER_RESOURCES,
    CONF_DEVICE_TYPES,
    CONF_ENABLE_ENTITIES,
    CONF_EXCLUDED_PLUGINS,
    CONF_SCRYPTED_NVR,
    DEFAULT_DEVICE_TYPES,
    DOMAIN,
    EXCLUDED_DEVICE_TYPES,
    HA_PLUGIN_ID,
)
from .http import retrieve_token

# All scrypted device types a user might want mirrored as entities
# (server plumbing types are not offered).
DEVICE_TYPE_OPTIONS = sorted(
    device_type.value
    for device_type in ScryptedDeviceType
    if device_type.value not in EXCLUDED_DEVICE_TYPES
)


def _plugin_options(
    entry: config_entries.ConfigEntry, selected: list[str]
) -> list[SelectOptionDict]:
    """Return the choices for the plugin exclusion option.

    Live plugins that own at least one device of a type the options offer,
    labelled with their plugin device's name, plus every already-selected
    plugin so a saved choice stays visible when the server no longer reports
    it or is disconnected.
    """
    labels = {plugin_id: plugin_id for plugin_id in selected}
    runtime_data = getattr(entry, "runtime_data", None)
    client = runtime_data.client if runtime_data else None
    if client is not None and client.sdk is not None:
        system_manager = client.sdk.systemManager
        devices = [
            device
            for device_id in system_manager.getSystemState()
            if (device := system_manager.getDeviceById(device_id)) is not None
        ]
        names = {
            device.pluginId: device.name
            for device in devices
            if ScryptedInterface.ScryptedPlugin.value in (device.interfaces or [])
        }
        for device in devices:
            plugin_id = device.pluginId
            if (
                not plugin_id
                # always excluded, so never offered
                or plugin_id == HA_PLUGIN_ID
                or device.type not in DEVICE_TYPE_OPTIONS
            ):
                continue
            name = names.get(plugin_id)
            labels[plugin_id] = f"{name} ({plugin_id})" if name else plugin_id
    return [
        SelectOptionDict(value=plugin_id, label=label)
        for plugin_id, label in sorted(labels.items(), key=lambda item: item[1].lower())
    ]


def text_selector(selector_type: TextSelectorType) -> TextSelector:
    """Create a text selector."""
    return TextSelector(TextSelectorConfig(type=selector_type))


def _get_config_schema(
    default: Mapping[str, Any] | None,
    *,
    include_nvr: bool = False,
    include_auto_register: bool = False,
) -> vol.Schema:
    """Get config schema."""
    if not default:
        default = {}
    auto_register_value = default.get(CONF_AUTO_REGISTER_RESOURCES, False)
    schema: dict[Any, Any] = {
        vol.Required(
            CONF_NAME, default=default.get(CONF_NAME, DOMAIN.title())
        ): text_selector(TextSelectorType.TEXT),
        vol.Required(
            CONF_ICON, default=default.get(CONF_ICON, "mdi:memory")
        ): IconSelector(IconSelectorConfig()),
        vol.Required(CONF_HOST, default=default.get(CONF_HOST)): text_selector(
            TextSelectorType.TEXT
        ),
        vol.Required(CONF_USERNAME, default=default.get(CONF_USERNAME)): text_selector(
            TextSelectorType.TEXT
        ),
        vol.Required(CONF_PASSWORD, default=default.get(CONF_PASSWORD)): text_selector(
            TextSelectorType.PASSWORD
        ),
    }
    if include_nvr:
        schema[
            vol.Optional(
                CONF_SCRYPTED_NVR, default=default.get(CONF_SCRYPTED_NVR, False)
            )
        ] = bool
    if include_auto_register:
        schema[
            vol.Required(CONF_AUTO_REGISTER_RESOURCES, default=auto_register_value)
        ] = bool
    return vol.Schema(schema)


class ScryptedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Scrypted config flow."""

    VERSION = 1

    async def validate_input(self, data: dict[str, Any]) -> bool:
        """Validate that the host is valid."""
        session = async_get_clientsession(self.hass, verify_ssl=False)
        required_keys = [
            CONF_HOST,
            CONF_ICON,
            CONF_NAME,
            CONF_USERNAME,
        ]
        if CONF_AUTO_REGISTER_RESOURCES in data:
            required_keys.append(CONF_AUTO_REGISTER_RESOURCES)
        for key in required_keys:
            if key not in data:
                return False
        try:
            await retrieve_token(data, session)
        except (ValueError, ClientError, TimeoutError):
            # No token in the reply, a malformed host, or an unreachable or
            # rejecting server: the form reports them all the same way.
            return False

        return True

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user flow."""
        errors = {}
        if user_input is not None and CONF_USERNAME in user_input:
            if await self.validate_input(user_input):
                await self.async_set_unique_id(slugify(user_input[CONF_HOST]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_HOST], data=user_input
                )
            errors["base"] = "invalid_host_or_credentials"

        return self.async_show_form(
            step_id="user",
            data_schema=_get_config_schema(
                user_input,
                include_nvr=True,
                include_auto_register=True,
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth flow."""
        if CONF_PASSWORD not in entry_data:
            return await self.async_step_upgrade()
        return await self.async_step_credentials()

    async def _async_step_update_entry(
        self, step_id: str, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a form that rewrites the existing entry (reauth or reconfigure)."""
        errors = {}
        config_entry = (
            self._get_reauth_entry()
            if self.source == SOURCE_REAUTH
            else self._get_reconfigure_entry()
        )
        if user_input is not None:
            if not await self.validate_input(user_input):
                errors["base"] = "invalid_host_or_credentials"
            else:
                unique_id = slugify(user_input[CONF_HOST])
                if any(
                    entry.unique_id == unique_id
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                    if entry.entry_id != config_entry.entry_id
                ):
                    errors[CONF_HOST] = "already_configured"
                else:
                    # Keep options this form does not collect (the entity
                    # options); the fields it does collect win and live in
                    # options, never in data.
                    data = dict(user_input)
                    options = dict(config_entry.options)
                    for key in (CONF_SCRYPTED_NVR, CONF_AUTO_REGISTER_RESOURCES):
                        if key in data:
                            options[key] = data.pop(key)
                    return self.async_update_reload_and_abort(
                        config_entry,
                        unique_id=unique_id,
                        data=data,
                        options=options,
                    )

        # Entries from before credentials were stored keep everything in options.
        defaults = user_input or {**config_entry.options, **config_entry.data}
        include_panel_options = step_id == "upgrade"

        return self.async_show_form(
            step_id=step_id,
            data_schema=_get_config_schema(
                defaults,
                include_nvr=include_panel_options,
                include_auto_register=include_panel_options,
            ),
            errors=errors,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle credentials step."""
        return await self._async_step_update_entry("credentials", user_input)

    async def async_step_upgrade(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle upgrade step."""
        return await self._async_step_update_entry("upgrade", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the host, credentials, or panel name and icon."""
        return await self._async_step_update_entry("reconfigure", user_input)

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlowWithReload:
        """Return the options flow handler for this config entry."""
        return ScryptedOptionsFlowHandler()


class ScryptedOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Handle Scrypted options; saving them reloads the entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initialize the options flow."""
        return await self.async_step_general(user_input)

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle general options."""
        if user_input is not None:
            data = {
                **self.config_entry.options,
                CONF_AUTO_REGISTER_RESOURCES: user_input[CONF_AUTO_REGISTER_RESOURCES],
                CONF_SCRYPTED_NVR: user_input[CONF_SCRYPTED_NVR],
                CONF_ENABLE_ENTITIES: user_input[CONF_ENABLE_ENTITIES],
                CONF_DEVICE_TYPES: user_input[CONF_DEVICE_TYPES],
                CONF_EXCLUDED_PLUGINS: user_input[CONF_EXCLUDED_PLUGINS],
            }
            return self.async_create_entry(data=data)

        current_auto = self.config_entry.options.get(CONF_AUTO_REGISTER_RESOURCES)
        if current_auto is None:
            current_auto = self.config_entry.data.get(
                CONF_AUTO_REGISTER_RESOURCES, False
            )

        current_nvr = self.config_entry.options.get(CONF_SCRYPTED_NVR)
        if current_nvr is None:
            current_nvr = self.config_entry.data.get(CONF_SCRYPTED_NVR, False)

        current_entities = self.config_entry.options.get(CONF_ENABLE_ENTITIES, True)
        current_types = self.config_entry.options.get(
            CONF_DEVICE_TYPES, DEFAULT_DEVICE_TYPES
        )
        current_plugins = self.config_entry.options.get(CONF_EXCLUDED_PLUGINS, [])

        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTO_REGISTER_RESOURCES, default=current_auto
                    ): bool,
                    vol.Required(CONF_SCRYPTED_NVR, default=current_nvr): bool,
                    vol.Required(CONF_ENABLE_ENTITIES, default=current_entities): bool,
                    vol.Required(
                        CONF_DEVICE_TYPES, default=current_types
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=DEVICE_TYPE_OPTIONS,
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_EXCLUDED_PLUGINS, default=current_plugins
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_plugin_options(self.config_entry, current_plugins),
                            multiple=True,
                            custom_value=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )
