"""Adds support for virtual thermostat units."""
import asyncio
import logging

import voluptuous as vol

from homeassistant.components.climate import PLATFORM_SCHEMA, ClimateDevice
from homeassistant.components.climate.const import (
    ATTR_PRESET_MODE, CURRENT_HVAC_COOL, CURRENT_HVAC_HEAT, CURRENT_HVAC_IDLE,
    CURRENT_HVAC_OFF, HVAC_MODE_COOL, HVAC_MODE_HEAT, HVAC_MODE_OFF,
    PRESET_AWAY, SUPPORT_PRESET_MODE, SUPPORT_TARGET_TEMPERATURE, PRESET_NONE)
from homeassistant.const import (
    ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_NAME, EVENT_HOMEASSISTANT_START,
    PRECISION_HALVES, PRECISION_TENTHS, PRECISION_WHOLE, SERVICE_TURN_OFF,
    SERVICE_TURN_ON, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN)
from homeassistant.core import DOMAIN as HA_DOMAIN, callback
from homeassistant.helpers import condition
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change, async_track_time_interval)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.temperature import display_temp as show_temp

from datetime import timedelta

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.3
DEFAULT_NAME = 'Virtual Thermostat'

CONF_HEATER = 'heater'
CONF_SENSOR = 'target_sensor'
CONF_MIN_TEMP = 'min_temp'
CONF_MAX_TEMP = 'max_temp'
CONF_TARGET_TEMP = 'target_temp'
CONF_AC_MODE = 'ac_mode'
CONF_MIN_DUR = 'min_cycle_duration'
CONF_MIN_DUR_SWITCH = 'min_cycle_switch'
CONF_COLD_TOLERANCE = 'cold_tolerance'
CONF_HOT_TOLERANCE = 'hot_tolerance'
CONF_KEEP_ALIVE = 'keep_alive'
CONF_SENSOR_TIMEOUT = 'sensor_timeout'
CONF_INITIAL_HVAC_MODE = 'initial_hvac_mode'
CONF_AWAY_TEMP = 'away_temp'
CONF_PRECISION = 'precision'
SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE

ATTR_SAVED_TEMP = "saved_temperature"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HEATER): cv.entity_id,
    vol.Required(CONF_SENSOR): cv.entity_id,
    vol.Optional(CONF_AC_MODE): cv.boolean,
    vol.Optional(CONF_MAX_TEMP): vol.Coerce(float),
    vol.Optional(CONF_MIN_DUR): vol.All(cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_MIN_DUR_SWITCH): cv.entity_id,
    vol.Optional(CONF_MIN_TEMP): vol.Coerce(float),
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_COLD_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(
        float),
    vol.Optional(CONF_HOT_TOLERANCE, default=DEFAULT_TOLERANCE): vol.Coerce(
        float),
    vol.Optional(CONF_TARGET_TEMP): vol.Coerce(float),
    vol.Optional(CONF_KEEP_ALIVE): vol.All(
        cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_SENSOR_TIMEOUT): vol.All(
        cv.time_period, cv.positive_timedelta),
    vol.Optional(CONF_INITIAL_HVAC_MODE):
        vol.In([HVAC_MODE_COOL, HVAC_MODE_HEAT, HVAC_MODE_OFF]),
    vol.Optional(CONF_AWAY_TEMP): vol.Coerce(float),
    vol.Optional(CONF_PRECISION): vol.In(
        [PRECISION_TENTHS, PRECISION_HALVES, PRECISION_WHOLE]),
})


