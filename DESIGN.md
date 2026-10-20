---
name: dmrdash
description: Dual-VFO front panel for a personal BrandMeister DMR hotspot.
colors:
  panel: "#1d2023"
  panel-2: "#24282c"
  panel-3: "#2c3136"
  rule: "#3a4046"
  rule-2: "#4a5158"
  row: "#2f3439"
  legend: "#b3bac2"
  legend-dim: "#8d959e"
  white: "#eef1f3"
  lcd: "#e4e0cc"
  lcd-ink: "#17190f"
  lcd-dim: "#5c5946"
  lcd-rule: "#bdb79c"
  lcd-tx: "#f0d9d2"
  lcd-tx-ink: "#a3191f"
  tx: "#e5484d"
  rx: "#35b46a"
  busy: "#f0a020"
  off: "#464c52"
  tx-text: "#ff9b9e"
  rx-text: "#8fe0b0"
  fail-text: "#ff8a8d"
  alarm-bg: "#3a1e20"
  alarm-text: "#ffd2d3"
  mine: "#2b2422"
  danger-bg: "#4a2326"
typography:
  readout:
    fontFamily: "Callsign, ui-monospace, SF Mono, monospace"
    fontSize: "50px"
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "0"
  readout-idle:
    fontFamily: "Callsign, ui-monospace, SF Mono, monospace"
    fontSize: "28px"
    fontWeight: 800
    lineHeight: 1.05
  timer:
    fontFamily: "VFO Timer, ui-monospace, monospace"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1
    fontFeature: "tnum"
  title:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "17px"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.4
  data:
    fontFamily: "ui-monospace, SF Mono, Cascadia Mono, Roboto Mono, Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.25
    fontFeature: "tnum"
  label:
    fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.12em"
rounded:
  tag: "3px"
  control: "6px"
  module: "10px"
  led: "50%"
spacing:
  xs: "6px"
  sm: "10px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  vfo-module:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.module}"
    padding: "12px"
  lcd-window:
    backgroundColor: "{colors.lcd}"
    textColor: "{colors.lcd-ink}"
    rounded: "{rounded.control}"
    padding: "14px 16px 12px"
  lcd-window-tx:
    backgroundColor: "{colors.lcd-tx}"
    textColor: "{colors.lcd-ink}"
  key:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.white}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 10px"
    height: "34px"
  key-action:
    backgroundColor: "{colors.panel-3}"
    textColor: "{colors.legend}"
    rounded: "{rounded.control}"
    height: "34px"
  key-confirm:
    backgroundColor: "{colors.danger-bg}"
    textColor: "{colors.white}"
    rounded: "{rounded.control}"
  tg-input:
    backgroundColor: "{colors.lcd}"
    textColor: "{colors.lcd-ink}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "34px"
  slot-tag:
    backgroundColor: "{colors.legend}"
    textColor: "{colors.panel}"
    rounded: "{rounded.tag}"
    padding: "3px 5px"
  alarm:
    backgroundColor: "{colors.alarm-bg}"
    textColor: "{colors.alarm-text}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  log-box:
    backgroundColor: "{colors.panel-2}"
    rounded: "{rounded.module}"
    padding: "12px 0 4px"
---

# Design System: dmrdash

## Overview

**Creative North Star: "The Dual-VFO Front Panel"**

dmrdash reads as the face of a transceiver, not an admin console. The two DMR timeslots are VFO A and VFO B: two equal modules, each holding a positive LCD window where the callsign on air sits the way a rig shows frequency. Everything around the windows is a flat graphite panel with small engraved legends, a status strip of LEDs, and rows of hard keys for talkgroup memory.

The panel is flat and quiet. There is no glow, no bevel, no simulated knobs, and no skeuomorphic texture; the rig reference lives in structure (paired VFOs, LCD windows, LED legends, keys) and in the readout faces, not in rendering tricks. Colour is almost entirely neutral graphite and warm LCD ground, so the three rig LED colours carry meaning on sight: red is transmit, green is receive or healthy, amber is busy or warning. Normal is quiet; a fault is the loudest thing on the page.

Density is instrument-like: compact rows, tabular figures, and DMR vocabulary left intact. A single dark theme is a decision, not an omission.

