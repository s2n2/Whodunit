"""Engine tests. Run with: python -m pytest tests"""

import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "whodunit_engine",
    Path(__file__).parents[1] / "custom_components" / "whodunit" / "engine.py",
)
engine = importlib.util.module_from_spec(_spec)
sys.modules["whodunit_engine"] = engine
_spec.loader.exec_module(engine)

Engine, Run, StateChange = engine.Engine, engine.Run, engine.StateChange


def make(**kw):
    users = {"u-sam": "Sam", "u-alex": "Alex"}
    return Engine(user_name=lambda uid: users.get(uid, "a user"), fmt_time=lambda t: f"t{t:g}", **kw)


def kinds(record):
    return [link.kind for link in record.links]


def test_physical_change_is_the_device():
    e = make()
    rec = e.record_change(StateChange("light.hall", "Hall", "off", "on", 1, "c1"))
    assert kinds(rec) == ["device"]
    assert rec.sentence == "Hall turned on at t1 because the device or its integration reported it directly."


def test_user_from_dashboard():
    e = make()
    rec = e.record_change(StateChange("light.hall", "Hall", "off", "on", 1, "c1", user_id="u-alex"))
    assert kinds(rec) == ["user"]
    assert "Alex did it" in rec.sentence


def test_untracked_entities_are_linked_but_not_returned():
    e = make()
    assert e.record_change(StateChange("binary_sensor.pir", "PIR", "off", "on", 1, "c-pir")) is None


def test_motion_automation_chain():
    """Real shape from the logbook: PIR -> automation -> light."""
    e = make()
    e.record_change(StateChange("binary_sensor.pir_lounge1", "pir_lounge1", "off", "on", 1, "c-pir"))
    e.record_run(Run("automation", "automation.motion_in_lounge_2", "Motion in Lounge",
                     "state of binary_sensor.pir_lounge1", "c-run", "c-pir", None, 1.01))
    rec = e.record_change(StateChange("light.lounge", "Lounge lights", "off", "on", 1.05, "c-run", "c-pir"))
    assert kinds(rec) == ["automation", "trigger", "device"]
    assert rec.links[1].name == "pir_lounge1"
    assert rec.links[1].detail == "off → on"
    assert rec.sentence == (
        "Lounge lights turned on at t1.05 because automation “Motion in Lounge” ran, "
        "triggered by pir_lounge1 changing off → on, which the device reported itself."
    )


def test_time_pattern_trigger():
    e = make()
    e.record_run(Run("automation", "automation.dispatch", "Energy schedule · dispatcher",
                     "time pattern", "c-run", None, None, 5))
    rec = e.record_change(StateChange("switch.towel", "Towel rail", "off", "on", 5.1, "c-run"))
    assert kinds(rec) == ["automation", "source"]
    assert rec.sentence.endswith("on a time pattern trigger.")


def test_multi_hop_chain_through_two_automations_and_a_user():
    e = make()
    # Sam toggles a helper on the dashboard
    e.record_context("c-svc", "u-sam", None)
    e.record_change(StateChange("input_boolean.movie", "Movie mode", "off", "on", 1, "c-svc", user_id="u-sam"))
    # automation A reacts and turns a switch on
    e.record_run(Run("automation", "automation.a", "Movie mode on", "state of input_boolean.movie",
                     "c-a", "c-svc", None, 1.1))
    e.record_change(StateChange("switch.tv", "TV", "off", "on", 1.2, "c-a", "c-svc"))
    # automation B reacts to the TV and dims the lights
    e.record_run(Run("automation", "automation.b", "Dim when TV on", "state of switch.tv",
                     "c-b", "c-a", None, 1.3))
    rec = e.record_change(StateChange("light.lounge", "Lounge", "on", "off", 1.4, "c-b", "c-a"))
    assert kinds(rec) == ["automation", "trigger", "automation", "trigger", "user"]
    assert [l.name for l in rec.links] == ["Dim when TV on", "TV", "Movie mode on", "Movie mode", "Sam"]
    assert rec.sentence == (
        "Lounge turned off at t1.4 because automation “Dim when TV on” ran, "
        "triggered by TV changing off → on, which automation “Movie mode on” did, "
        "triggered by Movie mode changing off → on, which Sam did."
    )


def test_script_called_from_automation_shares_context():
    e = make()
    e.record_change(StateChange("binary_sensor.door", "Door", "off", "on", 1, "c-door"))
    e.record_run(Run("automation", "automation.door", "Doorbell", "state of binary_sensor.door",
                     "c-run", "c-door", None, 1.1))
    e.record_run(Run("script", "script.chime", "Chime", None, "c-run", "c-door", None, 1.2))
    rec = e.record_change(StateChange("switch.chime", "Chime switch", "off", "on", 1.3, "c-run", "c-door"))
    assert kinds(rec) == ["script", "automation", "trigger", "device"]
    assert "called from automation “Doorbell”" in rec.sentence


def test_user_runs_script_from_ui():
    e = make()
    e.record_run(Run("script", "script.candles", "Lounge candles off", None, "c-ui", None, "u-alex", 1))
    rec = e.record_change(StateChange("switch.candles", "Candles", "on", "off", 1.1, "c-ui", user_id="u-alex"))
    assert kinds(rec) == ["script", "user"]
    assert rec.sentence.endswith("started by Alex.")