async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up the virtual thermostat platform."""
    name = config.get(CONF_NAME)
    heater_entity_id = config.get(CONF_HEATER)
    sensor_entity_id = config.get(CONF_SENSOR)
    min_temp = config.get(CONF_MIN_TEMP)
    max_temp = config.get(CONF_MAX_TEMP)
    target_temp = config.get(CONF_TARGET_TEMP)
    ac_mode = config.get(CONF_AC_MODE)
    min_cycle_duration = config.get(CONF_MIN_DUR)
    min_cycle_entity_id = config.get(CONF_MIN_DUR_SWITCH)
    cold_tolerance = config.get(CONF_COLD_TOLERANCE)
    hot_tolerance = config.get(CONF_HOT_TOLERANCE)
    keep_alive = config.get(CONF_KEEP_ALIVE)
    sensor_timeout = config.get(CONF_SENSOR_TIMEOUT)
    initial_hvac_mode = config.get(CONF_INITIAL_HVAC_MODE)
    away_temp = config.get(CONF_AWAY_TEMP)
    precision = config.get(CONF_PRECISION)
    unit = hass.config.units.temperature_unit

    async_add_entities([VirtualThermostat(
        name, heater_entity_id, sensor_entity_id, min_temp, max_temp,
        target_temp, ac_mode, min_cycle_duration, min_cycle_entity_id, cold_tolerance,
        hot_tolerance, keep_alive, sensor_timeout, initial_hvac_mode, away_temp,
        precision, unit)])


class VirtualThermostat(ClimateDevice, RestoreEntity):
    """Representation of a Virtual Thermostat device."""

    def __init__(self, name, heater_entity_id, sensor_entity_id,
                 min_temp, max_temp, target_temp, ac_mode, min_cycle_duration, min_cycle_entity_id,
                 cold_tolerance, hot_tolerance, keep_alive, sensor_timeout,
                 initial_hvac_mode, away_temp, precision, unit):
        """Initialize the thermostat."""
        self._name = name
        self.heater_entity_id = heater_entity_id
        self.sensor_entity_id = sensor_entity_id
        self.ac_mode = ac_mode
        self.min_cycle_duration = min_cycle_duration
        self.min_cycle_entity_id = min_cycle_entity_id
        self._cold_tolerance = cold_tolerance
        self._hot_tolerance = hot_tolerance
        self._keep_alive = keep_alive
        self._sensor_timeout = sensor_timeout
        self._hvac_mode = initial_hvac_mode
        self._saved_hvac_mode = None
        self._saved_target_temp = target_temp or away_temp
        self._temp_precision = precision
        if self.ac_mode:
            self._hvac_list = [HVAC_MODE_COOL, HVAC_MODE_OFF]
        else:
            self._hvac_list = [HVAC_MODE_HEAT, HVAC_MODE_OFF]
        self._active = False
        self._cur_temp = None
        self._temp_lock = asyncio.Lock()
        self._min_temp = min_temp
        self._max_temp = max_temp
        self._target_temp = target_temp
        self._unit = unit
        self._support_flags = SUPPORT_FLAGS
        if away_temp:
            self._support_flags = SUPPORT_FLAGS | SUPPORT_PRESET_MODE
        self._away_temp = away_temp
        self._is_away = False
        self._last_sensor_update = None

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Add listener
        async_track_state_change(
            self.hass, self.sensor_entity_id, self._async_sensor_changed)
        async_track_state_change(
            self.hass, self.heater_entity_id, self._async_switch_changed)

        if self._keep_alive:
            async_track_time_interval(
                self.hass, self._async_control_heating, self._keep_alive)

        if self._sensor_timeout:
            async_track_time_interval(
                self.hass, self._async_check_for_sensor_timeout, timedelta(seconds=30))

        @callback
        def _async_startup(event):
            """Init on startup."""
            sensor_state = self.hass.states.get(self.sensor_entity_id)
            if sensor_state and sensor_state.state != STATE_UNKNOWN and sensor_state.state != STATE_UNAVAILABLE:
                self._async_update_temp(sensor_state)

# TODO need to get this next bit to work so that the switch is turned off
#  if the sensor is unavailable. Not sure how at this point
#  If sensor_timeout is used with 'retained' temperatures, this shouldn't be a major issue
            # elif not sensor_state or sensor_state.state == STATE_UNAVAILABLE:
            #     _LOGGER.warn("Something is wrong with the sensor,"
            #                  "turning the heater off")
            #     self.hass.services.call(HA_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: self.heater_entity_id})

        self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_START, _async_startup)

        # Check If we have an old state
        old_state = await self.async_get_last_state()
        if old_state is not None:
            # If we have no initial temperature, restore
            if self._target_temp is None:
                # If we have a previously saved temperature
                if old_state.attributes.get(ATTR_TEMPERATURE) is None:
                    if self.ac_mode:
                        self._target_temp = self.max_temp
                    else:
                        self._target_temp = self.min_temp
                    _LOGGER.warning("Undefined target temperature,"
                                    "falling back to %s", self._target_temp)
                else:
                    self._target_temp = float(
                        old_state.attributes[ATTR_TEMPERATURE])

            # restoring in "away" mode
            if old_state.attributes.get(ATTR_PRESET_MODE) == PRESET_AWAY:
                self._is_away = True
                self._saved_target_temp = self._target_temp
                self._target_temp = self._away_temp

            # restore previous saved temperature
            if ATTR_SAVED_TEMP in old_state.attributes:
                if old_state.attributes.get(ATTR_SAVED_TEMP) is None:
                    self._saved_target_temp = self.target_temperature
                else:
                    self._saved_target_temp = float(
                            old_state.attributes[ATTR_SAVED_TEMP])

            if not self._hvac_mode and old_state.state:
                self._hvac_mode = old_state.state
#                self._saved_hvac_mode = self._hvac_mode

        else:
            # No previous state, try and restore defaults
            if self._target_temp is None:
                if self.ac_mode:
                    self._target_temp = self.max_temp
                else:
                    self._target_temp = self.min_temp
            _LOGGER.warning("No previously saved temperature, setting to %s",
                            self._target_temp)

        # Set default state to off
        if not self._hvac_mode:
            self._hvac_mode = HVAC_MODE_OFF

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def precision(self):
        """Return the precision of the system."""
        if self._temp_precision is not None:
            return self._temp_precision
        return super().precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def current_temperature(self):
        """Return the sensor temperature."""
        return self._cur_temp

    @property
    def hvac_mode(self):
        """Return current operation."""
        return self._hvac_mode

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported.

        Need to be one of CURRENT_HVAC_*.
        """
        if self._hvac_mode == HVAC_MODE_OFF:
            return CURRENT_HVAC_OFF
        if not self._is_device_active:
            return CURRENT_HVAC_IDLE
        if self.ac_mode:
            return CURRENT_HVAC_COOL
        return CURRENT_HVAC_HEAT

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temp

    @property
    def hvac_modes(self):
        """List of available operation modes."""
        return self._hvac_list

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp."""
        if self._away_temp:
            if self._is_away:
                return PRESET_AWAY
            return PRESET_NONE
        return None

    @property
    def preset_modes(self):
        """Return a list of available preset modes."""
        if self._away_temp:
            return [PRESET_NONE, PRESET_AWAY]
        return None

    async def async_set_hvac_mode(self, hvac_mode):
        """Set hvac mode."""
        if hvac_mode == HVAC_MODE_HEAT:
            self._hvac_mode = HVAC_MODE_HEAT
            await self._async_control_heating(force=True)
        elif hvac_mode == HVAC_MODE_COOL:
            self._hvac_mode = HVAC_MODE_COOL
            await self._async_control_heating(force=True)
        elif hvac_mode == HVAC_MODE_OFF:
            self._hvac_mode = HVAC_MODE_OFF
            if self._is_device_active:
                await self._async_heater_turn_off()
        else:
            _LOGGER.error("Unrecognized hvac mode: %s", hvac_mode)
            return
        # Ensure we update the current operation after changing the mode
        self.schedule_update_ha_state()

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self._is_away:
            self._saved_target_temp = temperature
        else:
            self._target_temp = temperature
            await self._async_control_heating(force=True)

        await self.async_update_ha_state()

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp:
            return self._min_temp

        # get default temp from super class
        return super().min_temp

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp:
            return self._max_temp

        # Get default temp from super class
        return super().max_temp

    async def _async_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature changes."""
        if new_state is None:
            return

        self._async_update_temp(new_state)
        await self._async_control_heating()
        await self.async_update_ha_state()

    @callback
    def _async_switch_changed(self, entity_id, old_state, new_state):
        """Handle heater switch state changes."""
        if new_state is None:
            return
        self.async_schedule_update_ha_state()

    @property
    def available(self) -> bool:
        """Return True if sensor and heater are available."""
        return self._is_heater_available and self._is_sensor_available

    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from sensor."""
        try:
            self._cur_temp = float(state.state)
            self._last_sensor_update = state.last_changed
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    async def _async_check_for_sensor_timeout(self, time=None, force=False):
        """Check to see if the sensor has become unavailable"""
        async with self._temp_lock:
            # if the sensor hasn't sent a reading in a while, turn the heating off
            _LOGGER.debug("Checking for sensor timeout")
            if self._sensor_timeout and time is not None and self._is_device_active:
                _LOGGER.debug("Sensor timeout enabled")
                duration = (time - self._last_sensor_update).total_seconds()
                _LOGGER.debug("Duration: {} of {}".format(int(duration), self._sensor_timeout.total_seconds()))
                if duration > self._sensor_timeout.total_seconds():
                    _LOGGER.warning("No temperature update in {} seconds, turning off heater {}".format(
                        int(duration),
                        self.heater_entity_id
                    ))
                    await self._async_heater_turn_off()
                return

    async def _async_control_heating(self, time=None, force=False):
        """Check if we need to turn heating on or off."""
        async with self._temp_lock:
            if not self._active and None not in (self._cur_temp,
                                                 self._target_temp):
                self._active = True
                _LOGGER.info("Obtained current and target temperature. Virtual thermostat %s active. %s, %s",
                             self.name,
                             self._cur_temp,
                             self._target_temp
                             )

            if not self._active or self._hvac_mode == HVAC_MODE_OFF:
                return

            # if the sensor is unavailable, turn the heating off
            if not self._is_sensor_available and self._is_device_active:
                _LOGGER.warning("Sensor {} is unavailable, turning off the heater {}".format(
                    self.sensor_entity_id,
                    self.heater_entity_id
                    ))
                await self._async_heater_turn_off()
                return

            long_enough = True
            switch_entity = self.heater_entity_id

            if not force and time is None:
                # If the `force` argument is True, we
                # ignore `min_cycle_duration`.
                # If the `time` argument is not none, we were invoked for
                # keep-alive purposes, and `min_cycle_duration` is irrelevant.
                if self.min_cycle_duration:
                    if self._is_device_active:
                        current_state = STATE_ON
                    else:
                        current_state = HVAC_MODE_OFF

                    # which entity to check for the time
                    if self.min_cycle_entity_id:
                        switch_entity = self.min_cycle_entity_id
                    long_enough = condition.state(
                        self.hass,
                        switch_entity,
                        current_state,
                        self.min_cycle_duration,
                    )
#                    if not long_enough:
#                        _LOGGER.debug("Switch not in current state for long enough. Not changing.")
#                        return

            too_cold = self._target_temp - self._cur_temp >= self._cold_tolerance
            too_hot = self._cur_temp - self._target_temp >= self._hot_tolerance
            if self._is_device_active:
                if (self.ac_mode and too_cold) or (not self.ac_mode and too_hot):
                    if not long_enough:
                        _LOGGER.warning("Switch %s not on for long enough, not turning off", switch_entity)
                    else:
                        _LOGGER.info("Turning off heater %s", self.heater_entity_id)
                        await self._async_heater_turn_off()
                elif time is not None and self._keep_alive:
                    # The time argument is passed only in keep-alive case
                    await self._async_heater_turn_on()
            else:
                if (self.ac_mode and too_hot) or (not self.ac_mode and too_cold):
                    if not long_enough:
                        _LOGGER.warn("Switch %s not off for long enough, not turning on", switch_entity)
                    else:
                        _LOGGER.info("Turning on heater %s", self.heater_entity_id)
                        await self._async_heater_turn_on()
                elif time is not None and self._keep_alive:
                    # The time argument is passed only in keep-alive case
                    await self._async_heater_turn_off()

    @property
    def _is_device_active(self):
        """If the toggleable device is currently active."""
        return self.hass.states.is_state(self.heater_entity_id, STATE_ON)

    @property
    def _is_heater_available(self):
        """If the toggleable device is currently active."""
        return not self.hass.states.is_state(self.heater_entity_id, STATE_UNAVAILABLE)

    @property
    def _is_sensor_available(self):
        """If the toggleable device is currently active."""
        return not self.hass.states.is_state(self.sensor_entity_id, STATE_UNAVAILABLE)


    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    async def _async_heater_turn_on(self):
        """Turn heater toggleable device on."""
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(HA_DOMAIN, SERVICE_TURN_ON, data)

    async def _async_heater_turn_off(self):
        """Turn heater toggleable device off."""
        data = {ATTR_ENTITY_ID: self.heater_entity_id}
        await self.hass.services.async_call(HA_DOMAIN, SERVICE_TURN_OFF, data)

    async def async_set_preset_mode(self, preset_mode: str):
        """Set new preset mode.

        This method must be run in the event loop and returns a coroutine.
        """
        if preset_mode == PRESET_AWAY and not self._is_away:
            self._is_away = True
            self._saved_target_temp = self._target_temp
            self._target_temp = self._away_temp
            await self._async_control_heating(force=True)
        elif preset_mode == PRESET_NONE and self._is_away:
            self._is_away = False
            self._target_temp = self._saved_target_temp
            await self._async_control_heating(force=True)

        await self.async_update_ha_state()

    @property
    def state_attributes(self):
        """Return the state attributes."""
        data = super().state_attributes
        data[ATTR_SAVED_TEMP] = show_temp(
                self.hass, self._saved_target_temp, self.temperature_unit,
                self.precision)
        return data
