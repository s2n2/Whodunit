"""Websocket commands used by the Whodunit card."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


def _wd(hass: HomeAssistant):
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    return entries[0].runtime_data if entries else None


def _not_ready(connection, msg) -> None:
    connection.send_error(msg["id"], "not_loaded", "Whodunit is not set up")


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_explain)
    websocket_api.async_register_command(hass, ws_recent)
    websocket_api.async_register_command(hass, ws_fights)
    websocket_api.async_register_command(hass, ws_busy)
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/explain", vol.Required("entity_id"): str, vol.Optional("limit", default=10): int}
)
@callback
def ws_explain(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (wd := _wd(hass)) is None:
        return _not_ready(connection, msg)
    connection.send_result(msg["id"], wd.explain(msg["entity_id"], msg["limit"]))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/recent", vol.Optional("limit", default=50): int})
@callback
def ws_recent(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (wd := _wd(hass)) is None:
        return _not_ready(connection, msg)
    connection.send_result(msg["id"], {"changes": [r.as_dict() for r in wd.engine.recent(msg["limit"])]})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/fights"})
@callback
def ws_fights(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (wd := _wd(hass)) is None:
        return _not_ready(connection, msg)
    eng = wd.engine
    connection.send_result(
        msg["id"],
        {
            "active": [f.as_dict() for f in eng.active_fights.values()],
            "recent": [f.as_dict() for f in reversed(eng.recent_fights)],
            "window": eng.fight_window,
            "flips": eng.fight_flips,
        },
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/busy", vol.Optional("limit", default=15): int})
@callback
def ws_busy(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if (wd := _wd(hass)) is None:
        return _not_ready(connection, msg)
    connection.send_result(msg["id"], {"automations": wd.engine.busiest(msg["limit"])})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Push every tracked change and fight event to the card as it happens."""
    if (wd := _wd(hass)) is None:
        return _not_ready(connection, msg)

    @callback
    def forward(kind: str, payload: Any) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"kind": kind, "data": payload.as_dict()}))

    connection.subscriptions[msg["id"]] = wd.engine.add_listener(forward)
    connection.send_result(msg["id"])
