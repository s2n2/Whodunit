"""Whodunit: find out why anything in Home Assistant changed."""

from __future__ import annotations

from datetime import timedelta
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, persistent_notification
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_NAME,
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
    Platform,
)
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    CARD_URL,
    CONF_FIGHT_FLIPS,
    CONF_FIGHT_WINDOW,
    CONF_GUESS_WINDOW,
    CONF_HISTORY,
    CONF_IGNORED_ENTITIES,
    CONF_NOTIFY,
    CONF_TRACKED_DOMAINS,
    DEFAULTS,
    DOMAIN,
    EVENT_FIGHT_ENDED,
    EVENT_FIGHT_STARTED,
    SIGNAL_UPDATE,
    VERSION,
)
from .engine import Command, Engine, Fight, Run, StateChange, build_sentence
from . import websocket_api

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

EVENT_AUTOMATION_TRIGGERED = "automation_triggered"
EVENT_SCRIPT_STARTED = "script_started"

# Service calls that never change a device themselves, or that would make misleading guesses
SKIP_DOMAINS = frozenset(
    {"automation", "script", "logbook", "system_log", "persistent_notification", "notify", "tts",
     "recorder", "logger", "frontend", "conversation", "counter", "timer", "whodunit"}
)
SKIP_SERVICES = frozenset(
    {"homeassistant.update_entity", "homeassistant.reload_config_entry", "homeassistant.reload_all",
     "homeassistant.check_config", "homeassistant.restart", "homeassistant.stop"}
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",")] if "," in value else [value]
    return [v for v in value if isinstance(v, str)]

type WhodunitConfigEntry = ConfigEntry[Whodunit]


def _opt(entry: ConfigEntry, key: str) -> Any:
    return entry.options.get(key, DEFAULTS[key])


