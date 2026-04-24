# -*- coding: utf-8 -*-
"""
Download-Manager Screen fuer OeMediathek.
Zeigt den laufenden Download und die Warteschlange an.
Wird aus plugin.py geoeffnet; greift auf die globalen Queue-Variablen zu.
"""

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
            <eLabel position="40,667" size="8,40" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow"   position="56,660"  size="360,50"  font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="460,667" size="8,40" backgroundColor="#CC0000" zPosition="2" />
            <widget name="hint_red"      position="476,660" size="360,50"  font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <widget name="hint_exit"     position="880,660" size="280,50"  font="Regular;32" halign="right" valign="center" foregroundColor="#AAAAAA" transparent="1" />
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
            <eLabel position="27,455" size="5,27" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow"   position="38,452"  size="240,33"  font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <eLabel position="307,455" size="5,27" backgroundColor="#CC0000" zPosition="2" />
            <widget name="hint_red"      position="318,452" size="240,33"  font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" transparent="1" />
            <widget name="hint_exit"     position="587,452" size="186,33"  font="Regular;21" halign="right" valign="center" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""

    def __init__(self, session, active_downloader_ref, queue_ref):
        Screen.__init__(self, session)

        # Referenzen auf die globalen Objekte aus plugin.py
        self._get_active = active_downloader_ref
        self._get_queue  = queue_ref

        self["title_label"]    = Label(_b("Download-Manager"))
        self["active_head"]    = Label(_b("Laufender Download:"))
        self["active_label"]   = Label(_b(""))
        self["progress_label"] = Label(_b(""))
        self["queue_head"]     = Label(_b("Warteschlange:"))
        self["queue_label"]    = Label(_b(""))
        self["hint_yellow"]    = Label(_b("Alles abbrechen"))
        self["hint_red"]       = Label(_b("Aktuellen abbrechen"))
        self["hint_exit"]      = Label(_b("EXIT = Schliessen"))

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.close,
                "ok":     self.close,
                "yellow": self._cancel_all,
                "red":    self._cancel_current,
            },
            -1,
        )

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

    def _poll(self):
        try:
            active = self._get_active()
            queue  = self._get_queue()

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
                    if converting:
                        self["progress_label"].setText(_b("Konvertiere zu TS ..."))
                    else:
                        dl_bytes = active._downloaded if hasattr(active, "_downloaded") else 0
                        total    = active._total      if hasattr(active, "_total")      else 0
                        if total > 0:
                            pct = int(dl_bytes * 100 / total)
                            self["progress_label"].setText(_b("%d%% von %s" % (pct, format_size(total))))
                        elif dl_bytes > 0:
                            self["progress_label"].setText(_b("%s heruntergeladen" % format_size(dl_bytes)))
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
                    lines.append("%d. %s" % (i + 1, t))
                self["queue_label"].setText(_b("\n".join(lines)))
        except Exception:
            pass

    def _cancel_current(self):
        try:
            active = self._get_active()
            if active:
                active.cancel()
        except Exception:
            pass
        self.close()

    def _cancel_all(self):
        try:
            import sys
            _plugin = sys.modules.get("plugin")
            if _plugin is not None:
                _plugin._download_queue = []
                _plugin._active_downloader = None
            active = self._get_active()
            if active:
                active.cancel()
        except Exception:
            pass
        self.close()

    def doClose(self):
        self.__stop_timers()
        try:
            Screen.doClose(self)
        except TypeError:
            pass
