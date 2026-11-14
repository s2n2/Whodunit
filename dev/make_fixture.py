"""Run a realistic scenario through the engine and dump the websocket payloads the card reads."""
import importlib.util, json, sys, time
from pathlib import Path
root = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("wd_engine", root / "custom_components/whodunit/engine.py")
m = importlib.util.module_from_spec(spec); sys.modules["wd_engine"] = m; spec.loader.exec_module(m)
E, R, S = m.Engine, m.Run, m.StateChange
now = time.time() - 300
e = E(user_name={"u1": "Alex", "u2": "Sam"}.get, fight_flips=4)
t = now
def tick(d=1.0):
    global t; t += d; return t
# 1. PIR -> Motion in Lounge -> lounge lights
for i in range(3):
    e.record_change(S("binary_sensor.pir_lounge1", "pir_lounge1", "off", "on", tick(20), f"pir{i}"))
    e.record_run(R("automation", "automation.motion_in_lounge_2", "Motion in Lounge", "state of binary_sensor.pir_lounge1", f"ml{i}", f"pir{i}", None, tick(0.01)))
    e.record_change(S("light.lounge_lights_scaled", "Lounge lights", "off", "on", tick(0.05), f"ml{i}", f"pir{i}"))
    e.record_change(S("light.lounge_lights_scaled", "Lounge lights", "on", "off", tick(60), f"ml{i}x"))
# 2. Alex turns on movie mode -> automation -> TV on -> second automation dims lights + script
e.record_context("svc1", "u1", None)
e.record_change(S("input_boolean.movie_mode", "Movie mode", "off", "on", tick(5), "svc1", user_id="u1"))
e.record_run(R("automation", "automation.movie_mode_on", "Movie mode · start", "state of input_boolean.movie_mode", "mm", "svc1", None, tick(0.02)))
e.record_change(S("media_player.lounge", "Lounge", "off", "on", tick(0.4), "mm", "svc1"))
e.record_run(R("automation", "automation.turn_off_marantz_if_tv_turns_off", "Dim lounge when TV on", "state of media_player.lounge", "dim", "mm", None, tick(0.02)))
e.record_run(R("script", "script.loungecandles_off_broadlink", "LoungeCandles Off - Broadlink", None, "dim", "mm", None, tick(0.01)))
e.record_change(S("switch.lounge_candles", "Lounge candles", "on", "off", tick(0.2), "dim", "mm"))
# 3. Fight: towel rail energy controller vs daily schedule
for i in range(6):
    who = ("automation.bathroom_towel_rail_energy", "Towel rail · energy-aware start") if i % 2 == 0 else ("automation.bathroom_towel_rail_daily", "Towel rail · daily request")
    e.record_run(R("automation", who[0], who[1], "time pattern" if i % 2 else "state of sensor.electricity_solar_surplus_power", f"tr{i}", None if i % 2 else f"solar{i}", None, tick(4)))
    if i % 2 == 0: e.record_change(S("sensor.electricity_solar_surplus_power", "Solar surplus", "1200", "1450", t - 0.01, f"solar{i}"))
    old, new = ("off", "on") if i % 2 == 0 else ("on", "off")
    e.record_change(S("switch.towel_rail_bathroom", "Towel Rail Bathroom", old, new, tick(0.1), f"tr{i}"))
# 4. trigger storm: clothes horse controller 4x in 70ms + others
for i in range(4):
    e.record_run(R("automation", "automation.bathroom_heated_clothes_horse_energy_aware_start_controller", "Bathroom heated clothes horse · energy-aware start controller", "state of binary_sensor.energy_bathroom_rack_power_ready", f"ch{i}", None, None, t + i * 0.02))
for i in range(12):
    e.record_run(R("automation", "automation.energy_schedule_dispatcher", "Energy schedule · dispatcher", "time pattern", f"d{i}", None, None, now + i * 25))
# 4b. IR command turns the kitchen screen on; its light reports 6 s later in a fresh context
e.record_change(S("binary_sensor.pir_kitchen1", "pir_kitchen1", "off", "on", tick(2), "kpir"))
e.record_run(R("automation", "automation.turn_display_on", "Turn Kitchen Display On", "state of binary_sensor.pir_kitchen1", "kauto", "kpir", None, tick(0.01)))
e.record_run(R("script", "script.1634521151793", "Kitchen Display - Screen On", None, "kauto", "kpir", None, tick(0.01)))
e.record_command(m.Command("kauto", "remote.send_command", frozenset({"remote.kitchen_ir"}), frozenset({"kitchen"}), tick(0.01)))
e.record_change(S("light.kitchen_display_unit_main", "Kitchen DIsplay Unit Main", "off", "on", tick(6.1), "kfresh", area="kitchen"))
# 5. Sam flips the hallway light from the app
e.record_change(S("light.hallway", "Hallway Lights", "off", "on", tick(3), "app1", user_id="u2"))
out = {
  "recent": {"changes": [r.as_dict() for r in e.recent(50)]},
  "explain": {eid: {"entity_id": eid, "name": e.history(eid)[0].change.name, "tracked": True,
                    "changes": [r.as_dict() for r in e.history(eid, 10)], "sentence": e.history(eid)[0].sentence}
              for eid in ["light.kitchen_display_unit_main", "switch.lounge_candles", "light.lounge_lights_scaled", "switch.towel_rail_bathroom", "light.hallway", "media_player.lounge", "input_boolean.movie_mode"]},
  "fights": {"active": [f.as_dict() for f in e.active_fights.values()], "recent": [], "window": e.fight_window, "flips": e.fight_flips},
  "busy": {"automations": e.busiest(15)},
}
(Path(__file__).parent / "fixture.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
print(e.history("switch.lounge_candles")[0].sentence)
print([f.summary() for f in e.active_fights.values()])