class Whodunit:
    """Runtime object: wires the engine to the event bus."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.notify = bool(_opt(entry, CONF_NOTIFY))
        self._user_names: dict[str, str] = {}
        self._pending_users: set[str] = set()
        self.engine = Engine(
            tracked_domains=_opt(entry, CONF_TRACKED_DOMAINS),
            ignored_entities=_opt(entry, CONF_IGNORED_ENTITIES),
            fight_window=float(_opt(entry, CONF_FIGHT_WINDOW)),
            fight_flips=int(_opt(entry, CONF_FIGHT_FLIPS)),
            history_per_entity=int(_opt(entry, CONF_HISTORY)),
            guess_window=float(_opt(entry, CONF_GUESS_WINDOW)),
            user_name=self.user_name,
            fmt_time=lambda at: dt_util.as_local(dt_util.utc_from_timestamp(at)).strftime("%H:%M:%S"),
        )
        self.fights_today = 0
        self._unsubs: list = []
        self._er = er.async_get(hass)
        self._dr = dr.async_get(hass)
        self._area_cache: dict[str, str | None] = {}

    # ------------------------------------------------------------- users

    async def async_load_users(self) -> None:
        for user in await self.hass.auth.async_get_users():
            if user.name:
                self._user_names[user.id] = user.name
        # A person's name is friendlier than the login name
        for state in self.hass.states.async_all("person"):
            if uid := state.attributes.get("user_id"):
                self._user_names[uid] = state.name

    def user_name(self, user_id: str) -> str:
        if name := self._user_names.get(user_id):
            return name
        if user_id not in self._pending_users:
            self._pending_users.add(user_id)
            self.hass.async_create_task(self._async_resolve_user(user_id))
        return "a user"

    async def _async_resolve_user(self, user_id: str) -> None:
        user = await self.hass.auth.async_get_user(user_id)
        if user and user.name:
            self._user_names[user_id] = user.name

    # ------------------------------------------------------------ areas

    def _area_of(self, entity_id: str) -> str | None:
        try:
            return self._area_cache[entity_id]
        except KeyError:
            pass
        area = None
        if (ent := self._er.async_get(entity_id)) is not None:
            area = ent.area_id
            if area is None and ent.device_id and (dev := self._dr.async_get(ent.device_id)):
                area = dev.area_id
        self._area_cache[entity_id] = area
        return area

    @callback
    def _on_registry_updated(self, _event: Event) -> None:
        self._area_cache.clear()

    # ------------------------------------------------------------ events

    @callback
    def _on_state_changed(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None:
            return
        old = event.data.get("old_state")
        ctx = new.context
        attr_only = old is not None and old.state == new.state
        if attr_only and not (ctx.parent_id or ctx.user_id):
            # attribute churn the integration produced on its own (media position etc.)
            # still matters for linking triggers, but not worth a history entry
            self.engine.record_context(ctx.id, ctx.user_id, ctx.parent_id)
            return
        self.engine.record_change(
            StateChange(
                entity_id=new.entity_id,
                name=new.name,
                old=old.state if old is not None else None,
                new=new.state,
                at=new.last_updated_timestamp,
                context_id=ctx.id,
                parent_id=ctx.parent_id,
                user_id=ctx.user_id,
                attr_only=attr_only,
                area=self._area_of(new.entity_id) if self.engine.is_tracked(new.entity_id) else None,
            )
        )

    def _run(self, kind: str, event: Event) -> None:
        ctx = event.context
        self.engine.record_run(
            Run(
                kind=kind,
                entity_id=event.data.get(ATTR_ENTITY_ID, f"{kind}.unknown"),
                name=event.data.get(ATTR_NAME) or event.data.get(ATTR_ENTITY_ID, kind),
                source=event.data.get("source"),
                context_id=ctx.id,
                parent_id=ctx.parent_id,
                user_id=ctx.user_id,
                at=event.time_fired_timestamp,
            )
        )

    @callback
    def _on_automation(self, event: Event) -> None:
        self._run("automation", event)

    @callback
    def _on_script(self, event: Event) -> None:
        self._run("script", event)

    @callback
    def _on_service(self, event: Event) -> None:
        ctx = event.context
        self.engine.record_context(ctx.id, ctx.user_id, ctx.parent_id)
        if self.engine.guess_window <= 0:
            return
        data = event.data
        domain, service = data.get("domain"), data.get("service")
        if not domain or domain in SKIP_DOMAINS or f"{domain}.{service}" in SKIP_SERVICES:
            return
        sd = data.get("service_data") or {}
        entities = {e for e in _as_list(sd.get("entity_id")) if "." in e}
        areas = set(_as_list(sd.get("area_id")))
        for device_id in _as_list(sd.get("device_id")):
            entities.update(ent.entity_id for ent in er.async_entries_for_device(self._er, device_id))
            if (dev := self._dr.async_get(device_id)) and dev.area_id:
                areas.add(dev.area_id)
        for entity_id in entities:
            if area := self._area_of(entity_id):
                areas.add(area)
        if entities or areas:
            self.engine.record_command(
                Command(ctx.id, f"{domain}.{service}", frozenset(entities), frozenset(areas),
                        event.time_fired_timestamp)
            )

    @callback
    def _on_engine(self, kind: str, payload: Any) -> None:
        if kind == "fight_started":
            self.fights_today += 1
            self._fight_started(payload)
        elif kind == "fight_ended":
            self.hass.bus.async_fire(EVENT_FIGHT_ENDED, payload.as_dict())
            persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_{payload.entity_id}")
        if kind != "change":
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def _fight_started(self, fight: Fight) -> None:
        data = fight.as_dict()
        _LOGGER.warning("Whodunit: %s", fight.summary())
        self.hass.bus.async_fire(EVENT_FIGHT_STARTED, data)
        if not self.notify:
            return
        lines = "\n".join(f"- **{a['name']}** ({a['kind']}): {a['count']}×" for a in data["actors"])
        title = "Automation loop" if fight.kind == "loop" else "Automation fight"
        persistent_notification.async_create(
            self.hass,
            f"{fight.summary()}.\n\n{lines}\n\nOpen the Whodunit card to see each change and its cause.",
            title=f"Whodunit: {title}",
            notification_id=f"{DOMAIN}_{fight.entity_id}",
        )

    @callback
    def _tick(self, _now: Any) -> None:
        self.engine.tick()

    @callback
    def _refresh(self, _now: Any) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    @callback
    def _midnight(self, now: Any) -> None:
        self.fights_today = 0
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def async_start(self) -> None:
        await self.async_load_users()
        bus = self.hass.bus
        self._unsubs += [
            bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed),
            bus.async_listen(EVENT_AUTOMATION_TRIGGERED, self._on_automation),
            bus.async_listen(EVENT_SCRIPT_STARTED, self._on_script),
            bus.async_listen(EVENT_CALL_SERVICE, self._on_service),
            bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._on_registry_updated),
            bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, self._on_registry_updated),
            self.engine.add_listener(self._on_engine),
            async_track_time_interval(self.hass, self._tick, timedelta(seconds=10)),
            # the busiest automation sensor drifts as the hour rolls on
            async_track_time_interval(self.hass, self._refresh, timedelta(minutes=1)),
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=0),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    # ---------------------------------------------------------- queries

    def explain(self, entity_id: str, limit: int = 10) -> dict[str, Any]:
        records = self.engine.history(entity_id, limit)
        state = self.hass.states.get(entity_id)
        out: dict[str, Any] = {
            "entity_id": entity_id,
            "name": state.name if state else entity_id,
            "state": state.state if state else None,
            "tracked": self.engine.is_tracked(entity_id),
            "changes": [r.as_dict() for r in records],
        }
        if records:
            out["sentence"] = records[0].sentence
        elif state is not None:
            # Nothing recorded since startup (or not tracked): explain the current state
            ctx = state.context
            change = StateChange(
                entity_id, state.name, None, state.state, state.last_changed_timestamp,
                ctx.id, ctx.parent_id, ctx.user_id,
            )
            links = self.engine.explain(change)
            out["sentence"] = build_sentence(change, links, self.engine.fmt_time)
            out["current"] = {"links": [link.as_dict() for link in links], "context_id": ctx.id}
        else:
            out["sentence"] = f"{entity_id} does not exist."
        return out


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the card and websocket commands once."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(Path(__file__).parent / "frontend" / "whodunit-card.js"), False)]
    )
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    websocket_api.async_register(hass)

    async def explain_service(call: ServiceCall) -> ServiceResponse:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return {"error": "Whodunit is not set up"}
        wd: Whodunit = entries[0].runtime_data
        ids = call.data[ATTR_ENTITY_ID]
        limit = call.data["limit"]
        results = {eid: wd.explain(eid, limit) for eid in ids}
        return {
            "results": results,
            "sentence": " ".join(r["sentence"] for r in results.values()),
        }

    hass.services.async_register(
        DOMAIN,
        "explain",
        explain_service,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
                vol.Optional("limit", default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WhodunitConfigEntry) -> bool:
    wd = Whodunit(hass, entry)
    await wd.async_start()
    entry.runtime_data = wd
    entry.async_on_unload(wd.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WhodunitConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