**Key Characteristics:**
- Two equal VFO modules side by side, stacking on a phone.
- Positive LCD windows (warm pale ground, near-black ink) inside a flat graphite panel.
- Split readout type: a heavy mono callsign face and a seven-segment timer face; system sans for prose and legends.
- Rig LED colours (TX red, RX green, BUSY amber) used only for state.
- Talkgroup subscriptions kept off the panel face, behind a ⋯ overflow key at each slot's bottom right.

## Colors

A graphite neutral panel, a warm khaki LCD ground, and three rig LED colours held strictly to state meaning.

### Primary
- **Positive LCD Ground** (lcd): the readout window background. The only large light surface on the page; it is where the eye lands first.
- **LCD Ink** (lcd-ink): callsign, name and TG characters on the LCD.
- **Faded LCD Ink** (lcd-dim): idle readouts, metadata line on the LCD, input placeholder.
- **LCD Hairline** (lcd-rule): the rule above the LCD metadata line, and the "------" empty-slot placeholder.
- **Keyed LCD Blush** (lcd-tx) with **Transmit Ink** (lcd-tx-ink): the whole LCD tints when the slot is transmitting from local RF; the RF tag takes the deep red ink.

### Secondary (state LEDs)
- **TX Red** (tx): transmit LED, RF source tag in the log, alarm border, destructive-action border on hover and confirm.
- **RX Green** (rx): receive LED and healthy-service LEDs.
- **BUSY Amber** (busy): warn LEDs, elevated BER, packet loss, warning events. Nothing else.
- **Dark LED** (off): unlit LED and idle slot state.
- Readable text tints for the same meanings on graphite: **TX Text** (tx-text), **RX Text** (rx-text), **Fail Text** (fail-text).

### Neutral
- **Graphite Panel** (panel): page background.
- **Module Graphite** (panel-2): VFO modules and lower-deck boxes.
- **Key Graphite** (panel-3): key caps.
- **Panel Rule** (rule) and **Key Rule** (rule-2): module borders and strip divider; key borders and scrollbar thumb.
- **Row Rule** (row): table and event-list row dividers.
- **Engraved Legend** (legend) and **Dim Legend** (legend-dim): legends, secondary labels, TG names; table heads, dt labels, timestamps.
- **Panel White** (white): primary text on graphite and the focus ring.
- **Alarm Ground** (alarm-bg) with **Alarm Text** (alarm-text): the link-down alarm banner.
- **Own-Call Row** (mine): the log row of the owner's own transmissions.
- **Danger Ground** (danger-bg): remove-TG hover and the armed confirm key.

### Named Rules
**The LED Meaning Rule.** Red, green and amber mean TX/fail, RX/healthy and BUSY/warn respectively, and are never used for decoration, selection, focus, or brand.

**The Amber Reservation Rule.** Amber (busy) is reserved for BUSY and warning meaning. Selection uses the legend tone and focus uses a white ring; no interactive affordance borrows amber.

**The Positive LCD Rule.** Readout windows are light-ground, dark-ink. Never invert them to a glowing dark LCD.

## Typography

