#!/usr/bin/env python3
"""dmrdash-tui - terminal view of a dmrdash server.

    dmrdash-tui                                  on the Pi, over SSH
    dmrdash-tui --url http://dmrhost.local:8080  from another machine

Polls the same /api/state and /api/log the web page uses, so both show the same calls,
talkgroups and log. Read-only. Standard library only (curses).

https://github.com/jcalado/dmrdash - MIT licence
"""

import argparse
import collections
import curses
import json
import locale
import os
import threading
import time
import urllib.error
import urllib.request

POLL = 1.0           # seconds between state fetches, like the page
OFFLINE_AFTER = 10   # no good fetch for this long: say so, and keep showing the last state
BER_WARN, BER_BAD = 2, 5  # the web page's cut-offs
MIN_W, MIN_H = 60, 16


# --- data --------------------------------------------------------------------

class Feed:
    """Fetches state and new log lines in the background. Everything the screen reads is swapped in under one lock."""

    def __init__(self, url):
        self.url = url.rstrip("/")
        self.lock = threading.Lock()
        self.state = None
        self.skew = 0.0       # server clock minus ours, so live durations count from the Pi's timestamps
        self.last_ok = 0.0
        self.error = None
        self.log = collections.deque(maxlen=400)
        self.log_last = 0

    def get(self, path):
        req = urllib.request.Request(self.url + path, headers={"User-Agent": "dmrdash-tui"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def run(self):
        while True:
            try:
                state = self.get("/api/state")
                log = self.get("/api/log?after=%d" % self.log_last)
                with self.lock:
                    if log["last"] < self.log_last:  # the server restarted and its line numbers began again
                        self.log.clear()
                    self.log.extend(log["lines"])
                    self.log_last = log["last"]
                    self.state = state
                    self.skew = state["now"] - time.time()
                    self.last_ok = time.time()
                    self.error = None
            except urllib.error.HTTPError as e:
                try:
                    msg = json.load(e).get("error") or e.reason
                except ValueError:
                    msg = e.reason
                with self.lock:
                    self.error = "HTTP %d: %s" % (e.code, msg)
            except (OSError, ValueError, KeyError) as e:
                with self.lock:
                    self.error = str(getattr(e, "reason", None) or e)
            time.sleep(POLL)


# --- formatting (ports of the page's helpers) --------------------------------

def hhmmss(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def ago(s):
    s = max(0, s)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%dh %dm" % (s // 3600, s % 3600 // 60)
    return "%dd %dh" % (s // 86400, s % 86400 // 3600)


def clock_dur(s):
    return "%d:%02d" % (s // 60, s % 60)


def log_dur(s):
    return "%.1fs" % s if s < 60 else "%dm%02d" % (s // 60, round(s % 60))


def call_of(w):
    return str((w or {}).get("callsign") or (w or {}).get("id") or "?")


def fit(text, width):
    """Cut text to width, marking the cut."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[:width - 1] + G["more"]


# Box drawing, with ASCII stand-ins for terminals that are not UTF-8.
UNICODE = {"h": "─", "v": "│", "tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "dot": "●", "live": "▶",
           "sel": "▸", "sep": "·", "more": "…", "arrow": "→"}
PLAIN = {"h": "-", "v": "|", "tl": "+", "tr": "+", "bl": "+", "br": "+", "dot": "*", "live": ">",
         "sel": ">", "sep": "|", "more": "~", "arrow": ">"}
G = UNICODE


# --- screen ------------------------------------------------------------------

class Screen:
    def __init__(self, scr, feed):
        self.scr = scr
        self.feed = feed
        self.view = "heard"        # or "log"
        self.sel = 0               # selected Last heard row
        self.sel_key = None        # (ts, slot) of that row, so the selection stays put as new calls push it down
        self.top = 0
        self.log_back = 0          # log lines scrolled up from the newest
        self.filter = None         # ("call", "CT7BLE") or ("tg", "268")
        self.rows_shown = 0
        self.c = {}
        self.colours()

    def colours(self):
        names = ["red", "green", "yellow", "cyan", "magenta"]
        self.c = {n: 0 for n in names}
        if not curses.has_colors():
            self.c["red"] = curses.A_BOLD
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        for i, n in enumerate(names, 1):
            curses.init_pair(i, getattr(curses, "COLOR_" + n.upper()), bg)
            self.c[n] = curses.color_pair(i)

    # drawing primitives: anything past the edge is dropped, never raised
    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        text = text[:w - x]
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass  # writing the bottom-right cell raises after drawing it

    def box(self, y, x, h, w, title, attr):
        self.put(y, x, G["tl"] + G["h"] * (w - 2) + G["tr"], attr)
        self.put(y, x + 1, title, attr | curses.A_BOLD)
        for i in range(1, h - 1):
            self.put(y + i, x, G["v"], attr)
            self.put(y + i, x + w - 1, G["v"], attr)
        self.put(y + h - 1, x, G["bl"] + G["h"] * (w - 2) + G["br"], attr)

    def rule(self, y, label, right=""):
        w = self.scr.getmaxyx()[1]
        line = " " + label + " " + G["h"] * w
        self.put(y, 0, line[:w - len(right) - 2], self.c["cyan"])
        self.put(y, 1, label, self.c["cyan"] | curses.A_BOLD)
        if right:
            self.put(y, w - len(right) - 1, right, curses.A_DIM)

    # --- state helpers ---
    def now(self):
        return time.time() + self.feed.skew

    def tg_name(self, s, tg):
        return s.get("tg_names", {}).get(str(tg), "")

    def health(self, s):
        """Status lights and the one alarm worth a banner, as the page works them out."""
        offline = self.feed.last_ok and time.time() - self.feed.last_ok > OFFLINE_AFTER
        host, modem, net = s["host"]["status"], s["modem"]["status"], s["network"]["status"]
        master = (s["bm"].get("device") or {}).get("lastKnownMaster") or "link"
        leds = [
            ["host", "on" if host == "running" else "warn" if host == "starting" else "fail"],
            ["modem", "on" if modem == "ok" else "warn" if modem == "opening" else "fail"],
            ["BM %s" % master, "on" if net == "connected" else "warn" if net in ("connecting", "unknown") else "fail"],
        ]
        alarm = None
        if offline:
            alarm = "Can't reach %s %s last update %s ago, showing the last known state" % (
                self.feed.url, G["sep"], ago(time.time() - self.feed.last_ok))
            for led in leds:
                led[1] = "warn"
        elif host == "stopped":
            alarm = "DMRHost is stopped %s check it with: systemctl status dmrhost" % G["sep"]
        elif modem == "error":
            alarm = "Modem problem %s DMRHost reported a modem error, RF may not work" % G["sep"]
        elif net in ("login failed", "reconnecting", "down", "master closing"):
            alarm = ("BrandMeister login failed %s the master rejected the hotspot password" if net == "login failed"
                     else "BrandMeister link lost %s RF still works locally, nothing reaches the network") % G["sep"]
        return leds, alarm

    def subs(self, s, slot):
        bm = s["bm"]
        on = lambda t: int(t.get("slot") or 0) == slot  # noqa: E731
        return ([t for t in bm["static"] if on(t)], [t for t in bm["dynamic"] if on(t)], [t for t in bm["timed"] if on(t)])

    # --- sections ---
    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        with self.feed.lock:
            s, err = self.feed.state, self.feed.error
            log = list(self.feed.log)
        if w < MIN_W or h < MIN_H:
            self.put(0, 0, "Terminal too small: need %dx%d, have %dx%d" % (MIN_W, MIN_H, w, h))
            return
        if s is None:
            self.put(h // 2 - 1, 2, "Connecting to %s %s" % (self.feed.url, G["more"]), curses.A_BOLD)
            if err:
                self.put(h // 2 + 1, 2, err, self.c["red"])
            self.put(h - 1, 1, "q quit", curses.A_DIM)
            return
        leds, alarm = self.health(s)
        y = self.header(s, leds)
        if alarm:
            self.put(y, 0, (" " + alarm).ljust(w), self.c["yellow"] | curses.A_REVERSE)
            y += 1
        self.slot_box(s, 1, y, 0, w // 2)
        self.slot_box(s, 2, y, w // 2, w - w // 2)
        y = self.talkgroups(s, y + 5)
        if self.view == "log":
            self.log_view(log, y, h - 1)
        else:
            self.heard(s, y, h - 1)
        self.footer(h - 1)

    def header(self, s, leds):
        w = self.scr.getmaxyx()[1]
        dev = s["bm"].get("device") or {}
        left = " %s %s %s" % (dev.get("callsign") or s["hostname"], G["sep"], s["device_id"])
        self.put(0, 0, left, curses.A_BOLD)
        x = len(left) + 3
        for label, st in leds:
            colour = {"on": "green", "warn": "yellow", "fail": "red"}[st]
            self.put(0, x, G["dot"], self.c[colour] | curses.A_BOLD)
            self.put(0, x + 2, label)
            x += len(label) + 4
        right = hhmmss(self.now()) + " "
        try:
            freqs = "%.3f/%.3f" % (float(dev["rx"]), float(dev["tx"]))
        except (KeyError, TypeError, ValueError):
            freqs = ""
        if freqs and x + len(freqs) + len(right) + 3 <= w:
            right = freqs + "   " + right
        self.put(0, w - len(right), right, curses.A_DIM)
        return 1

    def slot_box(self, s, n, y, x, bw):
        slot = s["slots"][str(n)]
        live = slot.get("active")
        attr = self.c["red"] | curses.A_BOLD if live else curses.A_DIM
        self.box(y, x, 5, bw, " TS%d " % n, attr)
        inner = bw - 4
        lines = []
        if live:
            w = slot.get("who") or {"id": slot.get("src")}
            src = "RF" if slot.get("source") == "rf" else "NET"
            lines.append([(G["live"] + " ", self.c["red"] | curses.A_BOLD), (call_of(w), curses.A_BOLD),
                          ("  " + (w.get("name") or ""), 0)])
            lines[-1].append((src, (self.c["red"] if src == "RF" else 0) | curses.A_BOLD, "right"))
            lines.append(self.target(s, slot))
            alias = slot.get("alias")
            lines.append([(clock_dur(max(0, self.now() - (slot.get("start") or self.now()))), self.c["red"]),
                          (("   “%s”" % alias) if alias else "", curses.A_DIM)])
        else:
            last = next((c for c in s["lastheard"] if c["slot"] == n), None)
            st, dy, ti = self.subs(s, n)
            if last:
                lines.append([("idle", curses.A_DIM), ("last " + ago(self.now() - last["ts"]) + " ago", curses.A_DIM, "right")])
                lines.append([(call_of(last.get("who")) + " ", curses.A_BOLD)] + self.target(s, last, arrow=True))
            else:
                lines.append([("idle", curses.A_DIM)])
                lines.append([("listening, no calls yet on this slot", curses.A_DIM)])
            counts = "%d static %s %d dynamic" % (len(st), G["sep"], len(dy))
            if ti:
                counts += " %s %d scheduled" % (G["sep"], len({t.get("talkgroup") for t in ti}))
            lines.append([(counts, curses.A_DIM)])
        for i, parts in enumerate(lines):
            cx = x + 2
            for part in parts:
                text, a = part[0], part[1]
                if len(part) > 2:  # right-aligned in the box
                    self.put(y + 1 + i, x + bw - 2 - len(text), text, a)
                    continue
                text = fit(text, x + 2 + inner - cx)
                self.put(y + 1 + i, cx, text, a)
                cx += len(text)

    def target(self, s, c, arrow=False):
        """Where a call went: 'TG 268  Portugal', or 'PC CT1ABC (Name)' for a private call."""
        lead = (G["arrow"] + " ") if arrow else ""
        if c.get("group"):
            return [(lead + "TG " + str(c["dst"]), curses.A_BOLD), ("  " + self.tg_name(s, c["dst"]), 0)]
        to = c.get("to") or {}
        name = (" (%s)" % to["name"]) if to.get("name") else ""
        return [(lead + "PC ", 0), (str(to.get("callsign") or c["dst"]), curses.A_BOLD), (name, 0)]

    def talkgroups(self, s, y):
        w = self.scr.getmaxyx()[1]
        col = (w - 10) // 2
        rows = [("static", 0), ("dynamic", 1)]
        if s["bm"]["timed"]:
            rows.append(("sched", 2))
        if s["bm"].get("error"):
            self.put(y, 1, fit("BrandMeister: " + s["bm"]["error"], w - 2), self.c["yellow"])
            return y + 2
        for label, k in rows:
            self.put(y, 1, label, curses.A_DIM)
            for n in (1, 2):
                items = self.subs(s, n)[k]
                if k == 1:
                    text = [self.tg_label(s, t["talkgroup"]) + self.dyn_left(t) for t in items]
                else:
                    seen = []
                    for t in items:  # a schedule is several records for one TG
                        if t["talkgroup"] not in seen:
                            seen.append(t["talkgroup"])
                    text = [self.tg_label(s, tg) for tg in seen]
                x = 9 + (n - 1) * (col + 1)
                self.put(y, x, "TS%d " % n, curses.A_DIM)
                self.put(y, x + 4, fit((" " + G["sep"] + " ").join(text) or "-", col - 5))
            y += 1
        return y

    def tg_label(self, s, tg):
        name = self.tg_name(s, tg)
        return "%s %s" % (tg, name) if name else str(tg)

    def dyn_left(self, d):
        if not d.get("timeout"):
            return ""
        left = -(-(d["timeout"] - self.now()) // 60)
        return " (%dm)" % left if left > 0 else " (ending)"

    # --- last heard ---
    def matched(self, s):
        calls = s["lastheard"]
        if self.filter:
            k, v = self.filter
            calls = [c for c in calls if (call_of(c.get("who")) == v if k == "call" else c.get("group") and str(c["dst"]) == v)]
        return calls

    def columns(self, w):
        """(title, width, align) for the columns that fit, TALKGROUP taking what is left."""
        cols = [("TIME", 8, "<"), ("TS", 2, ">"), ("SRC", 3, "<"), ("CALL", 8, "<"), ("NAME", 9, "<"),
                ("TG", 7, "<"), ("TALKGROUP", 0, "<"), ("DUR", 6, ">"), ("BER", 5, ">"), ("LOSS", 4, ">")]
        if w < 96:
            cols = [c for c in cols if c[0] != "LOSS"]
        if w < 72:
            cols = [c for c in cols if c[0] != "NAME"]
        used = 3 + sum(c[1] + 2 for c in cols)
        return [(t, cw or max(6, w - used), a) for t, cw, a in cols]

    def cells(self, s, c):
        """Text and colour for each column of one call."""
        w = c.get("who") or {}
        rf = c.get("source") == "rf"
        if c.get("group"):
            tg, tgname = str(c["dst"]), self.tg_name(s, c["dst"])
        else:
            to = c.get("to") or {}
            tg = "PC " + str(c["dst"])
            tgname = (to.get("callsign") or "") + ((" (%s)" % to["name"]) if to.get("name") else "")
        ber = c.get("ber")
        ber_attr = self.c["red"] if ber is not None and ber >= BER_BAD else self.c["yellow"] if ber is not None and ber >= BER_WARN else 0
        dur = (log_dur(c["duration"]) if c.get("duration") is not None else "") + ("*" if c.get("lost") else "")
        return {
            "TIME": (hhmmss(c["ts"]), 0),
            "TS": (str(c["slot"]), 0),
            "SRC": ("RF" if rf else "NET", self.c["red"] if rf else curses.A_DIM),
            "CALL": (call_of(w), curses.A_BOLD),
            "NAME": (w.get("name") or "", 0),
            "TG": (tg, 0),
            "TALKGROUP": (tgname, curses.A_DIM),
            "DUR": (dur, self.c["yellow"] if c.get("lost") else 0),
            "BER": ("%.1f%%" % ber if ber is not None else "", ber_attr),
            "LOSS": ("%d%%" % c["loss"] if c.get("loss") is not None else "", 0),
        }

    def heard(self, s, y, bottom):
        w = self.scr.getmaxyx()[1]
        calls = self.matched(s)
        right = "%d of %d" % (len(calls), len(s["lastheard"]))
        if self.filter:
            k, v = self.filter
            right = "%s %s  x clear  %s %s" % ("call" if k == "call" else "TG", v, G["sep"], right)
        self.rule(y, "LAST HEARD", right)
        cols = self.columns(w)
        x = 3
        for title, cw, a in cols:
            self.put(y + 1, x, title.rjust(cw) if a == ">" else title, curses.A_DIM | curses.A_BOLD)
            x += cw + 2
        y += 2
        self.rows_shown = room = max(1, bottom - y)
        mine = int(s["device_id"]) // 100
        # keep the selection on the same call as new ones arrive above it
        if self.sel_key:
            for i, c in enumerate(calls):
                if (c["ts"], c["slot"]) == self.sel_key:
                    self.sel = i
                    break
        self.sel = max(0, min(self.sel, len(calls) - 1))
        self.sel_key = (calls[self.sel]["ts"], calls[self.sel]["slot"]) if calls and self.sel else None
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + room:
            self.top = self.sel - room + 1
        self.top = max(0, min(self.top, max(0, len(calls) - room)))
        if not calls:
            self.put(y + 1, 3, "No calls match this filter." if self.filter else "No calls yet.", curses.A_DIM)
        for i, c in enumerate(calls[self.top:self.top + room]):
            row = self.top + i
            selected = row == self.sel
            own = (c.get("who") or {}).get("id") == mine
            base = curses.A_REVERSE if selected else 0
            if own:
                base |= curses.A_BOLD
            if selected:
                self.put(y + i, 0, " " * w, base)
                self.put(y + i, 1, G["sel"], base)
            x = 3
            cells = self.cells(s, c)
            for title, cw, a in cols:
                text, attr = cells[title]
                text = fit(text, cw)
                self.put(y + i, x, text.rjust(cw) if a == ">" else text, attr | base)
                x += cw + 2

    def log_view(self, log, y, bottom):
        w = self.scr.getmaxyx()[1]
        self.rule(y, "LIVE LOG", "%d lines" % len(log))
        y += 1
        room = max(1, bottom - y)
        self.log_back = max(0, min(self.log_back, len(log) - room))
        end = len(log) - self.log_back
        level = {"E": self.c["red"], "F": self.c["red"] | curses.A_BOLD, "W": self.c["yellow"], "D": curses.A_DIM}
        for i, line in enumerate(log[max(0, end - room):end]):
            self.put(y + i, 1, hhmmss(line["ts"]), curses.A_DIM)
            self.put(y + i, 11, fit(line["t"], w - 12), level.get(line["l"], 0))

    def footer(self, y):
        if self.view == "log":
            keys = "q quit  %s scroll  l last heard" % ("↑↓" if G is UNICODE else "up/down")
        else:
            keys = "q quit  %s select  f filter call  g filter TG  x clear  l log" % ("↑↓" if G is UNICODE else "up/down")
        self.put(y, 1, keys, curses.A_DIM)

    # --- keys ---
    def key(self, k):
        if k in (ord("q"), ord("Q")):
            return False
        if k == ord("l"):
            self.view = "log" if self.view == "heard" else "heard"
            self.log_back = 0
            return True
        step = {curses.KEY_UP: -1, ord("k"): -1, curses.KEY_DOWN: 1, ord("j"): 1,
                curses.KEY_PPAGE: -self.rows_shown, curses.KEY_NPAGE: self.rows_shown}.get(k)
        if self.view == "log":
            if step:
                self.log_back -= step
            elif k == curses.KEY_HOME:
                self.log_back = 10 ** 6
            elif k == curses.KEY_END:
                self.log_back = 0
            return True
        if step:
            self.sel = max(0, self.sel + step)
            self.sel_key = None
        elif k == curses.KEY_HOME:
            self.sel, self.sel_key = 0, None
        elif k in (ord("x"), 27):
            self.filter = None
        elif k in (ord("f"), ord("g")):
            with self.feed.lock:
                calls = self.matched(self.feed.state) if self.feed.state else []
            if calls:
                c = calls[min(self.sel, len(calls) - 1)]
                want = ("call", call_of(c.get("who"))) if k == ord("f") else ("tg", str(c["dst"])) if c.get("group") else None
                if want:
                    self.filter = None if self.filter == want else want
            self.sel, self.sel_key, self.top = 0, None, 0
        return True


def run(scr, feed):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    scr.timeout(250)  # redraw four times a second, so clocks and live durations tick
    screen = Screen(scr, feed)
    while True:
        screen.draw()
        scr.refresh()
        k = scr.getch()
        if k != -1 and not screen.key(k):
            return


def main():
    global G
    ap = argparse.ArgumentParser(description="Terminal view of a dmrdash server (read-only).")
    ap.add_argument("--url", default=os.environ.get("DMRDASH_URL", "http://localhost:8080"),
                    help="dmrdash address (default %(default)s, or $DMRDASH_URL)")
    ap.add_argument("--ascii", action="store_true", help="draw with plain ASCII instead of box characters")
    args = ap.parse_args()
    locale.setlocale(locale.LC_ALL, "")
    if args.ascii or "utf" not in (locale.getpreferredencoding(False) or "").lower():
        G = PLAIN
    os.environ.setdefault("ESCDELAY", "25")  # Esc clears the filter without curses' one-second wait
    feed = Feed(args.url)
    threading.Thread(target=feed.run, daemon=True).start()
    try:
        curses.wrapper(run, feed)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
