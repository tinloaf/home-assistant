"""
Support for MQTT climate devices.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/climate.mqtt/
"""

import asyncio
import logging
import re

import voluptuous as vol

from homeassistant.core import callback
from homeassistant.components.climate import (
    STATE_HEAT, STATE_COOL, STATE_IDLE, ClimateDevice, PLATFORM_SCHEMA,
    STATE_AUTO)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT, STATE_ON, STATE_OFF, ATTR_TEMPERATURE,
    CONF_NAME, TEMP_CELSIUS, TEMP_FAHRENHEIT)
import homeassistant.components.mqtt as mqtt
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ['mqtt']

DEFAULT_NAME = "MQTT Climate Device"

CONF_TEMPERATURE_TOPIC = "temperature_topic"
CONF_CURRENT_TEMPERATURE_TOPIC = "current_temperature_topic"
CONF_MAX_TEMPERATURE_TOPIC = "max_temperature_topic"
CONF_MIN_TEMPERATURE_TOPIC = "min_temperature_topic"
CONF_AWAY_MODE_TOPIC = "away_mode_topic"
CONF_OPERATION_TOPIC = "operation_topic"
CONF_OPERATION_LIST = "operation_modes"
CONF_DEFAULT_OPERATION = "default_operation"

# TODO they should be defined elsewhere?
CONF_RETAIN = "retain"
CONF_QOS = "qos"

# TODO make configurable
ON_PAYLOAD = "On"
OFF_PAYLOAD = "Off"

PLATFORM_SCHEMA = mqtt.MQTT_BASE_PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_TEMPERATURE_TOPIC): mqtt.valid_publish_topic,
    vol.Optional(CONF_CURRENT_TEMPERATURE_TOPIC): mqtt.valid_subscribe_topic,
    vol.Optional(CONF_MAX_TEMPERATURE_TOPIC): mqtt.valid_publish_topic,
    vol.Optional(CONF_MIN_TEMPERATURE_TOPIC): mqtt.valid_publish_topic,
    vol.Optional(CONF_AWAY_MODE_TOPIC): mqtt.valid_publish_topic,
    vol.Optional(CONF_OPERATION_TOPIC): mqtt.valid_publish_topic,
    vol.Optional(CONF_OPERATION_LIST): [cv.string],
    vol.Optional(CONF_DEFAULT_OPERATION, default="auto"): cv.string,
    vol.Optional(CONF_RETAIN): cv.boolean,
})


# TODO this should probably live somewhere else
temperature_re = re.compile(r'(?P<number>\d*(\.\d*)?) ?°?(?P<unit>[FC])')
def parse_temperature(temp_str):
    match = temperature_re.match(temp_str.strip())
    if match is None:
        return None

    if match.group('unit') == 'F':
        unit = TEMP_FAHRENHEIT
    elif match.group('unit') == 'C':
        unit = TEMP_CELSIUS
    else:
        _LOGGER.warn("Error in parsing temperature unit")
        return None

    return (float(match.group('number')), unit)

def fahrenheit_to_celsius(val):
    return (val-32) * 5 / 9
def celsius_to_fahrenheit(val):
    retun (val * 9 / 5) + 32

