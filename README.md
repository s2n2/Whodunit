# Whodunit

**Why did that just happen?**

Whodunit is a Home Assistant integration and dashboard card that explains
*why* any light, switch, helper or player changed. It follows the chain of
causes back to a person, a device or a schedule, across as many automations as
it takes. It also tells you when automations start fighting over the same entity.

```
Lounge candles  on → off   13:42:55
  ↳ script     LoungeCandles Off - Broadlink
  ↳ automation Dim lounge when TV on
  ↳ triggered  Lounge (TV)  off → on
  ↳ automation Movie mode · start
  ↳ triggered  Movie mode  off → on
  ↳ person     Alex
```

> Lounge candles turned off at 13:42:55 because script “LoungeCandles Off -
> Broadlink” ran, called from automation “Dim lounge when TV on”, triggered by
> Lounge changing off → on, which automation “Movie mode · start” did,
> triggered by Movie mode changing off → on, which Alex did.

No YAML, no labels and no changes to your automations are needed.

## How it works

Every state change in Home Assistant carries a *context*. An automation run
gets a new context whose parent is the context of the thing that triggered it,
and everything the run does carries the run's context. Whodunit listens to
`state_changed`, `automation_triggered`, `script_started` and `call_service`,
remembers those links in memory, and walks them backwards for each change.

| Chain step | Meaning |
| --- | --- |
| **Automation / Script** | It ran and made the change. A script called from an automation shows both. |
| **Triggered by** | The state change that started the automation (`off → on`). Tap **why?** to explain that one too. |
| **Person** | Someone changed it from a dashboard, the app, voice, or pressed *Run*. The person's name is used when available. |
| **Device / integration** | Nothing in HA asked for it: a wall switch, a remote, the device itself, or a template. |
| **Trigger** | A trigger with no state behind it: time pattern, sun, webhook, event. |
| **Loop** | The chain led back to an automation already in it. |

## Fights and loops

A **fight** is when one entity changes *N* times inside a window, default
4 times in 60 seconds, and at least two different actors caused those changes.
For example, two automations disagree, or someone keeps turning a light off and an
automation keeps turning it back on. A single actor flapping on its own (a
media player, a blinking light) does not count.

When a fight starts Whodunit:

- turns on `binary_sensor.whodunit_automation_fight`,
- fires `whodunit_fight_started` (and `whodunit_fight_ended`),
- shows a persistent notification naming each actor and its flip count (optional),
- counts it in `sensor.whodunit_fights_today`.

## Busy automations

`sensor.whodunit_busiest_automation` and the card's **Busy** tab rank
automations by runs in the last hour. They also show the largest **burst**, the most runs
within one second. A burst of 3 or more usually means several triggers fire for
the same event, which is worth a `mode: single` or a short `for:`.

## Install

1. HACS → ⋮ → Custom repositories → add this repository as an *Integration*.
2. Install **Whodunit** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **Whodunit**.

The card is registered automatically; no resource needs adding.

## Card

```yaml
type: custom:whodunit-card
```

| Option | Default | |
| --- | --- | --- |
| `title` | `Whodunit` | Card title |
| `entity` | none | Open on the **Why?** tab for this entity |
| `default_tab` | `live` | `live`, `why`, `fights` or `busy` |
| `tabs` | all | Which tabs to show, e.g. `[why, fights]` |

Tabs:

- **Live**: every tracked change as it happens, with its closest cause. Tap a row to see the full chain.
- **Why?**: pick any entity and see its last changes with full chains. Automation steps link to their trace.
- **Fights**: fights happening now and recent ones, with who flipped what.
- **Busy**: automations ranked by runs in the last hour, plus burst size.

Set `--whodunit-max-height` with card-mod to change the scroll height.

## Ask it from a script, or have the house say it

`whodunit.explain` returns data and a plain sentence:

```yaml
action: whodunit.explain
data:
  entity_id: light.hallway
response_variable: why
```

```yaml
# "Why is the hallway light on?" → say the answer on the Nabaztag / kitchen speaker
- action: whodunit.explain
  data: { entity_id: light.hallway, limit: 1 }
  response_variable: why
- action: tts.speak
  target: { entity_id: tts.piper }
  data:
    media_player_entity_id: media_player.kitchen_speaker
    message: "{{ why.sentence }}"
```

Notify your phone when a fight starts:

```yaml
triggers:
  - trigger: event
    event_type: whodunit_fight_started
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: Automation fight
      message: "{{ trigger.event.data.summary }}"
```

## Options

Settings → Devices & services → Whodunit → Configure.

| Option | Default | |
| --- | --- | --- |
| Flips that make a fight | 4 | |
| Fight window | 60 s | Also how long a fight must be quiet to end |
| Notification on fight | on | |
| Domains to explain | lights, switches, covers, climate, fans, media players, locks, vacuums, helpers (`input_*`, `number`, `select`) and more | Other entities still appear as triggers in chains |
| Entities to ignore | none | |
| Changes per entity | 25 | |

## Limits

- Memory only. Chains start from when Home Assistant started. For older changes,
  the **Why?** tab gives a best guess from the entity's current context.
- A cause outside Home Assistant (Node-RED, an MQTT client, a phone app talking
  straight to a device) shows as *Device / integration*, or as the user whose
  token made the call.
- Attribute-only changes are recorded only when something commanded them, such as an
  automation setting brightness. Changes an integration makes on its own, such as media position, are not.

## Development

The causality engine (`custom_components/whodunit/engine.py`) has no Home
Assistant imports and is fully unit tested:

```bash
python -m pytest tests
```

`dev/preview.html` renders the card against payloads generated by the real
engine (`python dev/make_fixture.py`), served with `python -m http.server`.
