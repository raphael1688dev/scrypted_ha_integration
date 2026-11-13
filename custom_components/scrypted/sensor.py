"""Sensors for the Scrypted integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfDensity,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_NAME, DOMAIN
from .entity import (
    ScryptedDeviceEntity,
    ScryptedEntityDescriptionMixin,
    async_setup_scrypted_platform,
    device_matches,
)
from .sdk import get_base_url

TOKEN_KEY = "token"


@dataclass(frozen=True, kw_only=True)
class ScryptedSensorDescription(
    SensorEntityDescription, ScryptedEntityDescriptionMixin
):
    """Describes a scrypted sensor."""


SENSORS: tuple[ScryptedSensorDescription, ...] = (
    ScryptedSensorDescription(
        key="temperature",
        translation_key="temperature",
        interface="Thermometer",
        state_property="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="humidity",
        translation_key="humidity",
        interface="HumiditySensor",
        state_property="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="battery",
        translation_key="battery",
        interface="Battery",
        state_property="batteryLevel",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ScryptedSensorDescription(
        key="ambient_light",
        translation_key="ambient_light",
        interface="AmbientLightSensor",
        state_property="ambientLight",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="luminance",
        translation_key="luminance",
        interface="LuminanceSensor",
        state_property="luminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="ultraviolet",
        translation_key="ultraviolet",
        interface="UltravioletSensor",
        state_property="ultraviolet",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="co2",
        translation_key="co2",
        interface="CO2Sensor",
        state_property="co2ppm",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="pm25",
        translation_key="pm25",
        interface="PM25Sensor",
        state_property="pm25Density",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="pm10",
        translation_key="pm10",
        interface="PM10Sensor",
        state_property="pm10Density",
        device_class=SensorDeviceClass.PM10,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="voc",
        translation_key="voc",
        interface="VOCSensor",
        state_property="vocDensity",
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="nox",
        translation_key="nox",
        interface="NOXSensor",
        state_property="noxDensity",
        native_unit_of_measurement=UnitOfDensity.MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ScryptedSensorDescription(
        key="air_quality",
        translation_key="air_quality",
        interface="AirQualitySensor",
        state_property="airQuality",
        device_class=SensorDeviceClass.ENUM,
        options=["Excellent", "Good", "Fair", "Inferior", "Poor", "Unknown"],
    ),
)


# State is pushed by scrypted and commands are independent RPC calls.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Scrypted sensors from config entry."""
    async_add_entities(
        [ScryptedTokenSensor(config_entry, config_entry.runtime_data.token)]
    )

    client = config_entry.runtime_data.client

    def _discover(device_id: str) -> list[ScryptedSensor]:
        return [
            ScryptedSensor(client, config_entry, device_id, description)
            for description in SENSORS
            if device_matches(client, device_id, description.interface)
        ]

    await async_setup_scrypted_platform(
        hass, config_entry, async_add_entities, _discover
    )


class ScryptedTokenSensor(SensorEntity):
    """The login token of the Scrypted account this entry uses.

    Nothing reads this at runtime; it exists so the token can be copied into
    Scrypted's notifier settings or a hand-written resource URL. Every Home
    Assistant user can read it and the recorder keeps its history, which the
    README spells out. Diagnostic keeps it off auto-generated dashboards.
    """

    _attr_has_entity_name = True
    _attr_translation_key = TOKEN_KEY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, config_entry: ConfigEntry, token: str) -> None:
        """Initialize a ScryptedTokenSensor entity."""
        host = config_entry.data[CONF_HOST]
        # Not the host: reconfiguring the host must not leave a second sensor.
        self._attr_unique_id = token_sensor_unique_id(config_entry)
        self._attr_native_value = token
        self._attr_extra_state_attributes = {CONF_HOST: host}
        # The device representing the Scrypted server; __init__ registers the
        # same identifier when device entities are enabled.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            manufacturer="Scrypted",
            name=config_entry.options.get(CONF_NAME, DEFAULT_NAME),
            configuration_url=get_base_url(host),
        )


def token_sensor_unique_id(config_entry: ConfigEntry) -> str:
    """Return the token sensor's unique ID."""
    return f"{config_entry.entry_id}_{TOKEN_KEY}"


@callback
def async_migrate_token_sensor(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Move the token sensor off its old, host-based unique ID.

    Device entities always start with the entry ID; any other sensor of this
    entry is a token sensor from before 1.0.2. The one for the current host
    keeps its entity ID and history under the new unique ID. Others were left
    behind by earlier host changes and are removed.
    """
    registry = er.async_get(hass)
    new_unique_id = token_sensor_unique_id(config_entry)
    legacy = [
        entity
        for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id)
        if entity.domain == SENSOR_DOMAIN
        and not entity.unique_id.startswith(f"{config_entry.entry_id}_")
    ]
    if not legacy:
        return
    if registry.async_get_entity_id(SENSOR_DOMAIN, DOMAIN, new_unique_id) is None:
        current = next(
            (
                entity
                for entity in legacy
                if entity.unique_id == config_entry.data.get(CONF_HOST)
            ),
            legacy[0],
        )
        registry.async_update_entity(current.entity_id, new_unique_id=new_unique_id)
        legacy.remove(current)
    for entity in legacy:
        registry.async_remove(entity.entity_id)


class ScryptedSensor(ScryptedDeviceEntity, SensorEntity):
    """A numeric/enum scrypted device property."""

    entity_description: ScryptedSensorDescription

    @property
    def native_value(self):
        """Return the converted native value, or None while the property is unset."""
        value = self.raw_value
        if value is None:
            return None
        return self.entity_description.value_fn(value)