**Display Font:** Callsign (an inlined Saira Bold subset, wght 700 / wdth 100, with Saira's barred zero made the default 0), falling back to ui-monospace
**Timer Font:** VFO Timer (an inlined DSEG7 Classic Bold subset), falling back to ui-monospace
**Body Font:** system-ui sans stack
**Data Font:** ui-monospace stack

**Character:** A split readout, talkgroup first. The LCD carries two readouts in the callsign face, who (the callsign) then where (the TG number), so both answer at arm's length. The face is a squarish Eurostile-style sans, the lettering of Yaesu, Kenwood and Icom front panels; its barred zero keeps 0 and O apart in callsigns. The seven-segment timer is the one place the rig's display hardware is quoted literally. Everything a person reads as language (names, TG names, legends, messages) stays in the system sans.

### Hierarchy
- **Readout** (800, 68px, 0.95 on desktop; 46px under 820px): the live callsign in each LCD. The face is proportional, so after each render the callsign is measured and shrunk just enough to fit beside the timer (2% margin for glyph overhang, floor 30px); a refit runs once both readout faces have loaded and on resize.
- **TG Readout** (800, 32px, 1; 28px under 820px): the talkgroup number on the second LCD line, followed by the TG name in sans 600 16px. Private calls show a small engraved PRIVATE label (700 10.5px, 0.14em) before the number.
- **Readout Idle** (800, 40px; 28px under 820px): the last caller held in an idle LCD, dimmed, with a LAST tag; its TG readout drops to 24px (22px under 820px).
- **Timer** (700, 26px, 1, tabular): call duration, top right of the LCD, with the RF/NET tag stacked under it.
- **LCD detail line** (sans 13px, LCD dim): under the hairline, the operator name (600 14px, LCD ink), the DMR ID in mono, and the talker alias in italic sans. Idle shows the name, how long ago, and the subscription counts.
- **Body** (400, 14px, 1.4): page default; key text at 13px; notes and hints at 12.5px.
- **Data** (400, 13px, 1.25, tabular): log table, IDs, TG numbers, frequencies, timestamps; 12.5px on the LCD metadata line, 12px in the phone table. Station callsign in the strip is the data face at 700 18px.
- **Label** (600, 11px, 0.12em, uppercase): engraved legends: LED names, TIMESLOT, TALKGROUPS, box titles, table heads (0.1em).

### Named Rules
**The Split Readout Rule.** The Callsign face sets callsigns and the LCD's TG number only; the VFO Timer face sets the running call timer only. A 14-segment callsign face was tried and rejected for glance legibility; do not reintroduce segment faces for text.

**The Mono-Is-Data Rule.** Monospace is for data and identifiers (IDs, TG numbers, times, BER, addresses). Prose, names and legends use the system sans.

**The Zero Rule.** Any face that sets callsigns must keep 0 and O distinct (a barred, slashed or dotted zero). B612, Chakra Petch and Michroma were rejected on this test.

**The OFL Name Rule.** The segment face ships under the name "VFO Timer", never "DSEG", because DSEG is a Reserved Font Name. Any new subset of an OFL face with a Reserved Font Name must be renamed.

## Layout

The status strip spans the full window width (24px side padding, 16px under 820px) with its hairline running edge to edge; everything below it sits in a single centred column (max 1240px, 16px side padding). Top to bottom: the status strip (station callsign and hotspot ID, LED legends, local and UTC clock, the Presets key and the (i) key) divided by a hairline; an optional alarm banner; a page message line when a preset reports; the VFO pair in a two-column equal grid (16px gap); then the Last heard log at full width.

At 820px and below everything collapses to one column: the VFOs stack A over B, the readout shrinks, and the log drops its name, TG-name and Loss columns to keep callsign, TG and figures readable.

Spacing runs on a tight instrument rhythm: 6px between keys, 10-12px inside modules and between head and window, 16px between modules and decks, 24px between strip groups.

## Elevation & Depth

Flat. There are no shadows anywhere. Depth is tonal and by hairline: page graphite, a step lighter for modules, a step lighter again for key caps, each bordered with a 1px rule. The LCD window sits forward by contrast alone, its light ground against graphite with a near-black 1px edge.

### Named Rules
**The Flat Panel Rule.** No box-shadow, glow, bevel, gradient or texture. Separation comes from a tonal step plus a 1px rule.

## Shapes

Softly squared hardware. Modules and boxes use 10px corners, LCD windows, keys, inputs and the alarm use 6px, and small tags (the A/B slot tag, RF/NET/LAST tag) use 3px. LEDs are 9-10px circles. Strokes are 1px by default; the source tag uses 1.5px.

Talkgroup subscription type is spelled out in words (dynamic, timed, auto-static), never encoded in border style.

## Components

### Keys (buttons)
Hard keys on the panel, tactile by border, never by shadow.
- **Shape:** 6px corners, 34px tall, 10px horizontal padding, key graphite cap with a 1px key-rule border.
- **Small key** (Edit, Drop dynamic, Done, Stop call): 30px tall, 12.5px text, 14px stroked line icon.
- **Action key** (Stop call): legend-tone text at 600; TX-red border on hover; armed state reads "Cut CALLSIGN?".
- **Remove key:** a spelled-out "Remove" on each static row of the editor; transparent with a key-rule border, danger ground with TX border and TX text on hover.
- **Hover / Focus:** border brightens to the legend tone (0.15s ease-out). Focus is a 2px white outline offset 2px. Interrupting actions turn their border TX red on hover, and when armed for confirmation show danger ground, TX border and white text.
- **Disabled:** 45% opacity; in-flight keys 60%.

### Inputs / Fields
- **Style:** the Add TG field is a small LCD: LCD ground, LCD ink, bold mono, 34px tall, 6px corners, legend-tone border.
- **Hint / Error:** a name hint sits beside the field in legend tone; a failed add or remove shows a persistent fail-text message under the panel with the typed value kept.

### Status strip / LEDs
- Circular LED plus engraved legend. Green for healthy, amber for warning, red for failure (the legend itself turns fail-text on failure), dark for off.

### Alarm banner
- Full-width under the strip: alarm ground, 1px TX-red border, 6px corners, warning line icon, bold headline then plain explanation.

### VFO Module (signature)
- A module-graphite card holding: a head row with the A/B slot tag, the TIMESLOT legend, the slot state LED (TX / RX / IDLE) at the right and, only during a network call, the Stop call key beside it; the LCD window; the talkgroup editor when open; a footer with the message line on the left and the ⋯ talkgroup key on the right.
- **Talkgroup menu (⋯):** a 34px square small key at the footer's right. It opens a popover upward, right-aligned: panel ground, dim-legend border, 8px corners, a neutral drop shadow (the only shadow on the page, for a layer that floats over the LCD). Inside: a "TALKGROUPS · TSn" legend, one 34px row per TG (bold mono number, sans name, dim mono type for dynamic/timed/auto-static), the TG carrying the current call in RX text, and a full-width "Edit talkgroups" key when an API key is set. It closes on Escape (focus returns to ⋯), on a tap outside, or when the other slot's menu opens, and it does not re-render on polls while open.
- **Talkgroup editor (on demand):** opens between the LCD and the footer in a panel-ground box with a dim-legend border. One 40px row per TG: mono number, sans name, then a Remove key (static) or the type in dim mono (dynamic, timed: read-only). The foot holds the LCD-style add field with Add and the name hint, Drop dynamic when any exist, and Done. Escape or Done closes it and returns focus to the ⋯ key. The editor does not re-render on polls while open, so typing is never interrupted.
- **LCD window, live:** callsign readout; TG number readout with the TG name; a hairline; then the detail line (operator name, ID, italic alias). The timer sits top right with an RF or NET source tag under it. RF calls tint the whole window LCD blush.
- **LCD window, idle:** holds the last caller and their TG at reduced size, dimmed, tagged LAST; the detail line gives the name, how long ago, and the static/dynamic/timed counts. With no history it shows a "------" placeholder and "Listening · no calls yet on this slot".

### Station and events popover (i)
- A square small key with a stroked info icon at the far right of the strip opens a downward popover (same shell as Presets, up to 380px wide, scrolls if taller than the window) holding the STATION definition list and the EVENTS list. It closes on Escape, outside tap, or when another menu opens.
- A 7px BUSY-amber dot on the key's corner flags warning or error events that arrived since the popover was last opened; opening it clears the dot. Health problems still raise the alarm banner; the dot only says "there's something new to read".
- The EVENTS heading carries a "Clear" text button (legend tone, rule-2 underline, disabled when empty) that empties the Pi's in-memory event list for every viewer; the journal is untouched. The alarm banner has no dismiss: it reflects live health and clears itself.
- Retry storms collapse: an identical message within 2 minutes of its group's oldest copy joins that group, shown once at its newest time with a dim mono "×8 since 17:04:38". Repeats further apart stay separate lines, so chronology outside a burst is kept.

### Log
- Module-graphite boxes with legend titles. The log table is mono and tabular with sans name columns, row hairlines, a red RF or grey NET source tag per call, the talkgroup number and its name in separate columns (the name column is sans, dim legend, hidden on phones), the owner's own calls on the own-call row tint, and amber or fail-text for degraded BER and loss. On mouse devices a hovered row lifts to key graphite (the own-call row to a slightly lighter tint of its own) so its cells read across; touch screens get no hover state. Known callsigns are links to their QRZ.com page (new tab): same colour as the text, with an underline that fades in on hover or keyboard focus. Calls known only by DMR ID stay plain text. Group-call TG numbers above 90 link the same way to BrandMeister Hoseline with that TG pre-subscribed (`?subscribe=N`); TGs 90 and below stay plain. Private calls show "PC" and the DMR ID in the talkgroup column, and "CALLSIGN (Name)" in the name column when the ID resolves (callsign links to QRZ). The slot LCD shows the same under PRIVATE.
- Every log column head is a sort button. Click once to sort, click again to reverse. A new column starts newest or largest first, except Callsign and TG name, which start A–Z (case-insensitive, numbers in numeric order). The active head turns white and shows a small ▲/▼ with `aria-sort`, and inactive heads reserve no arrow space, so the phone table does not widen. Missing values always sort last, and ties fall back to newest first. The choice is saved per browser in localStorage.

### Talkgroup presets (header)
- A "Presets" small key at the right of the status strip opens a downward popover (same shell as the ⋯ menu). Each preset row is one wide button: the name (sans 600) and a mono summary ("TS1 268 91 · TS2 268912 · drops dynamics") that wraps rather than truncates. The preset matching the current statics is tagged IN USE (RX-text outline tag) and disabled. A ✕ on each row deletes after an armed second tap ("Delete?", danger ground, 4 s timeout).
- The footer key "Save current as preset…" opens an inline form: one-line hint, name field, a checkbox "Also drop dynamic TGs when applied", Save and Cancel.
- Results go to a page-level message line under the alarm bar (slot message style): "Applied Net night: +91 on TS1, −268911 on TS2" with Undo, which restores the previous statics. Partial failures stay as a persistent error, still with Undo.
- Presets snapshot static TGs only; the server re-reads BrandMeister before every apply and changes only the difference.

### Log filter
- On mouse devices a hovered row reveals small funnel keys (22px, dim legend, rule-2 ground on hover) after the callsign/name and after the TG number; the QRZ and Hoseline links are untouched. On touch screens the funnels are hidden and tapping a row's time or TS cell filters by its TG.
- Active filters show as pill chips beside the LAST HEARD title ("CT1EQL ✕", "TG 268 Portugal ✕") with an "n of 50" count; clicking a chip clears it. Filters combine and survive refreshes; an empty result says "No calls in the log match this filter."
- A "ROWS 5 · 10 · 50" segmented control (radio group, arrow keys move the choice) sits before the kerchunk switch: joined mono buttons on the page ground, the chosen one key graphite with a legend border and white text. It limits how many matching rows are drawn, not which calls match; when rows are cut, a footer line reads "10 of 34 shown" with a "Show all" text button. Remembered per browser; default 50 (all the Pi keeps).
- A "COLOUR Off · Caller · TG · TS" segmented control (same style as ROWS, arrow keys move it) colours rows by one key at a time. Caller and TG use six hues: cyan #5fc6de, blue #5a9cff, periwinkle #9a9cff, violet #bb8cff, orchid #e58bd6, lavender #dcb8ff. TS uses two fixed hues, TS1 cyan and TS2 violet. None sits near the LED red, amber or green, and none can pass for plain text (ice and steel tones were tried and dropped for that). The keyed cell takes the hue (the callsign, the TG number in bold, or the slot number in bold), the row gets an 8% wash in it (no left-edge stripe); the own-call row keeps its own tint; private calls stay plain in TG mode. Callers and TGs each start from a hash of the key, take the next free hue if a key already in the log holds it, and never change hue mid-session. Remembered per browser.
- A "Hide kerchunks" switch sits at the right of the LAST HEARD bar: an engraved-legend label with a 28×16 track (panel ground, rule-2 border) whose thumb slides right and turns white when on; it uses no LED colour. It hides calls shorter than an adjustable cutoff (calls with no duration are kept), shows the "n of 50" count, combines with the filter chips, and is remembered per browser. While on, a "< [1] s" mono number box follows it (0.5 s steps, 0.5–10 s, default 1 s, matching WPSD's kerchunk filter); a cleared field keeps the last good value and is restored on blur.

### Section order
- Last heard, RF BER and Live log can be put in any order. Each title bar starts with a grip: a six-dot icon in dim legend on a 22×26 transparent key that turns white on hover.
- Drag the grip with a mouse or finger. While a section is being dragged, its border lifts to the legend tone, and the other sections jump into place as the pointer passes their middles. Nothing floats and there is no shadow, keeping to the flat panel. Near the window's top or bottom edge the page scrolls, so a section can pass a long Last heard log.
- Keyboard: focus the grip and use the arrow keys to move the section up or down; a polite live region announces "RF BER moved to position 1 of 3".
- The order is remembered per browser (localStorage), like the other view settings.
- Swaps glide: each move runs in a view transition scoped to the deck (`deck.startViewTransition`). Browsers without that use `document.startViewTransition` with the root left uncaptured, and the move is instant where neither exists or reduced motion is on. Each box is named only while sorting (`.deck.vt`), so it isn't captured or made a stacking context the rest of the time. The move takes 0.22 s on a fast-out ease, with no cross-fade, since it's the same box before and after. `::view-transition { pointer-events: none }` keeps the drag reaching the page mid-glide.

### RF BER trend
- A collapsible box between Last heard and Live log: disclosure title "RF BER" (remembered per browser), the dim legend "Your radio into the hotspot", and at the right a mono summary: RX offset, last BER, and the median of the last 10 overs at the current offset (amber or fail text past the cut-offs).
- An inline SVG plot, one dot per RF over, evenly spaced (10–40px) with the newest at the right edge, because overs come in bursts hours apart. Y runs 0 to at least 6%, with the log's cut-offs as dashed lines: 2% BUSY amber and 5% fail text. Dots are legend tone, amber at 2% and above, fail text at 5% and above; a dropped over ("RF voice transmission lost") is a hollow ring, so the meaning never rests on colour alone. A 2px dim-legend line gives the rolling median of five, restarted at each RX offset change.
- RX offset changes (parsed from DMRHost's "RX Offset" start-up line) draw a dotted vertical rule with the new value in mono above the plot, on two alternating rows; labels that don't fit are dropped. Time labels sit under the first over and after gaps over 30 minutes, with the date when the day changes.
- Hover (or tap) a column for a tooltip: BER and dropped, time, TS and duration, TG or private call, RX offset. A key row under the plot explains dot, ring, median and offset marks.
- Source: a 200-over RAM ring in the dashboard, rebuilt from the journal on start like everything else; no file is written.

### RX offset suggestion and tuning
Chosen from mockups/rx-tune.html: option B for the suggestion and option D for the tuning run.
- **Tune key:** a small key (24px, sans 12px) at the end of the RF BER summary. When the median of the last 5 to 10 overs at the current offset is over 2%, it becomes "Retune?" with a BUSY-amber border and a 7px amber dot. Only overs of at least 1.5 s count, since shorter ones carry too few frames for a fair BER. There is no banner and no LED change: the link is up, just noisy.
- **Popover:** opens down from the summary (same shell as Presets). It holds the legend "RX OFFSET · LAST 24 H", the median at the current offset in words, and where the overs at other offsets point. Then comes a BER-against-offset chart: a dot per offset with 2 or more overs in the last 24 hours, a dashed parabola through the best three, and the suggestion marked with a white triangle and dotted rule. It also has "Try −525 Hz" (only when the suggestion is 50 Hz or more from the current offset), "Tune RX offset…", and a fine-print line saying each change restarts DMRHost. Both keys arm on the first tap ("Restart DMRHost?", danger ground, 4 s), because they cut any call in progress. Without the helper installed, the keys are replaced by a line pointing to install.sh.
- **Suggestion:** it comes from overs in the last 24 hours only, since older ones were made at another temperature. It needs 3 or more offsets, uses the vertex of the parabola through the best three when that falls inside them, and otherwise the best measured offset. It is rounded to 25 Hz.
- **Tuning run (LCD):** it takes over the plot area with a positive LCD (LCD ground, LCD ink, 6px corners).
  - Top left: a label ("Testing RX offset", "Switching to" or "Best RX offset") over the offset in the Callsign readout at 52px (42px on phones), with the sign set in sans because the readout subset has no minus.
  - Top right: "KEY UP 1/3" with three 14px pips, "Restarting…" while DMRHost comes back, or the best BER.
  - Middle: the same BER-against-offset chart in LCD inks (darkened amber #8a5208 and transmit ink for the cut-offs and dots), with a dashed "now" ring at the offset under test. Capped at 720px wide and centred.
  - Bottom: a hairline and a detail line with the start offset and BER, the best so far, and the step count.
  - Under the LCD: "Stop, back to −350 Hz" with a fine-print line while running; "Keep −575 Hz" and "Back to −350 Hz" when done.
- **Search (server):** measure 3 overs at the start offset, then go 150 Hz towards the better side while the BER improves. Once the best offset is bracketed, halve the step, stopping below 50 Hz or after 8 offsets. The run goes back to the start offset after 5 minutes without a key-up, and reports an error if DMRHost doesn't come back with the new offset within 45 s. Outcomes show as a slot-message line under the RF BER bar. Confirmations ("Tuning stopped; back to −350 Hz") fade after 8 s. Errors, and the note that a run timed out, stay until closed with a ✕ at the line's right.

### Live log
- A collapsible box under Last heard: a disclosure title "LIVE LOG" (chevron, remembered per browser) and a dim legend "DMRHost · held in RAM". The console is the page ground (darker than the box), 300px tall (240px under 820px), mono 12px, one grid row per line: time with milliseconds, level letter, message; long lines wrap rather than scroll sideways, and under 820px the level column drops.
- Colour follows meaning only: M plain legend, I and D dim, W BUSY amber, E and F fail text.
- It follows the newest line until the reader scrolls up; then it pauses and a "Jump to live" small key appears at the right of the bar. New lines are appended, never re-rendered, so a text selection survives updates. While collapsed it does not fetch.
- Source: a 400-line ring buffer in the dashboard process, fed by the volatile journal stream it already follows (`/api/log?after=N` returns only new lines). No file is written for it.

### PIN dialog
- Only when the config sets a `pin`. The first change the Pi refuses opens a modal `<dialog>` (panel-2 ground, rule-2 border, 10px radius, up to 320px wide, dimmed backdrop): the legend "PIN", a sans line "Changes on this hotspot need its dashboard PIN.", an LCD-style password field (mono 700 16px, wide tracking), then Cancel and Unlock small keys. Enter means Unlock, Escape or Cancel gives up and the slot message says the change needs the PIN.
- A wrong PIN reopens it with the line in fail text, "Wrong PIN. Try again.". A right one is kept for that browser tab (sessionStorage) and the change goes ahead without a second tap. Five wrong PINs in a minute lock changes for that minute.
- STATION in the (i) popover ends with "Dashboard  dmrdash 1.0.0 · PIN on" so bug reports name the build.

## Do's and Don'ts

### Do:
- **Do** keep the two VFO modules equal in size and treatment; neither timeslot is primary.
- **Do** keep readout windows as positive LCDs: lcd ground, lcd-ink characters.
- **Do** reserve red, green and amber for TX/fail, RX/healthy and BUSY/warn state.
- **Do** use the white 2px outline (2px offset) for focus and the legend tone for selection.
- **Do** spell talkgroup subscription types out in words, and keep the talkgroup list and editing behind the ⋯ key so the resting panel is just the LCD.
- **Do** set IDs, TG numbers, times and figures in mono with tabular numerals, and everything readable as language in the system sans.
- **Do** use engraved legends (600 11px uppercase, 0.12em) to label panel regions.

### Don't:
- **Don't** add glow, bevels, knobs, shadows or textures to evoke a radio; the panel is flat.
- **Don't** use amber for selection, focus, hover or emphasis.
- **Don't** set callsigns or prose in a segment face; the segment face is for the timer only.
- **Don't** ship a font subset under an OFL Reserved Font Name (use "VFO Timer", not "DSEG").
- **Don't** load external fonts or assets; faces are subset and inlined.
- **Don't** add a light theme without a new decision; the system is single dark by choice.
