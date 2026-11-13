"""Tests covering the Scrypted config flow."""

from __future__ import annotations

from aiohttp import ClientConnectionError, ClientResponseError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.const import (
    CONF_HOST,
    CONF_ICON,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResultType

from custom_components.scrypted import config_flow, http
from custom_components.scrypted.const import (
    CONF_AUTO_REGISTER_RESOURCES,
    CONF_ENABLE_ENTITIES,
    CONF_EXCLUDED_PLUGINS,
    CONF_SCRYPTED_NVR,
    DOMAIN,
)
from tests.conftest import setup_entry, state
from tests.const import EXAMPLE_HOST, PASSWORD, USERNAME

CREDENTIALS_INPUT = {
    CONF_USERNAME: USERNAME,
    CONF_PASSWORD: PASSWORD,
}


USER_INPUT = {
    CONF_HOST: EXAMPLE_HOST,
    **CREDENTIALS_INPUT,
}


def _suggested(result, key):
    """Return the suggested value a form pre-fills for key, if any."""
    for field in result["data_schema"].schema:
        if field.schema == key:
            return (field.description or {}).get("suggested_value")
    raise AssertionError(f"{key} not in form")


def _fields(result):
    return [field.schema for field in result["data_schema"].schema]


async def _start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_creates_entry(hass):
    """A new entry keeps connection data in data and panel settings in options."""
    init_result = await _start_user_flow(hass)
    assert _fields(init_result) == [CONF_HOST, CONF_USERNAME, CONF_PASSWORD]
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == EXAMPLE_HOST
    assert result["data"] == USER_INPUT
    assert result["options"] == {CONF_NAME: "Scrypted", CONF_ICON: "mdi:memory"}
    # Scrypted has no stable server ID, and an IP address must not stand in.
    assert result["result"].unique_id is None
    # Let the new entry finish setting up before the test tears down.
    await hass.async_block_till_done()


async def test_user_flow_second_server_gets_its_own_title(hass):
    """A second server's panel does not reuse the first one's title."""
    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "other"},
        options={CONF_NAME: "Scrypted"},
    ).add_to_hass(hass)
    init_result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    assert result["options"][CONF_NAME] == f"Scrypted ({EXAMPLE_HOST})"
    await hass.async_block_till_done()


