# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
One person: the hotspot owner, amateur radio operator CT7BLE, in Portugal. They are an experienced operator and are technically comfortable (they SSH into the Pi). They use the dashboard on a phone and on a desktop about equally, often while operating the radio. They're the only user; no club or public audience.

## Product Purpose
dmrdash is a LAN dashboard for a personal BrandMeister DMR hotspot. It is a Raspberry Pi with an MMDVM_HS duplex hat running DMRHost, hotspot ID 268051301. It does three jobs with equal weight:
- Show who is talking right now on each timeslot, plus recent activity.
- Confirm the hotspot is healthy: DMRHost, modem, BrandMeister link.
- Manage BrandMeister talkgroups: add or remove static TGs, drop dynamic TGs, stop a call.

Success means: one glance answers "is it working and who's on", and talkgroup changes take seconds without opening BrandMeister SelfCare.

## Operating Context
- Typical moments: a quick look while the radio is in hand, a browser tab left open on a desktop, or changing talkgroups before a net.
- The DMR vocabulary is standard and expected: timeslot (TS1/TS2), talkgroup (TG), static/dynamic/timed subscriptions, RF vs network, BER, colour code, talker alias, master.
- The hotspot runs duplex, so both timeslots are live.

## Capabilities and Constraints
- Served by a single stdlib Python process on a 512 MB Pi Zero 2-class board (~26 MB RSS). The page is one self-contained HTML file with no build step and no external assets/CDNs (LAN, possibly offline from the internet).
- Live data comes from the DMRHost journal (calls, durations, BER, packet loss, talker alias, modem and network status). BrandMeister API v2 provides device status, static/dynamic/timed/auto-static TGs, and TG names. The page polls every 2 s.
- Writes go through the server with the SelfCare API key held server-side.
- History is in memory only, from the current boot; the journal is volatile to spare the SD card.
- Not yet available: RSSI (the MMDVM_HS doesn't report it), location, and per-call audio.

## Brand Commitments
None. It's a personal tool with no identity to match.

## Evidence on Hand
Real live data on the running hotspot: TG 91 World-wide and TG 268 Portugal traffic, Portuguese D-Star/C4FM bridge TGs (268912/268913), radioid.net callsign and name lookups.

## Product Principles
- The glance comes first: state and the current call must be readable in under a second, at arm's length on a phone.
- Health problems must be loud, and normal must be quiet.
- Controls must be safe and reversible: show what changed, and confirm anything that interrupts a QSO.
- Speak the operator's language. Never simplify DMR terms away.
