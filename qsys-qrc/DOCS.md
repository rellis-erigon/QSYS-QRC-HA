# Q-SYS QRC Bridge

Connects to Q-SYS Cores over QRC, discovers every control they expose, and
lets you choose which become Home Assistant entities.

## Before you start

Every component in Q-SYS Designer has a **Script Access** property:
`None`, `Script`, `External` or `All`. QRC only sees **External** or **All**.

A design where nobody set it will connect, log on, and report almost
nothing. That is not a fault in the bridge — check Script Access first.

## Configuration

```yaml
cores:
  - name: Main Hall
    host: 192.0.2.20
    port: 1710
    username: ""
    password: ""
poll_rate: 0.5
log_level: info
```

| Option | Meaning |
|--------|---------|
| `name` | What the Core is called in Home Assistant. |
| `host` | The Core's IP address. QRC is TCP port 1710. |
| `port` | Rarely anything but 1710. |
| `username`, `password` | Only if the Core requires a logon. Leave blank otherwise. |
| `poll_rate` | How often the Core reports changes, in seconds. 0.5 is responsive without being chatty. |

## Choosing what to expose

The panel lists components down the side and their controls beside them.
Filter, select a batch, and press **Expose with suggested type** — the
add-on already worked out what each control can be from its type and
direction.

Nothing is exposed until you say so, and nothing is watched until it is
exposed. Subscribing to all three hundred controls of a design in order to
follow the fifty you care about would put a continuously changing audio
meter on the wire for no reason.

## Devices and areas

Two settings decide where a control lands in Home Assistant.

**Device** is which Home Assistant device the control belongs to. It
defaults to the Q-SYS component, which is right until it is not: one `Mixer`
component carries the outputs for every room in a building, and those
outputs belong in the rooms rather than in a device called Mixer. Set the
device name on `output.3.gain` and `output.3.mute` to "Function Room 3" and
they become one device.

**Area** is the Home Assistant area that device sits in. Existing areas are
offered for reuse; typing a new name creates it. It is passed as a
*suggestion*, so Home Assistant will not override a device you have already
placed by hand.

A device takes its area from the first control in it that names one, so
assigning the area once is enough to move the whole thing.

Both can be set in bulk. Select every control for a room, type the area
once, and press **Set area**.

## Presentation

| Setting | What it does |
|---------|--------------|
| Name | What the entity is called. Defaults to the component and control. |
| Icon | Any `mdi:` icon. |
| Category | `config` or `diagnostic` moves the entity out of a device's main view, which is where a threshold or a setup flag belongs. |
| Precision | Decimal places shown. A fader reporting -30.39999961 does not need eight. |
| Unit | Overrides the unit Q-SYS reports, for the rare case it reports none. |

## Faders and position

A number entity takes its range from the Core, so a fader staged to
−50…−25 dB offers that range and nothing wider.

**Use position** drives the control by normalised position rather than by
value. Because a dB scale is not linear, a slider moving in position feels
correct where one moving in dB does not.

## Redeploying a design

Rediscovery refreshes what the Core reports — values, ranges, new
components — and never touches what you decided. Controls that vanish are
forgotten, except ones you named or exposed: designs are often redeployed in
pieces, and losing the setup would be worse than a stale row.
