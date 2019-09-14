A fork of Home Assistant's [generic thermostat](https://www.home-assistant.io/components/generic_thermostat/) to deal 
with some of the issues in that component, specifically:

1. Setting the temperature when the thermostat is in "away" mode overrides the current desired temperature. 
If you use automations to create a heating/cooling schedule, you can't use "away" to set a fixed temperature, 
e.g. when you go on hoiday. With **Virtual Thermostat**, setting the temperature when set to "away" updates the
stored temperature, which is reverted to when "away" mode is cancelled.
2. Further to (1), with **Virtual Thermostat** the saved temperature is persisted and reverted in the event of Home 
Asssistant being restarted while in "away" mode. No more reverting to the set `min_temp` or `max_temp`
3. With **Generic Thermostat**, if the associated sensor goes offline or stops updating, the linked switch will stay on 
indefinitely. With **Virtual Thermostat**, if the sensor becomes `unavailable`, the switch will be turned off. There is
also an option to set a timeout, after which the switch will be turned off if the sensor has not sent an update in that 
time. Note: if the switch is on and the sensor unavailable when Home Assistant starts, this will not turn the switch 
off. I'm working on that, but in the meantime, I suggest either using `retained` temperatures and the timeout (if using 
MQTT sensors), or a startup automation to turn off the switches.

Tested in Home Assistant 0.98.5. It _should_ work in 0.96+, as it's based on the `Climate 1.0` **Generic Thermostat**.

See the [wiki](https://github.com/b4dpxl/virtual_thermostat/wiki) for configuration details. It's basically the same as
**Generic Thermostat** with one addition (`sensor_timeout`) though.  
