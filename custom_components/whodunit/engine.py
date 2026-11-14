"""Whodunit causality engine.

Pure Python, no Home Assistant imports, so it can be unit tested on its own.

Home Assistant links every state change to a *context*.  An automation run
gets a fresh context whose ``parent_id`` is the context of whatever triggered
it, and everything the run does (service calls, state changes) carries the
run's context.  Recording runs, service calls and state changes as they happen
lets us walk those links backwards:

    Hallway Lights -> on
      <- automation "Motion in hallway"
      <- triggered by Hallway PIR off -> on
      <- reported by the device itself

The engine also watches controllable entities for *fights*: two or more
actors (automations, people, the device itself) flipping the same entity back
and forth inside a short window, and *loops*: a chain that leads back to an
automation already in it.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import time
from typing import Any

MAX_DEPTH = 12

DEFAULT_TRACKED_DOMAINS: tuple[str, ...] = (
    "alarm_control_panel",
    "climate",
    "cover",
    "fan",
    "humidifier",
    "input_boolean",
    "input_number",
    "input_select",
    "light",
    "lock",
    "media_player",
    "number",
    "remote",
    "select",
    "siren",
    "switch",
    "vacuum",
    "valve",
    "water_heater",
)

IGNORED_STATES = frozenset({"unavailable", "unknown"})


class _LRU(OrderedDict):
    """Insertion ordered dict that forgets its oldest keys."""

    def __init__(self, maxlen: int) -> None:
        super().__init__()
        self.maxlen = maxlen

    def put(self, key: Any, value: Any) -> None:
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.maxlen:
            self.popitem(last=False)


@dataclass(slots=True)
class Ctx:
    """What we know about one context id."""

    user_id: str | None
    parent_id: str | None


@dataclass(slots=True)
class Run:
    """An automation or script run."""

    kind: str  # "automation" | "script"
    entity_id: str
    name: str
    source: str | None
    context_id: str
    parent_id: str | None
    user_id: str | None
    at: float


@dataclass(slots=True)
class StateChange:
    """One state_changed event, reduced to what we need."""

    entity_id: str
    name: str
    old: str | None
    new: str
    at: float
    context_id: str
    parent_id: str | None = None
    user_id: str | None = None
    attr_only: bool = False
    area: str | None = None


@dataclass(slots=True)
class Command:
    """A service call and what it was aimed at."""

    context_id: str
    service: str  # "light.turn_on"
    entity_ids: frozenset[str]
    areas: frozenset[str]
    at: float
    claimed: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Link:
    """One step in a cause chain."""

    kind: str  # automation | script | trigger | user | device | source | loop | probable | unknown
    id: str | None
    name: str
    detail: str | None = None
    at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "detail": self.detail,
            "at": self.at,
        }


@dataclass(slots=True)
class Record:
    """A tracked change with its explanation."""

    change: StateChange
    links: list[Link]
    sentence: str

    @property
    def actor(self) -> Link:
        """The closest real actor; a guessed command step is skipped."""
        for link in self.links:
            if link.kind != "probable":
                return link
        return self.links[0] if self.links else Link("unknown", None, "unknown")

    def as_dict(self) -> dict[str, Any]:
        c = self.change
        return {
            "entity_id": c.entity_id,
            "name": c.name,
            "old": c.old,
            "new": c.new,
            "at": c.at,
            "attr_only": c.attr_only,
            "context_id": c.context_id,
            "links": [link.as_dict() for link in self.links],
            "sentence": self.sentence,
        }


def actor_key(link: Link) -> str:
    """Identity of an actor for fight counting."""
    if link.kind in ("automation", "script", "user"):
        return f"{link.kind}:{link.id}"
    return link.kind


@dataclass(slots=True)
class Fight:
    """Several actors flipping one entity back and forth."""

    entity_id: str
    name: str
    kind: str  # "fight" | "loop"
    started: float
    last: float
    flips: int = 0
    actors: dict[str, dict[str, Any]] = field(default_factory=dict)
    active: bool = True

    def add(self, link: Link, at: float) -> None:
        key = actor_key(link)
        entry = self.actors.setdefault(
            key, {"kind": link.kind, "id": link.id, "name": link.name, "count": 0}
        )
        entry["count"] += 1
        self.flips += 1
        self.last = at

    def summary(self) -> str:
        names = [a["name"] for a in sorted(self.actors.values(), key=lambda a: -a["count"])]
        if self.kind == "loop":
            return f"{self.name} is caught in an automation loop ({', '.join(names)})"
        if len(names) > 2:
            who = ", ".join(names[:-1]) + f" and {names[-1]}"
        else:
            who = " and ".join(names)
        return f"{who} are fighting over {self.name} ({self.flips} flips)"

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "kind": self.kind,
            "started": self.started,
            "last": self.last,
            "flips": self.flips,
            "active": self.active,
            "actors": sorted(self.actors.values(), key=lambda a: -a["count"]),
            "summary": self.summary(),
        }


_ON_SERVICES = ("turn_on", "open_cover", "unlock", "start", "media_play")
_OFF_SERVICES = ("turn_off", "close_cover", "lock", "stop", "media_pause", "media_stop")


def _consistent(service: str, new_state: str) -> bool:
    """Rule out a command whose result contradicts the new state."""
    action = service.rsplit(".", 1)[-1]
    if action in _ON_SERVICES and new_state in ("off", "closed", "locked", "idle", "paused", "docked"):
        return False
    if action in _OFF_SERVICES and new_state in ("on", "open", "unlocked", "playing", "cleaning"):
        return False
    return True


def _default_time(at: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(at))


def _q(name: str) -> str:
    return f"“{name}”"


def _describe_change(old: str | None, new: str, attr_only: bool) -> str:
    if attr_only:
        return f"updated ({new})"
    if old is None:
        return f"became {new}"
    return f"{old} → {new}"


def build_sentence(
    change: StateChange,
    links: list[Link],
    fmt_time: Callable[[float], str] = _default_time,
) -> str:
    """Plain English explanation, suitable for a notification or TTS."""
    if change.attr_only:
        what = f"{change.name} was updated"
    elif change.old is None:
        what = f"{change.name} became {change.new}"
    else:
        what = f"{change.name} turned {change.new}"
    if links and links[0].kind == "probable":
        guess = links[0]
        delay = max(0.0, change.at - (guess.at or change.at))
        head = f"{what} at {fmt_time(change.at)}, {guess.detail} because of {guess.name} {delay:.0f}s earlier"
        return head + "".join(f", {p}" for p in _phrases(links[1:], "probable")) + "."
    parts = _phrases(links, None)
    if not parts:
        return f"{what} at {fmt_time(change.at)}."
    return f"{what} at {fmt_time(change.at)} because " + ", ".join(parts) + "."


def _phrases(links: list[Link], before: str | None) -> list[str]:
    parts: list[str] = []
    for i, link in enumerate(links):
        prev = links[i - 1].kind if i else before
        if link.kind in ("automation", "script"):
            if prev == "probable":
                phrase = f"from {link.kind} {_q(link.name)}"
            elif prev in ("automation", "script"):
                phrase = f"called from {link.kind} {_q(link.name)}"
            elif prev == "trigger":
                phrase = f"which {link.kind} {_q(link.name)} did"
            else:
                phrase = f"{link.kind} {_q(link.name)} ran"
        elif link.kind == "trigger":
            phrase = f"triggered by {link.name} changing {link.detail}"
        elif link.kind == "user":
            if prev == "probable":
                phrase = f"sent by {link.name}"
            elif prev is None:
                phrase = f"{link.name} did it"
            elif prev == "trigger":
                phrase = f"which {link.name} did"
            else:
                phrase = f"started by {link.name}"
        elif link.kind == "device":
            if i == 0:
                phrase = "the device or its integration reported it directly"
            else:
                phrase = "which the device reported itself"
        elif link.kind == "source":
            phrase = f"on a {link.name} trigger"
        elif link.kind == "loop":
            phrase = f"which loops back to {_q(link.name)}"
        else:
            phrase = "for a reason Whodunit did not see"
        parts.append(phrase)
    return parts


class Engine:
    """Records events and explains state changes."""

    def __init__(
        self,
        *,
        tracked_domains: Iterable[str] = DEFAULT_TRACKED_DOMAINS,
        ignored_entities: Iterable[str] = (),
        fight_window: float = 60.0,
        fight_flips: int = 4,
        guess_window: float = 15.0,
        history_per_entity: int = 25,
        feed_size: int = 200,
        context_cache: int = 50_000,
        user_name: Callable[[str], str] | None = None,
        fmt_time: Callable[[float], str] = _default_time,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.tracked_domains = frozenset(tracked_domains)
        self.ignored_entities = frozenset(ignored_entities)
        self.fight_window = fight_window
        self.fight_flips = fight_flips
        self.guess_window = guess_window
        self.history_per_entity = history_per_entity
        self._user_name = user_name or (lambda uid: "a user")
        self.fmt_time = fmt_time
        self._clock = clock

        self._contexts: _LRU = _LRU(context_cache)
        self._runs: _LRU = _LRU(context_cache // 4)  # context_id -> list[Run]
        self._changes: _LRU = _LRU(context_cache // 2)  # context_id -> StateChange

        self._history: dict[str, deque[Record]] = {}
        self._flips: dict[str, deque[tuple[float, Link]]] = {}
        self.feed: deque[Record] = deque(maxlen=feed_size)

        self.active_fights: dict[str, Fight] = {}
        self.recent_fights: deque[Fight] = deque(maxlen=50)
        self.fights_started = 0

        self._run_times: dict[str, deque[float]] = {}
        self._run_names: dict[str, str] = {}
        self._run_peak: dict[str, int] = {}

        self._cmd_by_entity: dict[str, Command] = {}
        self._cmd_by_area: dict[str, Command] = {}

        self._listeners: list[Callable[[str, Any], None]] = []

    # ------------------------------------------------------------------ io

    def add_listener(self, cb: Callable[[str, Any], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return remove

    def _emit(self, kind: str, payload: Any) -> None:
        for cb in list(self._listeners):
            cb(kind, payload)

    def is_tracked(self, entity_id: str) -> bool:
        if entity_id in self.ignored_entities:
            return False
        return entity_id.split(".", 1)[0] in self.tracked_domains

    # ------------------------------------------------------------- record

    def record_context(
        self, context_id: str, user_id: str | None, parent_id: str | None
    ) -> None:
        known = self._contexts.get(context_id)
        if known is None:
            self._contexts.put(context_id, Ctx(user_id, parent_id))
        else:
            known.user_id = known.user_id or user_id
            known.parent_id = known.parent_id or parent_id

    def record_run(self, run: Run) -> None:
        self.record_context(run.context_id, run.user_id, run.parent_id)
        runs = self._runs.get(run.context_id)
        if runs is None:
            self._runs.put(run.context_id, [run])
        else:
            runs.append(run)
        if run.kind == "automation":
            times = self._run_times.setdefault(run.entity_id, deque(maxlen=5000))
            times.append(run.at)
            self._run_names[run.entity_id] = run.name
            burst = sum(1 for t in times if run.at - t < 1.0)
            if burst > self._run_peak.get(run.entity_id, 0):
                self._run_peak[run.entity_id] = burst

    def record_command(self, cmd: Command) -> None:
        """Remember a service call's targets, for devices that report back late."""
        self.record_context(cmd.context_id, None, None)
        for entity_id in cmd.entity_ids:
            self._cmd_by_entity[entity_id] = cmd
        for area in cmd.areas:
            self._cmd_by_area[area] = cmd
        if len(self._cmd_by_entity) > 20_000:
            self._prune_commands(cmd.at)

    def _prune_commands(self, now: float) -> None:
        for store in (self._cmd_by_entity, self._cmd_by_area):
            for key in [k for k, c in store.items() if now - c.at > self.guess_window]:
                del store[key]

    def _guess(self, change: StateChange, claim: bool) -> tuple[Command, str] | None:
        """A recent command that likely caused a change the device reported on its own."""
        if self.guess_window <= 0 or change.attr_only:
            return None
        for cmd, confidence in (
            (self._cmd_by_entity.get(change.entity_id), "probably"),
            (self._cmd_by_area.get(change.area) if change.area else None, "possibly"),
        ):
            if cmd is None or cmd.context_id == change.context_id:
                continue
            if not 0 <= change.at - cmd.at <= self.guess_window:
                continue
            if change.entity_id in cmd.claimed:
                continue  # one command explains one report per entity
            if not _consistent(cmd.service, change.new):
                continue
            if claim:
                cmd.claimed.add(change.entity_id)
            return cmd, confidence
        return None

    def record_change(self, change: StateChange) -> Record | None:
        """Record a state change. Returns a Record if the entity is tracked."""
        self.record_context(change.context_id, change.user_id, change.parent_id)
        if not change.attr_only:
            self._changes.put(change.context_id, change)
        if not self.is_tracked(change.entity_id):
            return None
        links = self.explain(change, claim=True)
        record = Record(change, links, build_sentence(change, links, self.fmt_time))
        hist = self._history.get(change.entity_id)
        if hist is None:
            hist = self._history[change.entity_id] = deque(maxlen=self.history_per_entity)
        hist.append(record)
        self.feed.append(record)
        self._emit("change", record)
        if not change.attr_only:
            self._check_fight(record)
        return record

    # ------------------------------------------------------------ explain

    def explain(self, change: StateChange, claim: bool = False) -> list[Link]:
        links: list[Link] = []
        guessed = False
        seen_ctx: set[str] = set()
        seen_runs: set[str] = set()
        cur: str | None = change.context_id
        cur_user = change.user_id
        cur_parent = change.parent_id
        origin = (change.entity_id, change.name)

        for _ in range(MAX_DEPTH):
            if cur is None:
                break
            if cur in seen_ctx:
                break
            seen_ctx.add(cur)
            runs: list[Run] | None = self._runs.get(cur)
            if runs:
                looped = False
                # newest first: a script called from an automation shares its context
                for run in reversed(runs):
                    if run.entity_id in seen_runs:
                        links.append(Link("loop", run.entity_id, run.name, at=run.at))
                        looped = True
                        break
                    seen_runs.add(run.entity_id)
                    links.append(Link(run.kind, run.entity_id, run.name, run.source, run.at))
                if looped:
                    break
                first = runs[0]
                user = first.user_id or cur_user
                if user:
                    links.append(Link("user", user, self._user_name(user)))
                    break
                parent = first.parent_id or cur_parent
                if not parent:
                    if first.source:
                        links.append(Link("source", None, first.source))
                    break
                trig: StateChange | None = self._changes.get(parent)
                pctx: Ctx | None = self._contexts.get(parent)
                if trig is None and pctx is None and parent not in self._runs:
                    links.append(Link("source", None, first.source or "unknown"))
                    break
                if trig is not None:
                    links.append(
                        Link(
                            "trigger",
                            trig.entity_id,
                            trig.name,
                            _describe_change(trig.old, trig.new, trig.attr_only),
                            trig.at,
                        )
                    )
                    origin = (trig.entity_id, trig.name)
                cur = parent
                cur_user = pctx.user_id if pctx else None
                cur_parent = pctx.parent_id if pctx else None
                continue

            if cur_user:
                links.append(Link("user", cur_user, self._user_name(cur_user)))
                break
            if cur_parent:
                cur = cur_parent
                pctx = self._contexts.get(cur)
                cur_user = pctx.user_id if pctx else None
                cur_parent = pctx.parent_id if pctx else None
                continue
            if guessed and links[-1].kind == "probable":
                break  # the command came from outside what HA can see (an add-on or API client)
            if not links:
                guess = self._guess(change, claim)
                if guess is not None:
                    cmd, confidence = guess
                    links.append(Link("probable", cmd.context_id, cmd.service, confidence, cmd.at))
                    guessed = True
                    cur = cmd.context_id
                    pctx = self._contexts.get(cur)
                    cur_user = pctx.user_id if pctx else None
                    cur_parent = pctx.parent_id if pctx else None
                    continue
            links.append(Link("device", origin[0], origin[1]))
            break
        return links

    def history(self, entity_id: str, limit: int = 10) -> list[Record]:
        hist = self._history.get(entity_id)
        if not hist:
            return []
        return list(hist)[-limit:][::-1]

    def recent(self, limit: int = 50) -> list[Record]:
        return list(self.feed)[-limit:][::-1]

    # ------------------------------------------------------------- fights

    def _check_fight(self, record: Record) -> None:
        change = record.change
        if change.new in IGNORED_STATES or change.old in IGNORED_STATES:
            return
        actor = record.actor
        now = change.at
        flips = self._flips.get(change.entity_id)
        if flips is None:
            flips = self._flips[change.entity_id] = deque(maxlen=200)
        flips.append((now, actor))
        while flips and now - flips[0][0] > self.fight_window:
            flips.popleft()

        fight = self.active_fights.get(change.entity_id)
        if fight is not None:
            fight.add(actor, now)
            if any(link.kind == "loop" for link in record.links):
                fight.kind = "loop"
            self._emit("fight_update", fight)
            return

        looped = any(link.kind == "loop" for link in record.links)
        distinct = {actor_key(a) for _, a in flips}
        if looped or (len(flips) >= self.fight_flips and len(distinct) >= 2):
            fight = Fight(
                entity_id=change.entity_id,
                name=change.name,
                kind="loop" if looped else "fight",
                started=flips[0][0],
                last=now,
            )
            for at, a in flips:
                fight.add(a, at)
            self.active_fights[change.entity_id] = fight
            self.fights_started += 1
            self._emit("fight_started", fight)

    def tick(self, now: float | None = None) -> list[Fight]:
        """End fights that went quiet. Returns the fights that ended."""
        now = self._clock() if now is None else now
        ended = [f for f in self.active_fights.values() if now - f.last > self.fight_window]
        for fight in ended:
            fight.active = False
            del self.active_fights[fight.entity_id]
            self.recent_fights.append(fight)
            self._emit("fight_ended", fight)
        return ended

    # --------------------------------------------------------------- busy

    def busiest(self, limit: int = 10, now: float | None = None) -> list[dict[str, Any]]:
        """Automations ranked by runs in the last hour."""
        now = self._clock() if now is None else now
        out = []
        for entity_id, times in list(self._run_times.items()):
            while times and now - times[0] > 3600:
                times.popleft()
            if not times:
                del self._run_times[entity_id]
                self._run_peak.pop(entity_id, None)
                continue
            out.append(
                {
                    "entity_id": entity_id,
                    "name": self._run_names.get(entity_id, entity_id),
                    "runs_last_hour": len(times),
                    "peak_per_second": self._run_peak.get(entity_id, 1),
                }
            )
        out.sort(key=lambda r: (-r["runs_last_hour"], -r["peak_per_second"]))
        return out[:limit]
