# -*- coding: utf-8 -*-
"""
Download-Manager Screen fuer OeMediathek.
Zeigt den laufenden Download und die Warteschlange an.
Wird aus plugin.py geoeffnet; greift auf die globalen Queue-Variablen zu.
"""

import time

from Screens.Screen import Screen
from Components.Label import Label
from Components.ActionMap import ActionMap
from enigma import eTimer, getDesktop
from downloader import format_size

try:
    IS_FHD = getDesktop(0).size().width() > 1280
except Exception:
    IS_FHD = True


def _b(s):
    if isinstance(s, bytes):
        return s
    return s.encode("utf-8", "replace")


class OeMediathekDownloadManagerScreen(Screen):
    if IS_FHD:
        skin = """
        <screen name="OeMediathekDownloadManagerScreen" position="360,200" size="1200,730" flags="wfNoBorder">
            <eLabel position="0,0" size="1200,730" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label"   position="40,30"  size="1120,60"  font="Regular;36" halign="center" foregroundColor="#FFFFFF" transparent="1" />
            <eLabel position="40,110" size="1120,2" backgroundColor="#44FFFFFF" zPosition="1" />
            <widget name="active_head"   position="40,128" size="1120,40"  font="Regular;28" halign="left"   foregroundColor="#AAAAAA" transparent="1" />
            <widget name="active_label"  position="40,175" size="1120,100" font="Regular;34" halign="left" valign="top" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="progress_label" position="40,285" size="1120,44" font="Regular;30" halign="left"   foregroundColor="#00BFFF" transparent="1" />
            <eLabel position="40,345" size="1120,2" backgroundColor="#44FFFFFF" zPosition="1" />
            <widget name="queue_head"    position="40,363" size="1120,40"  font="Regular;28" halign="left"   foregroundColor="#AAAAAA" transparent="1" />
            <widget name="queue_label"   position="40,410" size="1120,240" font="Regular;28" halign="left" valign="top" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="40,667" size="8,40" backgroundColor="#CC0000" zPosition="2" />
            <widget name="hint_red"      position="56,660"  size="280,50" font="Regular;30" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="340,667" size="8,40" backgroundColor="#00A000" zPosition="2" />
            <widget name="hint_green"    position="356,660" size="300,50"  font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="670,667" size="8,40" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow"   position="686,660" size="300,50"  font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <widget name="hint_exit"     position="1000,660" size="160,50" font="Regular;22" halign="right" valign="center" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""
    else:
        skin = """
        <screen name="OeMediathekDownloadManagerScreen" position="240,133" size="800,500" flags="wfNoBorder">
            <eLabel position="0,0" size="800,500" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label"   position="27,20"  size="746,40"  font="Regular;24" halign="center" foregroundColor="#FFFFFF" transparent="1" />
            <eLabel position="27,72" size="746,2" backgroundColor="#44FFFFFF" zPosition="1" />
            <widget name="active_head"   position="27,82"  size="746,28"  font="Regular;19" halign="left"   foregroundColor="#AAAAAA" transparent="1" />
            <widget name="active_label"  position="27,115" size="746,68"  font="Regular;22" halign="left" valign="top" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="progress_label" position="27,190" size="746,30"  font="Regular;20" halign="left"   foregroundColor="#00BFFF" transparent="1" />
            <eLabel position="27,230" size="746,2" backgroundColor="#44FFFFFF" zPosition="1" />
            <widget name="queue_head"    position="27,240" size="746,28"  font="Regular;19" halign="left"   foregroundColor="#AAAAAA" transparent="1" />
            <widget name="queue_label"   position="27,273" size="746,160" font="Regular;19" halign="left" valign="top" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="27,455" size="5,27" backgroundColor="#CC0000" zPosition="2" />
            <widget name="hint_red"      position="38,452"  size="190,33" font="Regular;18" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="225,455" size="5,27" backgroundColor="#00A000" zPosition="2" />
            <widget name="hint_green"    position="236,452" size="210,33"  font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="450,455" size="5,27" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow"   position="461,452" size="190,33"  font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <widget name="hint_exit"     position="660,452" size="113,33" font="Regular;14" halign="right" valign="center" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""

    def __init__(self, session, active_downloader_ref, queue_ref, cancel_all_fn, cancel_current_fn,
                 status_fn=None, retry_now_fn=None):
        Screen.__init__(self, session)

        # Referenzen auf die globalen Objekte/Funktionen aus plugin.py
        self._get_active     = active_downloader_ref
        self._get_queue      = queue_ref
        self._cancel_all_fn     = cancel_all_fn
        self._cancel_current_fn = cancel_current_fn
        self._status_fn      = status_fn
        self._retry_now_fn   = retry_now_fn

        self["title_label"]    = Label(_b("Download-Manager"))
        self["active_head"]    = Label(_b("Laufender Download:"))
        self["active_label"]   = Label(_b(""))
        self["progress_label"] = Label(_b(""))
        self["queue_head"]     = Label(_b("Warteschlange:"))
        self["queue_label"]    = Label(_b(""))
        self["hint_red"]       = Label(_b("Aktuellen abbrechen"))
        self["hint_green"]     = Label(_b("Alles abbrechen"))
        self["hint_yellow"]    = Label(_b(""))
        self["hint_exit"]      = Label(_b("EXIT = Schliessen"))

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.close,
                "ok":     self.close,
                "red":    self._cancel_current,
                "green":  self._cancel_all,
                "yellow": self._retry_now,
            },
            -1,
        )

        self._oem_rate_bytes = None
        self._oem_rate_time  = None
        self._oem_rate_bps   = 0.0

        self._poll_timer = eTimer()
        self._poll_timer.callback.append(self._poll)
        self._poll_timer.start(500, False)

        self.onClose.append(self.__stop_timers)
        self._poll()  # sofort befuellen

    def __stop_timers(self):
        try:
            self._poll_timer.stop()
        except Exception:
            pass

    def _oem_rate(self, downloaded):
        """Schaetzt die aktuelle Downloadgeschwindigkeit aus der Differenz
        zweier Poll-Zeitpunkte (min. 0.75s Abstand, um Rauschen durch den
        500ms-Poll-Takt zu glaetten)."""
        now = time.time()
        if self._oem_rate_bytes is None:
            self._oem_rate_bytes, self._oem_rate_time = downloaded, now
        else:
            elapsed = now - self._oem_rate_time
            delta = downloaded - self._oem_rate_bytes
            if elapsed >= 0.75:
                if delta >= 0:
                    self._oem_rate_bps = float(delta) / elapsed
                self._oem_rate_bytes, self._oem_rate_time = downloaded, now
        if self._oem_rate_bps >= 1024 * 1024:
            return "  |  %.2f MB/s" % (self._oem_rate_bps / (1024.0 * 1024.0))
        if self._oem_rate_bps >= 1024:
            return "  |  %.0f KB/s" % (self._oem_rate_bps / 1024.0)
        return "  |  %.0f B/s" % self._oem_rate_bps

    def _oem_queue_summary(self, queue):
        """Baut die Kopfzeile der Warteschlange: Anzahl + Gesamtgroesse aller
        Eintraege, deren Groesse bereits ermittelt wurde (siehe
        _oem_queue_size_probe in plugin.py); unbekannte Groessen (v.a.
        HLS/m3u8) werden separat ausgewiesen statt geschaetzt."""
        known_total = 0
        unknown = 0
        for item in queue:
            if not item.get("_oem_size_ready", False):
                unknown += 1
                continue
            size = int(item.get("_oem_size", 0) or 0)
            if size > 0:
                known_total += size
            else:
                unknown += 1
        if known_total > 0:
            total_text = format_size(known_total)
        elif unknown:
            total_text = "unbekannt"
        else:
            total_text = "0 MB"
        if unknown:
            total_text += " + %d unbekannt" % unknown
        return "%d Datei(en) | Gesamt: %s" % (len(queue), total_text)

    def _poll(self):
        try:
            active = self._get_active()
            queue  = self._get_queue()
            status = self._status_fn() if self._status_fn else {}

            queue_summary = self._oem_queue_summary(queue)
            if status.get("waiting"):
                remaining = max(0, int(status.get("until", 0) - time.time() + 0.999))
                self["queue_head"].setText(_b("Warteschlange:  |  %s  |  Retry in %d Sek. (Versuch %d)" %
                                               (queue_summary, remaining, status.get("attempt", 0))))
                self["hint_yellow"].setText(_b("Jetzt erneut versuchen"))
            else:
                message = status.get("message", "")
                suffix = ("  |  " + message) if message else ""
                self["queue_head"].setText(_b("Warteschlange:  |  %s%s" % (queue_summary, suffix)))
                self["hint_yellow"].setText(_b(""))

            if active is None:
                self["active_label"].setText(_b("Kein aktiver Download"))
                self["progress_label"].setText(_b(""))
            else:
                title = active.title
                if isinstance(title, bytes):
                    title = title.decode("utf-8", "replace")
                self["active_label"].setText(_b(title))

                # Fortschritt aus dem Downloader lesen (thread-safe: nur lesen)
                try:
                    converting = getattr(active, "_converting", False)
                    muxing     = getattr(active, "_muxing", False)
                    if converting:
                        self["progress_label"].setText(_b("Konvertiere zu TS ..."))
                    elif muxing:
                        self["progress_label"].setText(_b("Verbinde Video & Audio ..."))
                    else:
                        dl_bytes   = active._downloaded if hasattr(active, "_downloaded") else 0
                        total      = active._total      if hasattr(active, "_total")      else 0
                        segs_done  = getattr(active, "_segs_done", 0)
                        total_segs = getattr(active, "_total_segs", 0)
                        rate       = self._oem_rate(dl_bytes)
                        if total_segs > 0:
                            pct = int(segs_done * 100 / total_segs)
                            self["progress_label"].setText(_b("%d%% (%s)%s" % (pct, format_size(dl_bytes), rate)))
                        elif total > 0:
                            pct = int(dl_bytes * 100 / total)
                            self["progress_label"].setText(_b("%d%% von %s%s" % (pct, format_size(total), rate)))
                        elif dl_bytes > 0:
                            self["progress_label"].setText(_b("%s heruntergeladen%s" % (format_size(dl_bytes), rate)))
                        else:
                            self["progress_label"].setText(_b("Starte ..."))
                except Exception:
                    self["progress_label"].setText(_b(""))

            if not queue:
                self["queue_label"].setText(_b("(leer)"))
            else:
                lines = []
                for i, item in enumerate(queue):
                    t = item.get("title", b"")
                    if isinstance(t, bytes):
                        t = t.decode("utf-8", "replace")
                    if not item.get("_oem_size_ready", False):
                        size_text = "Groesse wird ermittelt ..."
                    else:
                        size = int(item.get("_oem_size", 0) or 0)
                        size_text = format_size(size) if size > 0 else "Groesse unbekannt"
                    lines.append("%d. %s (%s)" % (i + 1, t, size_text))
                self["queue_label"].setText(_b("\n".join(lines)))
        except Exception:
            pass

    def _retry_now(self):
        try:
            if self._retry_now_fn:
                self._retry_now_fn()
        except Exception:
            pass

    def _cancel_current(self):
        has_queued = bool(self._get_queue())
        try:
            self._cancel_current_fn()
        except Exception:
            pass
        if not has_queued:
            self.close()

    def _cancel_all(self):
        try:
            self._cancel_all_fn()
        except Exception:
            pass
        self.close()

    def doClose(self):
        self.__stop_timers()
        try:
            Screen.doClose(self)
        except TypeError:
            pass
