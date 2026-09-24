# Changelog

The add-on and the Home Assistant integration are released as a matched
pair and share a version number. CI fails the build if they drift.

## 0.2.0 — 2026-09-24

- **Feature**: Force a re-scan of the Core, from the panel or via
  `qsys_bridge.rescan`. After a design is redeployed components appear and
  disappear and faders get restaged; waiting for a reconnect to notice is
  no use when someone is at the panel wondering where their new zone is.
  What you decided survives it — only what the Core reports is refreshed.

## 0.1.2 — 2026-09-24

- **Fix**: Carry every configured field back to the panel. The API built its
  config block from a hand-written list of field names that never gained
  area, device, icon, category or precision, so those boxes were always
  empty however they had been set — indistinguishable from the settings not
  saving. Now taken from the dataclass, with a test that they cannot drift.

## 0.1.1 — 2026-09-24

- **Feature**: "Exposed to Home Assistant" view — everything switched on,
  grouped by the device it creates, with its area, type, range, live value
  and any setting that differs from the default. Devices with no area are
  called out.
- **Feature**: Enabled controls are marked in the browse table rather than
  being findable only through a filter.
- **Fix**: The store published a control's range as `minimum`/`maximum`
  while the feed and the UI both said `min`/`max`, so the write box offered
  "undefined to undefined".
- **Fix**: Device and Area columns were hidden below 800px. They are
  settings, not decoration.

## 0.1.0 — 2026-09-23

- Initial release. Speaks QRC — JSON-RPC over TCP 1710 with null-terminated
  framing — so no plugin or script block is needed in the design beyond
  setting Script Access to External.
- Full discovery: the Core lists every component and control with type,
  direction, range and the string it displays. No name-guessing anywhere.
- Built around scale rather than discovery. A modest design offers around
  three hundred controls and perhaps fifty belong on a dashboard, so the
  panel narrows by component and acts in bulk.
- Platform suggestions refuse the impossible — a read-only meter is never
  offered as a switch — and controls that configure the DSP rather than
  operate it get no suggestion at all.
- Controls can be placed into devices and Home Assistant areas, both
  assignable to a whole selection at once. One `Mixer` component carries the
  outputs for every room in a building, and those belong in the rooms.
- Faders take their range from the Core's own staging and can be driven by
  normalised position, because a dB scale is not linear.
