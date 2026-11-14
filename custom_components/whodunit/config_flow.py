"""Config flow for Whodunit."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_FIGHT_FLIPS,
    CONF_FIGHT_WINDOW,
    CONF_HISTORY,
    CONF_IGNORED_ENTITIES,
    CONF_NOTIFY,
    CONF_TRACKED_DOMAINS,
    DEFAULTS,
    DOMAIN,
)
from .engine import DEFAULT_TRACKED_DOMAINS

DOMAIN_CHOICES = sorted(set(DEFAULT_TRACKED_DOMAINS) | {"binary_sensor", "sensor", "input_text", "button", "scene"})


class WhodunitConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Whodunit", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WhodunitOptionsFlow()


class WhodunitOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = {**DEFAULTS, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(CONF_FIGHT_FLIPS, default=opts[CONF_FIGHT_FLIPS]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=3, max=30, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_FIGHT_WINDOW, default=opts[CONF_FIGHT_WINDOW]): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=900, step=5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_NOTIFY, default=opts[CONF_NOTIFY]): selector.BooleanSelector(),
                vol.Required(CONF_TRACKED_DOMAINS, default=opts[CONF_TRACKED_DOMAINS]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DOMAIN_CHOICES, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN
                    )
                ),
                vol.Optional(CONF_IGNORED_ENTITIES, default=opts[CONF_IGNORED_ENTITIES]): selector.EntitySelector(
                    selector.EntitySelectorConfig(multiple=True)
                ),
                vol.Required(CONF_HISTORY, default=opts[CONF_HISTORY]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=200, step=5, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
