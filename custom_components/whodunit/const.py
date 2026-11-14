"""Constants for Whodunit."""

from .engine import DEFAULT_TRACKED_DOMAINS

DOMAIN = "whodunit"
VERSION = "0.2.0"

CONF_FIGHT_WINDOW = "fight_window"
CONF_FIGHT_FLIPS = "fight_flips"
CONF_NOTIFY = "notify"
CONF_TRACKED_DOMAINS = "tracked_domains"
CONF_IGNORED_ENTITIES = "ignored_entities"
CONF_HISTORY = "history_per_entity"
CONF_GUESS_WINDOW = "guess_window"

DEFAULTS = {
    CONF_FIGHT_WINDOW: 60,
    CONF_FIGHT_FLIPS: 4,
    CONF_NOTIFY: True,
    CONF_TRACKED_DOMAINS: list(DEFAULT_TRACKED_DOMAINS),
    CONF_IGNORED_ENTITIES: [],
    CONF_HISTORY: 25,
    CONF_GUESS_WINDOW: 15,
}

SIGNAL_UPDATE = f"{DOMAIN}_update"
EVENT_FIGHT_STARTED = f"{DOMAIN}_fight_started"
EVENT_FIGHT_ENDED = f"{DOMAIN}_fight_ended"

CARD_URL = f"/{DOMAIN}_static/whodunit-card.js"
