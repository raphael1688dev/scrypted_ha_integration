"""Config flow for Scrypted integration.

Scrypted exposes no stable server identifier (the login answers with a
container hostname, addresses and a per-user token that changes with the
password), so config entries carry no unique ID. Duplicates are recognised by
host instead, and a flow that changes the host cannot prove it still points at
the same server; reauthentication therefore never changes the host.
"""

from collections.abc import Mapping
import logging
from typing import Any

from aiohttp import ClientError, ClientResponseError
from scrypted_sdk import ScryptedConnectionError, ScryptedDeviceType, ScryptedInterface
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
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

from .const import (
    CONF_AUTO_REGISTER_RESOURCES,
    CONF_DEVICE_TYPES,
    CONF_ENABLE_ENTITIES,
    CONF_EXCLUDED_PLUGINS,
    CONF_SCRYPTED_NVR,
    DEFAULT_DEVICE_TYPES,
    DEFAULT_ICON,
    DEFAULT_NAME,
    DOMAIN,
    EXCLUDED_DEVICE_TYPES,
    HA_PLUGIN_ID,
)
from .http import InvalidAuth, InvalidHost, retrieve_token
from .sdk import get_base_url

_LOGGER = logging.getLogger(__name__)

# All scrypted device types a user might want mirrored as entities
# (server plumbing types are not offered).
DEVICE_TYPE_OPTIONS = sorted(
    device_type.value
    for device_type in ScryptedDeviceType
    if device_type.value not in EXCLUDED_DEVICE_TYPES
)

_TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_USERNAME = TextSelector(
    TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="username")
)
_PASSWORD = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): _TEXT,
        vol.Required(CONF_USERNAME): _USERNAME,
        vol.Required(CONF_PASSWORD): _PASSWORD,
    }
)
# Leaving the password empty keeps the stored one, so the form never has to
# carry it to the browser.
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): _TEXT,
        vol.Required(CONF_USERNAME): _USERNAME,
        vol.Optional(CONF_PASSWORD): _PASSWORD,
    }
)
CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): _USERNAME,
        vol.Required(CONF_PASSWORD): _PASSWORD,
    }
)


def _normalized_host(host: Any) -> str | None:
    """Return the server URL a host setting points at, for comparing hosts.

    '1.2.3.4' and '1.2.3.4:10443' are the same server. None for anything that
    is not a usable host.
    """
    if not isinstance(host, str) or not host.strip():
        return None
    try:
        return get_base_url(host.strip()).lower()
    except ScryptedConnectionError:
        return None


