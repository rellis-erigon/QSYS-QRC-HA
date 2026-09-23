# Q-SYS for Home Assistant

Brings Q-SYS Cores into Home Assistant over **QRC**, the Core's own remote
control protocol, so no plugin or script block is needed in the design.

Two halves, released as a matched pair:

- **`qsys-qrc/`** — the add-on. Holds the connection to each Core, discovers
  every visible control, and provides the panel where you choose what each
  one becomes.
- **`custom_components/qsys_bridge/`** — the integration. A thin client that
  turns the controls you exposed into entities.

## Why this exists

Q-SYS introspects properly: a Core will list every component and every
control on it, with type, direction, range and the string it displays. The
problem is not discovery, it is scale. A modest design offers **around three
hundred controls**, and perhaps fifty belong on a dashboard.

So the add-on is built around deciding rather than finding: browse by
component, filter, select a batch, and expose them with the types it already
worked out for you.

## What a control becomes

The add-on suggests a platform from the control's own type and direction,
and will not offer one that cannot work — a read-only meter is never
offered as a switch.

| Q-SYS type | Writable | Read only |
|------------|----------|-----------|
| Boolean | Switch, Button | Binary sensor |
| Float, Integer | Number | Sensor |
| String | Text | Sensor |

Controls that configure the DSP rather than operate it — `invert`, `bypass`,
`pre.post` — get no suggestion. Polarity inversion does not belong beside a
volume slider.

## Faders

A number entity takes the Core's own staging as its range, so a fader
limited to −50…−25 dB offers exactly that and not 0 dB.

Setting **Use position** on a control drives it by normalised position
instead of by value. A dB scale is not linear, and a slider that moves in
position is the one that feels right.

## Installing

1. **Settings → Add-ons → Add-on store → ⋮ → Repositories**, add
   `https://github.com/rellis-erigon/QSYS-QRC-HA`.
2. Install **Q-SYS QRC Bridge** and configure your Cores.
3. Copy `custom_components/qsys_bridge/` into your `custom_components/`
   folder, or install this repository through HACS.
4. **Settings → Devices & services → Add integration → Q-SYS Bridge.** The
   add-on is found through Supervisor; you should not have to type a URL.

## Requirements

Components must have **Script Access** set to **External** or **All** in
Q-SYS Designer. QRC cannot see anything else, so a design where nobody set
it connects happily and reports almost nothing. The add-on says so in its
log rather than looking broken.

## Development

```bash
pip install pytest pytest-asyncio pytest-aiohttp aiohttp
pytest qsys-qrc/tests -q
```

The add-on and the integration carry the same version number, and CI fails
the build if they drift.