async def test_user_flow_aborts_for_a_configured_host(hass, mock_retrieve_token):
    """The same server is recognised with or without the default port."""
    MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: f"{EXAMPLE_HOST}:10443"}
    ).add_to_hass(hass)
    init_result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Recognised before any login is attempted.
    mock_retrieve_token["config_flow"].assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http.InvalidAuth(), "invalid_auth"),
        (http.InvalidHost(), "invalid_host"),
        (ClientResponseError(None, (), status=401), "invalid_auth"),
        (ClientResponseError(None, (), status=500), "cannot_connect"),
        (ClientConnectionError(), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
        (ValueError("not JSON"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(hass, patch_retrieve_token, error, expected):
    """Each login failure gets its own message, and the password is not echoed."""
    init_result = await _start_user_flow(hass)

    async def _fail(*args, **kwargs):
        raise error

    with patch_retrieve_token(_fail):
        result = await hass.config_entries.flow.async_configure(
            init_result["flow_id"], USER_INPUT
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert _suggested(result, CONF_HOST) == EXAMPLE_HOST
    assert _suggested(result, CONF_USERNAME) == USERNAME
    assert _suggested(result, CONF_PASSWORD) is None


def test_normalized_host():
    """Hosts compare by the server they point at."""
    assert config_flow._normalized_host("Example") == "https://example:10443"
    assert config_flow._normalized_host("example:10443") == "https://example:10443"
    assert config_flow._normalized_host("a:b:c") is None
    assert config_flow._normalized_host("  ") is None
    assert config_flow._normalized_host(None) is None


async def test_reauth_asks_only_for_credentials(hass, mock_async_reload):
    """Reauth keeps the host, so it cannot point the entry at another server."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: EXAMPLE_HOST, CONF_USERNAME: "old-user", CONF_PASSWORD: "old"},
        options={CONF_ENABLE_ENTITIES: False},
    )
    entry.add_to_hass(hass)
    init_result = await entry.start_reauth_flow(hass)
    assert init_result["step_id"] == "credentials"
    assert _fields(init_result) == [CONF_USERNAME, CONF_PASSWORD]
    assert init_result["description_placeholders"]["host"] == EXAMPLE_HOST
    assert _suggested(init_result, CONF_USERNAME) == "old-user"
    # The stored password never goes back to the browser.
    assert _suggested(init_result, CONF_PASSWORD) is None

    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], CREDENTIALS_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == {CONF_HOST: EXAMPLE_HOST, **CREDENTIALS_INPUT}
    assert entry.options == {CONF_ENABLE_ENTITIES: False}
    # Exactly one reload: the flow's own, with no update listener adding another.
    mock_async_reload.assert_awaited_once_with(entry.entry_id)


async def test_reauth_credentials_invalid_sets_error(hass, mock_retrieve_token_error):
    """Rejected credentials keep the form open."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: EXAMPLE_HOST, CONF_PASSWORD: "old"}
    )
    entry.add_to_hass(hass)
    init_result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], CREDENTIALS_INPUT
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert _suggested(result, CONF_PASSWORD) is None


async def test_reauth_without_password_shows_upgrade_step(hass):
    """Entries from before credentials were stored get the upgrade form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_HOST: EXAMPLE_HOST, CONF_USERNAME: "old-user"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "upgrade"
    assert _fields(result) == [CONF_HOST, CONF_USERNAME, CONF_PASSWORD]
    # Such entries kept everything in options.
    assert _suggested(result, CONF_HOST) == EXAMPLE_HOST
    assert _suggested(result, CONF_USERNAME) == "old-user"


async def test_upgrade_stores_credentials_and_keeps_options(hass, mock_async_reload):
    """The upgrade form writes the connection; options stay as they were."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: EXAMPLE_HOST},
        options={
            CONF_SCRYPTED_NVR: True,
            CONF_ENABLE_ENTITIES: False,
            "device_types": ["Camera", "Doorbell", "Sensor"],
        },
    )
    entry.add_to_hass(hass)
    init_result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data == USER_INPUT
    assert entry.options[CONF_SCRYPTED_NVR] is True
    assert entry.options[CONF_ENABLE_ENTITIES] is False
    assert entry.options["device_types"] == ["Camera", "Doorbell", "Sensor"]


async def test_upgrade_errors(hass, mock_retrieve_token_client_error):
    """The upgrade form refuses a taken host and reports login failures."""
    MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "taken"}).add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: EXAMPLE_HOST})
    entry.add_to_hass(hass)
    init_result = await entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], {**USER_INPUT, CONF_HOST: "taken"}
    )
    assert result["errors"] == {CONF_HOST: "already_configured"}

    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_updates_entry(hass, mock_async_reload):
    """Reconfigure rewrites the connection details and reloads once."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, CONF_HOST: "old-host"},
        options={CONF_ENABLE_ENTITIES: False},
    )
    entry.add_to_hass(hass)
    init_result = await entry.start_reconfigure_flow(hass)
    assert init_result["type"] == FlowResultType.FORM
    assert init_result["step_id"] == "reconfigure"
    assert _fields(init_result) == [CONF_HOST, CONF_USERNAME, CONF_PASSWORD]
    assert _suggested(init_result, CONF_HOST) == "old-host"
    assert _suggested(init_result, CONF_USERNAME) == USERNAME
    assert _suggested(init_result, CONF_PASSWORD) is None

    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], {**USER_INPUT, CONF_PASSWORD: "new"}
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {**USER_INPUT, CONF_PASSWORD: "new"}
    assert entry.options[CONF_ENABLE_ENTITIES] is False
    mock_async_reload.assert_awaited_once_with(entry.entry_id)


async def test_reconfigure_empty_password_keeps_the_stored_one(
    hass, mock_async_reload, mock_retrieve_token
):
    """Changing only the host does not require typing the password again."""
    entry = MockConfigEntry(domain=DOMAIN, data={**USER_INPUT, CONF_HOST: "old-host"})
    entry.add_to_hass(hass)
    init_result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], {CONF_HOST: "new-host", CONF_USERNAME: USERNAME}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {**USER_INPUT, CONF_HOST: "new-host"}
    # The login was tested with the stored password.
    tested = mock_retrieve_token["config_flow"].call_args.args[0]
    assert tested[CONF_PASSWORD] == PASSWORD


async def test_reconfigure_errors(hass, mock_retrieve_token_error):
    """Reconfigure refuses another entry's host and reports login failures."""
    MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "taken"}).add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    init_result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], {**USER_INPUT, CONF_HOST: "taken:10443"}
    )
    assert result["errors"] == {CONF_HOST: "already_configured"}
    assert _suggested(result, CONF_PASSWORD) is None

    # Its own host is not a duplicate.
    result = await hass.config_entries.flow.async_configure(
        init_result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_flow_updates_entry(hass, mock_async_reload):
    """Test case for test_options_flow_updates_entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_AUTO_REGISTER_RESOURCES: False,
            CONF_SCRYPTED_NVR: False,
        },
    )
    entry.add_to_hass(hass)
    init_result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        init_result["flow_id"],
        {CONF_AUTO_REGISTER_RESOURCES: True, CONF_SCRYPTED_NVR: True},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AUTO_REGISTER_RESOURCES] is True
    assert result["data"][CONF_SCRYPTED_NVR] is True


async def test_options_flow_defaults_to_entry_data(hass):
    """Test case for test_options_flow_defaults_to_entry_data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_AUTO_REGISTER_RESOURCES: True,
            CONF_SCRYPTED_NVR: True,
        },
        options={},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    form_schema = result["data_schema"]
    validated = form_schema({})
    assert validated[CONF_AUTO_REGISTER_RESOURCES] is True
    assert validated[CONF_SCRYPTED_NVR] is True