def _suggested(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return form suggestions without the password, which never goes back out."""
    return {
        key: value
        for key, value in (values or {}).items()
        if key != CONF_PASSWORD and value is not None
    }


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


class ScryptedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Scrypted config flow."""

    VERSION = 1
    # 1.2: entries no longer carry a host-based unique ID, and the token
    # sensor's unique ID no longer is the host (see async_migrate_entry).
    MINOR_VERSION = 2

    async def _async_validate(self, data: Mapping[str, Any]) -> str | None:
        """Log in with data; return a form error key, or None when it works."""
        session = async_get_clientsession(self.hass, verify_ssl=False)
        try:
            await retrieve_token(data, session)
        except InvalidHost:
            return "invalid_host"
        except InvalidAuth:
            return "invalid_auth"
        except ClientResponseError as err:
            return "invalid_auth" if err.status in (401, 403) else "cannot_connect"
        except (ClientError, TimeoutError, ValueError):
            # ValueError: a reply that is not Scrypted's JSON login answer.
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error logging in to Scrypted")
            return "unknown"
        return None

    @callback
    def _host_taken(self, host: str, ignore_entry_id: str | None = None) -> bool:
        """Return True when another entry already points at this server."""
        wanted = _normalized_host(host)
        return wanted is not None and any(
            _normalized_host(entry.data.get(CONF_HOST)) == wanted
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != ignore_entry_id
        )

    @callback
    def _default_name(self, host: str) -> str:
        """Return a sidebar title no other entry uses yet."""
        taken = {
            entry.options.get(CONF_NAME, entry.data.get(CONF_NAME))
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        return DEFAULT_NAME if DEFAULT_NAME not in taken else f"{DEFAULT_NAME} ({host})"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle user flow."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._host_taken(user_input[CONF_HOST]):
                return self.async_abort(reason="already_configured")
            if error := await self._async_validate(user_input):
                errors["base"] = error
            else:
                host = user_input[CONF_HOST]
                return self.async_create_entry(
                    title=host,
                    data=user_input,
                    # The sidebar panel's title and icon are settings, not
                    # connection data; the options flow changes them.
                    options={
                        CONF_NAME: self._default_name(host),
                        CONF_ICON: DEFAULT_ICON,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, _suggested(user_input)
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

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials for the same host."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            if error := await self._async_validate(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, _suggested(user_input or entry.data)
            ),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def async_step_upgrade(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store credentials for an entry created before they were kept.

        Such entries keep everything in options, possibly the host too, so this
        form asks for the host as well.
        """
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if self._host_taken(user_input[CONF_HOST], entry.entry_id):
                errors[CONF_HOST] = "already_configured"
            elif error := await self._async_validate(user_input):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **user_input}
                )

        return self.async_show_form(
            step_id="upgrade",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, _suggested(user_input or {**entry.options, **entry.data})
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user change the host or the credentials.

        Without a server identifier this cannot check that a new host is the
        same Scrypted server; it only refuses a host another entry already uses.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            if not user_input.get(CONF_PASSWORD):
                data[CONF_PASSWORD] = entry.data.get(CONF_PASSWORD, "")
            if self._host_taken(data[CONF_HOST], entry.entry_id):
                errors[CONF_HOST] = "already_configured"
            elif error := await self._async_validate(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA, _suggested(user_input or entry.data)
            ),
            errors=errors,
        )

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
        options = self.config_entry.options
        data = self.config_entry.data
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **options,
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_ICON: user_input[CONF_ICON],
                    CONF_AUTO_REGISTER_RESOURCES: user_input[
                        CONF_AUTO_REGISTER_RESOURCES
                    ],
                    CONF_SCRYPTED_NVR: user_input[CONF_SCRYPTED_NVR],
                    CONF_ENABLE_ENTITIES: user_input[CONF_ENABLE_ENTITIES],
                    CONF_DEVICE_TYPES: user_input[CONF_DEVICE_TYPES],
                    CONF_EXCLUDED_PLUGINS: user_input[CONF_EXCLUDED_PLUGINS],
                }
            )

        def current(key: str, default: Any) -> Any:
            # Older entries kept some of these in data until their next setup.
            return options.get(key, data.get(key, default))

        current_plugins = options.get(CONF_EXCLUDED_PLUGINS, [])

        return self.async_show_form(
            step_id="general",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=current(CONF_NAME, DEFAULT_NAME)
                    ): _TEXT,
                    vol.Required(
                        CONF_ICON, default=current(CONF_ICON, DEFAULT_ICON)
                    ): IconSelector(IconSelectorConfig()),
                    vol.Required(
                        CONF_AUTO_REGISTER_RESOURCES,
                        default=current(CONF_AUTO_REGISTER_RESOURCES, False),
                    ): bool,
                    vol.Required(
                        CONF_SCRYPTED_NVR, default=current(CONF_SCRYPTED_NVR, False)
                    ): bool,
                    vol.Required(
                        CONF_ENABLE_ENTITIES,
                        default=options.get(CONF_ENABLE_ENTITIES, True),
                    ): bool,
                    vol.Required(
                        CONF_DEVICE_TYPES,
                        default=options.get(CONF_DEVICE_TYPES, DEFAULT_DEVICE_TYPES),
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