@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the mqtt climate device platform."""
    name = config.get(CONF_NAME)
    qos = config.get(CONF_QOS)

    temperature_topic = config.get(CONF_TEMPERATURE_TOPIC)
    current_temperature_topic = config.get(CONF_CURRENT_TEMPERATURE_TOPIC)
    max_temperature_topic = config.get(CONF_MAX_TEMPERATURE_TOPIC)
    min_temperature_topic = config.get(CONF_MIN_TEMPERATURE_TOPIC)
    away_mode_topic = config.get(CONF_AWAY_MODE_TOPIC)
    operation_topic = config.get(CONF_OPERATION_TOPIC)

    operation_list = config.get(CONF_OPERATION_LIST)
    default_op = config.get(CONF_DEFAULT_OPERATION)
    retain = config.get(CONF_RETAIN)

    async_add_devices([MQTTClimate(
        hass, name, qos, temperature_topic, current_temperature_topic,
        max_temperature_topic, min_temperature_topic, away_mode_topic,
        operation_topic, operation_list, default_op, retain)])


class MQTTClimate(ClimateDevice):
    """Representation of a mqtt climate device."""

    def __init__(self, hass, name, qos, temperature_topic, current_temperature_topic,
                 max_temperature_topic, min_temperature_topic, away_mode_topic,
                 operation_topic, operation_list, default_op, retain):
        """Initialize the climate device."""
        self.hass = hass

        self._name = name
        self._qos = qos
        self._retain = retain

        self._temperature_topic = temperature_topic
        self._current_temperature_topic = current_temperature_topic
        self._max_temperature_topic = max_temperature_topic
        self._min_temperature_topic = min_temperature_topic
        self._away_mode_topic = away_mode_topic
        self._operation_topic = operation_topic

        self._operation_list = operation_list
        self._operation = default_op

        self._unit = hass.config.units.temperature_unit

        self._temperature = None
        self._cur_temp = None
        self._max_temp = None
        self._min_temp = None
        self._away = False

    @asyncio.coroutine
    def async_added_to_hass(self):
        """Subscribe to MQTT events.

        This method is a coroutine.
        """
        @callback
        def current_temperature_received(topic, payload, qos):
            """Handle MQTT message updating the current temperature"""
            (parsed_temp, parsed_unit) = parse_temperature(payload)
            if parsed_unit == self._unit:
                val = parsed_temp
            elif parsed_unit == TEMP_FAHRENHEIT and self._unit == TEMP_CELSIUS:
                val = fahrenheit_to_celsius(parsed_temp)
            elif parsed_unit == TEMP_CELSIUS and self._unit == TEMP_FAHRENHEIT:
                val = fahrenheit_to_celsius(parsed_temp)
            else:
                _LOGGER.warn("Cannot convert from " + str(parsed_unit) + " to " + str(self._unit))
                return

            self._cur_temp = val

            self.hass.async_add_job(self.async_update_ha_state())


        if self._current_temperature_topic is not None:
            yield from mqtt.async_subscribe(
                self.hass, self._current_temperature_topic,
                current_temperature_received, self._qos)


    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._cur_temp

    @property
    def current_operation(self):
        """Return current operation ie. off, heat, idle."""
        return self._operation

    @property
    def operation_list(self):
        """List of available operation modes."""
        return self._operation_list

    def set_operation_mode(self, operation_mode):
        """Set operation mode."""
        if operation_mode not in self._operation_list:
            _LOGGER.error("Unrecognized operation mode: %s", operation_mode)
            return

        self._operation = operation_mode

        if self._operation_topic is not None:
            mqtt.publish(self.hass, self._operation_topic, self._operation,
                         qos=self._qos, retain=self._retain)

        self.schedule_update_ha_state()

    @asyncio.coroutine
    def async_set_temperature(self, **kwargs):
        print("CALLED")
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            print("NO TEMP")
            return

        print("Temperature: " + str(temperature))
        self._target_temp = temperature

        payload = "{} {}".format(temperature, self._unit)

        mqtt.async_publish(
            self.hass, self._temperature_topic, payload, self._qos,
            self._retain)

        yield from self.async_update_ha_state()


    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp is not None:
            return self._min_temp

        # get default temp from super class
        return ClimateDevice.min_temp.fget(self)

    @min_temp.setter
    def min_temp(self, val):
        self._min_temp = val

        if self._min_temperature_topic is not None:
            payload = "{} {}".format(self._min_temp, self._unit)
            mqtt.publish(self.hass, self._min_temperature_topic, payload,
                         qos=self._qos, retain=self._retain)

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp is not None:
            return self._max_temp

        # Get default temp from super class
        return ClimateDevice.max_temp.fget(self)

    @max_temp.setter
    def max_temp(self, val):
        self._max_temp = val

        if self._max_temperature_topic is not None:
            payload = "{} {}".format(self._max_temp, self._unit)
            mqtt.publish(self.hass, self._min_temperature_topic, payload,
                         qos=self._qos, retain=self._retain)


    @property
    def is_away_mode_on(self):
        return self._away

    def turn_away_mode_off(self):
        self._away = False

        if self._away_mode_topic is not None:
            mqtt.publish(self.hass, self._away_mode_topic, OFF_PAYLOAD,
                         qos=self._qos, retain=self._retain)

    def turn_away_mode_on(self):
        self._away = True

        if self._away_mode_topic is not None:
            mqtt.publish(self.hass, self._away_mode_topic, ON_PAYLOAD,
                         qos=self._qos, retain=self._retain)