async def test_options_flow_respects_existing_options(hass):
    """Test case for test_options_flow_respects_existing_options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_AUTO_REGISTER_RESOURCES: False,
            CONF_SCRYPTED_NVR: False,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = list(result["data_schema"].schema.keys())
    (
        name_field,
        icon_field,
        auto_field,
        nvr_field,
        entities_field,
        types_field,
        plugins_field,
    ) = schema_keys
    assert name_field.default() == "Scrypted"
    assert icon_field.default() == "mdi:memory"
    assert auto_field.default() is False
    assert nvr_field.default() is False
    assert entities_field.default() is True
    assert types_field.default() == ["Camera", "Doorbell"]
    assert plugins_field.default() == []


async def test_options_flow_init_shows_general_step(hass):
    """Test that initializing the options flow shows the general step."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: EXAMPLE_HOST},
        options={
            CONF_AUTO_REGISTER_RESOURCES: True,
            CONF_SCRYPTED_NVR: False,
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "general"


async def test_options_flow_complete_end_to_end(hass, mock_async_reload):
    """Test a complete options flow from init to entry creation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: EXAMPLE_HOST,
            CONF_AUTO_REGISTER_RESOURCES: False,
            CONF_SCRYPTED_NVR: False,
        },
        options={},
    )
    entry.add_to_hass(hass)

    # Initialize the options flow
    init_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert init_result["type"] == FlowResultType.FORM
    assert init_result["step_id"] == "general"

    # Configure the options
    result = await hass.config_entries.options.async_configure(
        init_result["flow_id"],
        {CONF_AUTO_REGISTER_RESOURCES: True, CONF_SCRYPTED_NVR: True},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AUTO_REGISTER_RESOURCES] is True
    assert result["data"][CONF_SCRYPTED_NVR] is True


async def test_options_flow_includes_enable_entities(hass, mock_async_reload):
    """Options flow exposes and persists the enable_entities flag."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "1.2.3.4",
            "username": "u",
            "password": "p",
            "name": "Scrypted",
            "icon": "mdi:memory",
        },
        options={
            CONF_AUTO_REGISTER_RESOURCES: False,
            CONF_SCRYPTED_NVR: False,
            CONF_ENABLE_ENTITIES: True,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_AUTO_REGISTER_RESOURCES: False,
            CONF_SCRYPTED_NVR: False,
            CONF_ENABLE_ENTITIES: False,
            "device_types": ["Camera", "Doorbell"],
        },
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_ENABLE_ENTITIES] is False


def _excluded_plugins_field(result):
    """Return (schema key, selector) for the excluded plugins option."""
    for key, selector in result["data_schema"].schema.items():
        if key == CONF_EXCLUDED_PLUGINS:
            return key, selector
    raise AssertionError("excluded_plugins field missing")


async def test_options_flow_lists_plugins_from_live_server(hass, fake_sdk):
    """The plugin choices are live plugins that own user-facing devices."""
    devices = {
        # Owns a user-facing device: offered, labelled by its plugin device.
        "ring_plugin": state(
            name="Ring Plugin",
            type="API",
            info={},
            pluginId="@scrypted/ring",
            interfaces=["ScryptedPlugin"],
        ),
        "ring1": state(
            name="Hall Leak",
            type="Sensor",
            info={},
            pluginId="@scrypted/ring",
            interfaces=["FloodSensor"],
        ),
        # Owns only a type the options never offer: not offered.
        "llm_tools": state(
            name="Web Tools",
            type="LLMTools",
            info={},
            pluginId="@scrypted/llm",
            interfaces=["LLMTools"],
        ),
        # Owns only plumbing: not offered.
        "webrtc_plugin": state(
            name="WebRTC Plugin",
            type="API",
            info={},
            pluginId="@scrypted/webrtc",
            interfaces=["ScryptedPlugin"],
        ),
    }
    fake_sdk.systemManager.systemState.update(devices)
    entry = await setup_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_EXCLUDED_PLUGINS: ["@scrypted/gone"]}
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    key, selector = _excluded_plugins_field(result)
    assert key.default() == ["@scrypted/gone"]
    # @scrypted/homeassistant (haimport1) is always excluded, so never offered;
    # a saved plugin the server no longer has stays selectable.
    assert selector.config["options"] == [
        {"value": "@scrypted/gone", "label": "@scrypted/gone"},
        {"value": "@scrypted/ring", "label": "Ring Plugin (@scrypted/ring)"},
    ]
    assert selector.config["custom_value"] is True

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_AUTO_REGISTER_RESOURCES: False,
            CONF_SCRYPTED_NVR: False,
            CONF_ENABLE_ENTITIES: True,
            "device_types": ["Camera", "Doorbell", "Sensor"],
            CONF_EXCLUDED_PLUGINS: ["@scrypted/ring"],
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EXCLUDED_PLUGINS] == ["@scrypted/ring"]
    # Saving options reloads the entry; let that finish before teardown.
    await hass.async_block_till_done()


async def test_options_flow_plugins_without_connection(hass):
    """Without a live connection the saved plugins remain selectable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_EXCLUDED_PLUGINS: ["@scrypted/ring"]},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    key, selector = _excluded_plugins_field(result)
    assert key.default() == ["@scrypted/ring"]
    assert selector.config["options"] == [
        {"value": "@scrypted/ring", "label": "@scrypted/ring"}
    ]