def test_user_presses_run_on_an_automation():
    e = make()
    e.record_context("c-svc", "u-sam", None)  # automation.trigger service call
    e.record_run(Run("automation", "automation.a", "Bedtime", "manual", "c-run", "c-svc", None, 1))
    rec = e.record_change(StateChange("light.bed", "Bed", "on", "off", 1.1, "c-run", "c-svc"))
    assert kinds(rec) == ["automation", "user"]


def test_unknown_parent_falls_back_to_source():
    e = make()
    e.record_run(Run("automation", "automation.mqtt", "MQTT button", "event mqtt", "c-run", "c-gone", None, 1))
    rec = e.record_change(StateChange("light.x", "X", "off", "on", 1.1, "c-run", "c-gone"))
    assert kinds(rec) == ["automation", "source"]
    assert rec.links[1].name == "event mqtt"


def test_fight_between_two_automations():
    e = make(fight_flips=4, fight_window=60)
    events = []
    e.add_listener(lambda kind, payload: events.append(kind))
    for i in range(6):
        who = "a" if i % 2 == 0 else "b"
        ctx = f"c-{i}"
        e.record_run(Run("automation", f"automation.{who}", who.upper(), "time pattern", ctx, None, None, 10 + i))
        old, new = ("off", "on") if i % 2 == 0 else ("on", "off")
        e.record_change(StateChange("switch.heater", "Heater", old, new, 10 + i + 0.1, ctx))
    assert "fight_started" in events
    fight = e.active_fights["switch.heater"]
    assert fight.kind == "fight"
    assert fight.flips == 6
    assert {a["name"] for a in fight.as_dict()["actors"]} == {"A", "B"}
    assert "fighting over Heater" in fight.summary()
    assert e.fights_started == 1
    # quiet for longer than the window -> it ends
    ended = e.tick(now=10 + 6 + 61)
    assert [f.entity_id for f in ended] == ["switch.heater"]
    assert not e.active_fights
    assert e.recent_fights[-1].active is False


def test_human_vs_automation_counts_as_fight():
    e = make(fight_flips=4)
    for i in range(4):
        if i % 2 == 0:
            e.record_change(StateChange("light.k", "Kitchen", "on", "off", i, f"c{i}", user_id="u-alex"))
        else:
            e.record_run(Run("automation", "automation.keep_on", "Keep kitchen on", "state of light.k",
                             f"r{i}", f"c{i-1}", None, i + 0.01))
            e.record_change(StateChange("light.k", "Kitchen", "off", "on", i + 0.02, f"r{i}", f"c{i-1}"))
    assert "light.k" in e.active_fights


def test_one_actor_flapping_is_not_a_fight():
    e = make(fight_flips=3)
    for i in range(10):
        e.record_change(StateChange("media_player.tv", "TV", "playing" if i % 2 else "paused",
                                    "paused" if i % 2 else "playing", i, f"c{i}"))
    assert not e.active_fights


def test_loop_detection():
    e = make()
    # A sets X; X triggers B; B sets Y; Y triggers A again
    e.record_run(Run("automation", "automation.a", "A", "time pattern", "c-a1", None, None, 1))
    e.record_change(StateChange("input_boolean.x", "X", "off", "on", 1.1, "c-a1"))
    e.record_run(Run("automation", "automation.b", "B", "state of input_boolean.x", "c-b1", "c-a1", None, 1.2))
    e.record_change(StateChange("input_boolean.y", "Y", "off", "on", 1.3, "c-b1", "c-a1"))
    e.record_run(Run("automation", "automation.a", "A", "state of input_boolean.y", "c-a2", "c-b1", None, 1.4))
    rec = e.record_change(StateChange("input_boolean.x", "X", "on", "off", 1.5, "c-a2", "c-b1"))
    assert kinds(rec)[-1] == "loop"
    assert e.active_fights["input_boolean.x"].kind == "loop"


def test_busiest_and_burst_peak():
    e = make()
    for i in range(4):  # the logbook showed 4 runs in 70 ms
        e.record_run(Run("automation", "automation.rack", "Clothes horse", "state", f"c{i}", None, None, 100 + i * 0.02))
    e.record_run(Run("automation", "automation.quiet", "Quiet", "time", "q", None, None, 100))
    top = e.busiest(now=101)
    assert top[0]["entity_id"] == "automation.rack"
    assert top[0]["runs_last_hour"] == 4
    assert top[0]["peak_per_second"] == 4
    assert e.busiest(now=100 + 3601 + 1) == []


def test_history_newest_first_and_bounded():
    e = make(history_per_entity=3)
    for i in range(5):
        e.record_change(StateChange("light.a", "A", "off", "on", i, f"c{i}"))
    hist = e.history("light.a", 10)
    assert [r.change.at for r in hist] == [4, 3, 2]
    assert e.recent(2)[0].change.at == 4


def test_ignored_entities():
    e = make(ignored_entities=["light.a"])
    assert e.record_change(StateChange("light.a", "A", "off", "on", 1, "c")) is None


def test_as_dict_is_json_safe():
    import json

    e = make()
    rec = e.record_change(StateChange("light.a", "A", "off", "on", 1, "c"))
    json.dumps(rec.as_dict())
