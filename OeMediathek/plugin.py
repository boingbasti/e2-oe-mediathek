# -*- coding: utf-8 -*-
# plugin.py

import os
import threading

try:
    import traceback
    def _fmt_exc():
        return traceback.format_exc()
except ImportError:
    def _fmt_exc():
        return "(traceback nicht verfügbar)"

from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.VirtualKeyBoard import VirtualKeyBoard
from Screens.ChoiceBox import ChoiceBox
from Components.ActionMap import ActionMap
from Components.MenuList import MenuList
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel
from enigma import eTimer, ePoint, getDesktop

try:
    from Components.Pixmap import Pixmap as _Pixmap
except ImportError:
    _Pixmap = None

try:
    from Tools.LoadPixmap import LoadPixmap as _LoadPixmap
except ImportError:
    _LoadPixmap = None

from mediathek import (
    get_all_highlights,
    get_ard_highlights,
    get_zdf_highlights,
    get_arte_highlights,
    get_3sat_highlights,
    get_ndr_highlights,
    get_wdr_highlights,
    get_br_highlights,
    get_mdr_highlights,
    get_hr_highlights,
    get_swr_highlights,
    get_rbb_highlights,
    get_sr_highlights,
    get_zdfinfo_highlights,
    get_zdfneo_highlights,
    get_kika_highlights,
    get_phoenix_highlights,
    get_radio_bremen_highlights,
    get_funk_highlights,
    get_ard_alpha_highlights,
    get_one_highlights,
    get_tagesschau24_highlights,
    get_dw_highlights,
    get_orf_highlights,
    get_srf_highlights,
    get_favorites,
    add_favorite,
    remove_favorite,
    is_favorite,
    reorder_favorites,
    is_watched,
    toggle_watched,
    is_episode_favorite,
    add_episode_favorite,
    remove_episode_favorite,
    get_episode_favorites,
    _mvw_query,
    load_search_history,
    save_search_history,
)
from player import play_stream
from downloader import Downloader, get_save_dir, set_save_dir, get_content_length, format_size, get_auto_convert, set_auto_convert, convert_mp4_to_ts, get_tile_wrap_lr, set_tile_wrap_lr
from download_manager import OeMediathekDownloadManagerScreen

LOGO_DIR = os.path.join(os.path.dirname(__file__), "logos")
LOG_FILE = "/tmp/oemediathek.log"
PAGE_SIZE = 100
DEBUG = False

try:
    import xml.etree.ElementTree as _ET
    _meta = _ET.parse(os.path.join(os.path.dirname(__file__), "meta.xml"))
    PLUGIN_VERSION = _meta.findtext("version") or "?"
except Exception:
    PLUGIN_VERSION = "?"


# Download-Queue: aktiver Downloader, wartende Items, ausstehende Benachrichtigung
_active_downloader  = None
_download_queue     = []    # Liste von {"title": ..., "url": ..., "topic": ...}
_bg_download_result = None  # None | "ok" | "err:<meldung>"

# Auflösungs-Weiche: True = FHD (1920×1080), False = HD (1280×720)
try:
    IS_FHD = getDesktop(0).size().width() > 1280
except Exception:
    IS_FHD = True


def _log(msg):
    if not DEBUG:
        return
    line = "[OeMediathek] " + str(msg)
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _b(val):
    """Gibt val als Byte-String zurück (Python 2 / Enigma2)."""
    if isinstance(val, bytes):
        return val
    try:
        return val.encode("utf-8")
    except Exception:
        return str(val)


SOURCES = [
    # Seite 1
    ("Meine Favoriten",  get_favorites,          "favorites.png"),
    ("Alle Mediatheken", get_all_highlights,     "alle.png"),
    ("ARD Mediathek",    get_ard_highlights,     "ard.png"),
    ("ZDF Mediathek",    get_zdf_highlights,     "zdf.png"),
    ("Arte",             get_arte_highlights,    "arte.png"),
    ("3sat",             get_3sat_highlights,    "3sat.png"),
    ("NDR Mediathek",    get_ndr_highlights,     "ndr.png"),
    ("WDR Mediathek",    get_wdr_highlights,     "wdr.png"),
    ("BR Mediathek",     get_br_highlights,      "br.png"),
    # Seite 2
    ("MDR Mediathek",    get_mdr_highlights,     "mdr.png"),
    ("HR Mediathek",     get_hr_highlights,      "hr.png"),
    ("SWR Mediathek",    get_swr_highlights,     "swr.png"),
    ("rbb Mediathek",    get_rbb_highlights,     "rbb.png"),
    ("SR Mediathek",     get_sr_highlights,      "sr.png"),
    ("ZDF Info",         get_zdfinfo_highlights, "zdfinfo.png"),
    ("ZDF Neo",          get_zdfneo_highlights,  "zdfneo.png"),
    ("KiKA",             get_kika_highlights,    "kika.png"),
    ("Phoenix",          get_phoenix_highlights, "phoenix.png"),
    # Seite 2 (Forts.)
    ("Radio Bremen",     get_radio_bremen_highlights, "radio_bremen.png"),
    ("funk",             get_funk_highlights,         "funk.png"),
    ("ARD alpha",        get_ard_alpha_highlights,    "ard_alpha.png"),
    ("ONE",              get_one_highlights,          "one.png"),
    ("tagesschau24",     get_tagesschau24_highlights, "tagesschau24.png"),
    ("DW",               get_dw_highlights,           "dw.png"),
    # Seite 3
    ("ORF",              get_orf_highlights,          "orf.png"),
    ("SRF",              get_srf_highlights,          "srf.png"),
]
# Unveränderliche Kopie der Original-Reihenfolge für den Werksreset
_SOURCES_DEFAULT = list(SOURCES)

# Kachel-Layout 4×3 (vertikal zentriert zwischen Titel und Legende)
TILE_COLS = 4
TILE_ROWS = 3
TILES_PER_PAGE = TILE_COLS * TILE_ROWS  # 12
if IS_FHD:
    TILE_W, TILE_H = 450, 180
    _TX = [30, 500, 970, 1440]
    _TY = [245, 445, 645]
else:
    TILE_W, TILE_H = 290, 112
    _TX = [30, 340, 650, 960]
    _TY = [163, 295, 427]
TILE_POSITIONS = [(_TX[c], _TY[r]) for r in range(TILE_ROWS) for c in range(TILE_COLS)]

# Sender -> API-Kanalname (vollstaendig, inkl. nicht im Hauptmenu vertretener Sender)
CHANNEL_MAP = {
    "ARD Mediathek": "ARD",
    "ZDF Mediathek": "ZDF",
    "Arte":          "ARTE",
    "3sat":          "3Sat",
    "NDR Mediathek": "NDR",
    "WDR Mediathek": "WDR",
    "BR Mediathek":  "BR",
    "MDR Mediathek": "MDR",
    "HR Mediathek":  "HR",
    "SWR Mediathek": "SWR",
    "rbb Mediathek": "RBB",
    "SR Mediathek":  "SR",
    "ZDF Info":      "ZDFinfo",
    "ZDF Neo":       "ZDFneo",
    "KiKA":          "KiKA",
    "Phoenix":       "PHOENIX",
    "Radio Bremen":  "Radio Bremen TV",
    "funk":          "Funk.net",
    "ARD alpha":     "ARD-alpha",
    "ONE":           "ONE",
    "tagesschau24":  "tagesschau24",
    "DW":            "DW",
}

MODE_GROUPS   = 0
MODE_EPISODES = 1

# Sondereinträge am Anfang der Gruppenansicht
_SV_ENTRY  = b">> Sendung verpasst?"
_SN_ENTRY  = b">> Demn\xc3\xa4chst"


def _episode_label(title_bytes, topic_bytes=None, watched=False):
    """
    Gibt einen Listeneintrag zurueck. Falls der Titel (SXX/EYY) enthaelt,
    wird 'S12E08  <Titel ohne Tag>' vorangestellt, sonst unveraendert.
    Optional: topic_bytes als Praefix voranstellen (z.B. fuer Direkte Treffer),
    aber nur wenn das Topic nicht bereits im Titel enthalten ist.
    watched=True fuegt ein '[S] ' Praefix hinzu.
    """
    import re
    try:
        title = title_bytes.decode("utf-8", "replace")
    except Exception:
        title = str(title_bytes)
    m = re.search(r'\(S(\d+)/E(\d+)\)', title)
    if m:
        season  = int(m.group(1))
        episode = int(m.group(2))
        clean   = re.sub(r'\s*\(S\d+/E\d+\)', '', title).strip()
        label   = "S%02dE%02d  %s" % (season, episode, clean)
    else:
        label = title
    if topic_bytes:
        try:
            topic = topic_bytes.decode("utf-8", "replace")
        except Exception:
            topic = str(topic_bytes)
        if topic.lower() not in label.lower():
            label = topic + ": " + label
    if watched:
        label = "* " + label
    try:
        return label.encode("utf-8")
    except Exception:
        return str(label)


def _relevance_sort(groups, search_term):
    """
    Sortiert Gruppen nach Relevanz zum Suchbegriff:
      0 = Gruppenname beginnt mit dem Suchbegriff  (beste Treffer)
      1 = Gruppenname enthält den Suchbegriff       (gute Treffer)
      2 = Rest                                       (schwache Treffer)
    Ohne aktive Suche wird die Reihenfolge nicht veraendert.
    """
    if not search_term:
        return groups
    try:
        term = search_term.lower()
    except Exception:
        return groups

    def _rank(group_tuple):
        key = group_tuple[0]
        try:
            name = key.decode("utf-8", "replace").lower()
        except Exception:
            name = str(key).lower()
        if name.startswith(term):
            return 0
        if term in name:
            return 1
        return 2

    return sorted(groups, key=_rank)


def _inject_direct_hits(groups, search_term):
    """
    Fuegt bei aktiver Suche eine Gruppe "Direkte Treffer" ganz oben ein.
    Darin landen alle Episoden, deren Titel den Suchbegriff enthaelt,
    aber deren topic (Gruppenname) ihn NICHT enthaelt.
    So werden Filmtitel gefunden, auch wenn das topic nichts mit dem
    gesuchten Begriff zu tun hat.
    """
    if not search_term:
        return groups
    try:
        term = search_term.lower()
    except Exception:
        return groups

    terms = term.split()
    if not terms:
        return groups

    direct = []
    for key, episodes in groups:
        try:
            group_name = key.decode("utf-8", "replace").lower()
        except Exception:
            group_name = str(key).lower()
        if all(w in group_name for w in terms):
            # Topic enthaelt alle Woerter bereits -> normale Gruppe genuegt
            continue
        for ep in episodes:
            t = ep.get("title", b"")
            try:
                title_str = t.decode("utf-8", "replace").lower()
            except Exception:
                title_str = str(t).lower()
            if all(w in title_str for w in terms):
                direct.append(ep)

    if not direct:
        return groups

    # Relevanz-Sortierung: exakter Substring zuerst, dann Einzelwoerter, innerhalb gleicher Stufe nach Datum
    def _relevance_key(ep):
        t = ep.get("title", b"")
        try:
            title_str = t.decode("utf-8", "replace").lower()
        except Exception:
            title_str = str(t).lower()
        exact = 0 if term in title_str else 1
        ts = ep.get("timestamp", 0)
        try:
            ts = int(ts)
        except Exception:
            ts = 0
        return (exact, -ts)

    direct.sort(key=_relevance_key)

    # Duplikate entfernen: nur URL-Pfad vergleichen (Hostname ignorieren, da CDN-Varianten existieren)
    # Nach der Sortierung, damit der relevantere Eintrag gewinnt
    seen_url_paths = set()
    deduped = []
    for ep in direct:
        url = ep.get("stream_url_sd") or ep.get("stream_url_hd") or b""
        try:
            url_str = url.decode("utf-8", "replace") if isinstance(url, bytes) else str(url)
        except Exception:
            url_str = str(url)
        # Pfad ab dem ersten "/" nach "://" extrahieren
        try:
            path_key = url_str.split("://", 1)[1].split("/", 1)[1] if "://" in url_str else url_str
        except Exception:
            path_key = url_str
        if path_key and path_key in seen_url_paths:
            continue
        if path_key:
            seen_url_paths.add(path_key)
        deduped.append(ep)
    direct = deduped

    label = (">> Direkte Treffer (%d)" % len(direct)).encode("utf-8")
    return [(label, direct)] + list(groups)


def _build_groups(items, sort_mode="timestamp", flat=False):
    groups_dict  = {}
    groups_order = []
    for item in items:
        if flat:
            # Im Flat-Modus jeden Titel als eigene Gruppe — keine Sammelordner
            key = item.get("title") or b"Sonstige"
        else:
            key = item.get("group") or item.get("title") or b"Sonstige"
        if key not in groups_dict:
            groups_dict[key] = []
            groups_order.append(key)
        groups_dict[key].append(item)
    if sort_mode == "az":
        try:
            groups_order.sort(key=lambda k: k.decode("utf-8", "replace").lower())
        except Exception:
            pass
    return [(k, groups_dict[k]) for k in groups_order]


# ------------------------------------------------------------------
# Alpha-Picker – A-Z Buchstabenauswahl als Overlay (Card Layout)
# ------------------------------------------------------------------
_ALPHA_CHARS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]
_ALPHA_COLS  = 9
_ALPHA_ROWS  = 3
_ALPHA_CW, _ALPHA_CH = (160, 110) if IS_FHD else (106, 73)
_ALPHA_X0    = ((1920 - _ALPHA_COLS * _ALPHA_CW) // 2) if IS_FHD else ((1280 - _ALPHA_COLS * _ALPHA_CW) // 2)
_ALPHA_Y0    = ((1080 - _ALPHA_ROWS * _ALPHA_CH) // 2 + 20) if IS_FHD else ((720 - _ALPHA_ROWS * _ALPHA_CH) // 2 + 13)


class OeMediathekAlphaPickerScreen(Screen):

    def _make_skin(self):
        cells = ""
        font_cell  = 40 if IS_FHD else 26
        font_title = 34 if IS_FHD else 22
        font_hint  = 32 if IS_FHD else 21
        screen_w, screen_h = (1920, 1080) if IS_FHD else (1280, 720)
        title_h = 60 if IS_FHD else 40
        hint_h  = 50 if IS_FHD else 33

        for i, ch in enumerate(_ALPHA_CHARS):
            r = i // _ALPHA_COLS
            c = i % _ALPHA_COLS
            x = _ALPHA_X0 + c * _ALPHA_CW
            y = _ALPHA_Y0 + r * _ALPHA_CH
            cells += '<widget name="cell_%d" position="%d,%d" size="%d,%d" ' \
                     'font="Regular;%d" halign="center" valign="center" ' \
                     'foregroundColor="#CCCCCC" backgroundColor="#33000000" transparent="1" />\n' \
                     % (i, x, y, _ALPHA_CW, _ALPHA_CH, font_cell)

        bg_w = _ALPHA_COLS * _ALPHA_CW + (80 if IS_FHD else 53)
        bg_h = _ALPHA_ROWS * _ALPHA_CH + (160 if IS_FHD else 106)
        bg_x = _ALPHA_X0 - (40 if IS_FHD else 26)
        bg_y = _ALPHA_Y0 - (90 if IS_FHD else 60)

        return """
        <screen name="OeMediathekAlphaPickerScreen" position="0,0" size="%d,%d" flags="wfNoBorder">
            <eLabel position="0,0" size="%d,%d" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="%d,%d" size="%d,%d" backgroundColor="#33000000" zPosition="-5" />

            <widget name="title_label" position="%d,%d" size="%d,%d"
                    font="Regular;%d" halign="center" valign="center"
                    foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />

            <widget name="selector" position="%d,%d" size="%d,%d"
                    backgroundColor="#33333333" zPosition="-3" />
            %s
            <widget name="hint_label" position="%d,%d" size="%d,%d"
                    font="Regular;%d" halign="center" valign="center"
                    foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
        </screen>
        """ % (
            screen_w, screen_h,
            screen_w, screen_h,
            bg_x, bg_y, bg_w, bg_h,
            bg_x, bg_y + (20 if IS_FHD else 13), bg_w, title_h, font_title,
            _ALPHA_X0, _ALPHA_Y0, _ALPHA_CW, _ALPHA_CH,
            cells,
            bg_x, bg_y + bg_h - (60 if IS_FHD else 40), bg_w, hint_h, font_hint,
        )

    skin = ""

    def __init__(self, session):
        self.skin = self._make_skin()
        Screen.__init__(self, session)
        self.selected = 0

        self["title_label"] = Label(_b("Buchstabe w\xc3\xa4hlen"))
        self["selector"]    = Label("")
        self["hint_label"]  = Label(_b("OK = W\xc3\xa4hlen   |   EXIT = Abbrechen"))

        for i, ch in enumerate(_ALPHA_CHARS):
            self["cell_%d" % i] = Label(ch)

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self.on_ok,
                "cancel": self.on_cancel,
                "up":     self.key_up,
                "down":   self.key_down,
                "left":   self.key_left,
                "right":  self.key_right,
            },
            1,
        )
        self.onShow.append(self._refresh)

    def _refresh(self):
        self._move_selector()
        self._update_colors()

    def _move_selector(self):
        try:
            r = self.selected // _ALPHA_COLS
            c = self.selected % _ALPHA_COLS
            x = _ALPHA_X0 + c * _ALPHA_CW
            y = _ALPHA_Y0 + r * _ALPHA_CH
            self["selector"].instance.move(ePoint(x, y))
        except Exception:
            pass

    def _update_colors(self):
        try:
            from enigma import gRGB
            for i in range(len(_ALPHA_CHARS)):
                col = gRGB(0xFF, 0xFF, 0xFF) if i == self.selected else gRGB(0x88, 0x88, 0x88)
                self["cell_%d" % i].instance.setForegroundColor(col)
        except Exception:
            pass

    def _select(self, idx):
        self.selected = idx % len(_ALPHA_CHARS)
        self._move_selector()
        self._update_colors()

    def key_right(self):
        self._select(self.selected + 1)

    def key_left(self):
        self._select(self.selected - 1)

    def key_down(self):
        new = self.selected + _ALPHA_COLS
        self._select(new if new < len(_ALPHA_CHARS) else self.selected % _ALPHA_COLS)

    def key_up(self):
        new = self.selected - _ALPHA_COLS
        self._select(new if new >= 0 else ((_ALPHA_ROWS - 1) * _ALPHA_COLS) + self.selected % _ALPHA_COLS)

    def on_ok(self):
        self.close(_ALPHA_CHARS[self.selected])

    def on_cancel(self):
        self.close(None)

    def doClose(self):
        try:
            Screen.doClose(self)
        except TypeError:
            pass


# ------------------------------------------------------------------
# Info-Screen (Zeigt Details und Laufzeit zu einer Episode als Popup)
# ------------------------------------------------------------------
class OeMediathekInfoScreen(Screen):

    @staticmethod
    def _make_skin():
        if IS_FHD:
            return """
        <screen name="OeMediathekInfoScreen" position="0,0" size="1920,1080" flags="wfNoBorder">
            <eLabel position="0,0" size="1920,1080" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="360,140" size="1200,800" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="400,170" size="1120,60" font="Regular;42" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="duration_label" position="400,240" size="1120,40" font="Regular;28" halign="left" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <eLabel position="400,300" size="1120,2" backgroundColor="#33FFFFFF" zPosition="-4" />
            <widget name="text_label" position="400,330" size="1120,520" font="Regular;36" foregroundColor="#CCCCCC" backgroundColor="#33000000" valign="top" halign="left" transparent="1" />
            <widget name="hint_label" position="400,870" size="1120,40" font="Regular;24" halign="center" valign="center" foregroundColor="#555555" backgroundColor="#33000000" transparent="1" />
        </screen>
            """
        else:
            return """
        <screen name="OeMediathekInfoScreen" position="0,0" size="1280,720" flags="wfNoBorder">
            <eLabel position="0,0" size="1280,720" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="240,93" size="800,534" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="266,113" size="746,40" font="Regular;28" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="duration_label" position="266,160" size="746,26" font="Regular;18" halign="left" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <eLabel position="266,200" size="746,1" backgroundColor="#33FFFFFF" zPosition="-4" />
            <widget name="text_label" position="266,220" size="746,346" font="Regular;24" foregroundColor="#CCCCCC" backgroundColor="#33000000" valign="top" halign="left" transparent="1" />
            <widget name="hint_label" position="266,580" size="746,26" font="Regular;16" halign="center" valign="center" foregroundColor="#555555" backgroundColor="#33000000" transparent="1" />
        </screen>
            """

    def __init__(self, session, title, description, duration):
        self.skin = self._make_skin()
        Screen.__init__(self, session)
        
        self["title_label"] = Label(_b(title))
        
        dur_str = _b("Laufzeit: ") + _b(duration)
        self["duration_label"] = Label(dur_str)
        
        self["text_label"] = ScrollLabel(_b(description))
        self["hint_label"] = Label(_b("Hoch/Runter = Scrollen   |   EXIT/INFO = Schlie\xc3\x9fen"))
        
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "EPGSelectActions"],
            {
                "ok": self.close,
                "cancel": self.close,
                "info": self.close,
                "epg": self.close,
                "up": self.scroll_up,
                "down": self.scroll_down,
                "left": self.scroll_up,
                "right": self.scroll_down,
            },
            1
        )

    def scroll_up(self):
        self["text_label"].pageUp()

    def scroll_down(self):
        self["text_label"].pageDown()

    def doClose(self):
        try:
            Screen.doClose(self)
        except TypeError as e:
            _log("doClose TypeError: " + str(e))



# ------------------------------------------------------------------
# Download-Queue
# ------------------------------------------------------------------

def _bg_download_done(fp):
    """on_done-Callback für Hintergrund-Downloads: konvertiert ggf. zu TS, dann Queue weiter."""
    if get_auto_convert() and fp and fp.lower().endswith(".mp4"):
        if _active_downloader is not None:
            _active_downloader._converting = True
        convert_mp4_to_ts(fp, on_done=lambda ts: _bg_convert_done(), on_error=lambda e: _queue_next())
    else:
        _queue_next()


def _bg_convert_done():
    if _active_downloader is not None:
        _active_downloader._converting = False
    _queue_next()


def _queue_next():
    """Startet den nächsten Download aus der Queue, oder meldet alle fertig."""
    global _active_downloader, _download_queue, _bg_download_result
    if not _download_queue:
        _active_downloader  = None
        _bg_download_result = "ok"
        return
    item = _download_queue.pop(0)
    try:
        dl = Downloader(
            item["url"],
            item["title"],
            topic=item.get("topic"),
            description=item.get("description"),
            duration=item.get("duration"),
            on_done=_bg_download_done,
            on_error=lambda msg: _queue_error(msg),
        )
        dl.on_progress = lambda *a: None
        _active_downloader = dl
        dl.start()
    except Exception:
        _queue_next()


def _queue_error(msg):
    global _active_downloader, _bg_download_result
    _active_downloader  = None
    _bg_download_result = "err:" + str(msg)
    _queue_next()


# ------------------------------------------------------------------
# Hauptmenü – Vollbild-Kachelansicht mit Logos (Card Layout)
# ------------------------------------------------------------------
class OeMediathekMainScreen(Screen):

    @staticmethod
    def _make_skin():
        tiles_bg = ""
        logos    = ""
        for r in range(TILE_ROWS):
            for c in range(TILE_COLS):
                i   = r * TILE_COLS + c
                tx  = _TX[c]
                ty  = _TY[r]
                # Logo vertikal zentriert in der Kachel (xpicons 220x132 / HD: 140x84)
                lw, lh = (220, 132) if IS_FHD else (140, 84)
                lx  = tx + (TILE_W - lw) // 2
                ly  = ty + (TILE_H - lh) // 2
                # tile_bg_X als benanntes Widget damit hide()/show() moeglich ist
                tiles_bg += '<widget name="tile_bg_%d" position="%d,%d" size="%d,%d" backgroundColor="#1A000000" zPosition="-4" />\n' \
                            % (i, tx, ty, TILE_W, TILE_H)
                logos    += '<widget name="logo_%d" position="%d,%d" size="%d,%d" alphatest="blend" scale="1" transparent="1" zPosition="1" />\n' \
                            % (i, lx, ly, lw, lh)

        if IS_FHD:
            sw, sh    = 1920, 1080
            hdr_y, hdr_h = 30, 80
            font_title   = 44
            bar_y, bar_h = 960, 100
            font_hint    = 32
            font_page    = 36
            hint_w       = 1560
            page_x, page_w = 1620, 240
        else:
            sw, sh    = 1280, 720
            hdr_y, hdr_h = 20, 53
            font_title   = 29
            bar_y, bar_h = 640, 66
            font_hint    = 21
            font_page    = 24
            hint_w       = 1040
            page_x, page_w = 1080, 160

        margin = 30

        if IS_FHD:
            return """
        <screen name="OeMediathekMainScreen" position="0,0" size="%d,%d" flags="wfNoBorder">
            <eLabel position="0,0" size="%d,%d" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="%d,%d" size="%d,%d" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="selector" position="%d,%d" size="%d,%d" backgroundColor="#1A333333" zPosition="-3" />
            %s%s
            <eLabel position="30,960" size="1860,100" backgroundColor="#1A000000" zPosition="-5" />
            <eLabel position="80,980" size="8,60" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red"    position="98,960"   size="214,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="352,980" size="8,60" backgroundColor="#1A00AA00" zPosition="2" />
            <widget name="hint_green"  position="370,960"  size="214,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_ok"     position="624,960"  size="215,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_ch"     position="879,960"  size="355,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_nav"    position="1274,960" size="255,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="1569,980" size="8,60" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow" position="1587,960" size="144,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="page_label"  position="1771,960" size="80,100"  font="Regular;28" halign="right" valign="center" foregroundColor="#AAAAAA" backgroundColor="#1A000000" transparent="1" />
            <widget name="version_label" position="1530,30" size="330,80" font="Regular;24" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
        </screen>
        """ % (
                sw, sh, sw, sh,
                margin, hdr_y, sw - 2 * margin, hdr_h,
                margin, hdr_y, sw - 2 * margin, hdr_h, font_title,
                _TX[0], _TY[0], TILE_W, TILE_H,
                tiles_bg, logos,
            )
        else:
            return """
        <screen name="OeMediathekMainScreen" position="0,0" size="%d,%d" flags="wfNoBorder">
            <eLabel position="0,0" size="%d,%d" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="%d,%d" size="%d,%d" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="selector" position="%d,%d" size="%d,%d" backgroundColor="#1A333333" zPosition="-3" />
            %s%s
            <eLabel position="30,634" size="1220,60" backgroundColor="#1A000000" zPosition="-5" />
            <eLabel position="58,649" size="5,30" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red"    position="71,634"  size="142,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="240,649" size="5,30" backgroundColor="#1A00AA00" zPosition="2" />
            <widget name="hint_green"  position="253,634" size="142,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_ok"     position="422,634" size="143,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_ch"     position="592,634" size="237,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_nav"    position="856,634" size="170,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="1053,649" size="5,30" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow" position="1066,634" size="95,60"  font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="page_label"  position="1188,634" size="62,60"  font="Regular;21" halign="right" valign="center" foregroundColor="#AAAAAA" backgroundColor="#1A000000" transparent="1" />
            <widget name="version_label" position="1010,20" size="220,53" font="Regular;16" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
        </screen>
        """ % (
                sw, sh, sw, sh,
                margin, hdr_y, sw - 2 * margin, hdr_h,
                margin, hdr_y, sw - 2 * margin, hdr_h, font_title,
                _TX[0], _TY[0], TILE_W, TILE_H,
                tiles_bg, logos,
            )

    def __init__(self, session):
        self.skin = self._make_skin()
        _log("MainScreen init")
        Screen.__init__(self, session)
        self.session       = session
        self.selected      = 0
        self.main_page     = 0
        self._sort_mode    = False   # Sortiermodus aktiv?
        self._sort_grabbed = None    # Index der angefassten Kachel (None = noch nichts gegriffen)
        self._sort_order_backup = None  # Backup der Reihenfolge fuer Reset

        self["title_label"]  = Label(_b("\xc3\x96R Mediathek"))
        self["selector"]     = Label("")
        self["hint_red"]     = Label(_b("Sortieren"))
        self["hint_green"]   = Label(_b("Einstellungen"))
        self["hint_ok"]      = Label(_b("OK = \xc3\x96ffnen"))
        self["hint_ch"]      = Label(_b("CH+/- = Seite bl\xc3\xa4ttern"))
        self["hint_nav"]     = Label(_b("EXIT = Beenden"))
        self["hint_yellow"]  = Label(_b(""))
        self["page_label"]   = Label("")
        self["version_label"] = Label(_b("v" + PLUGIN_VERSION))

        for i in range(TILES_PER_PAGE):
            try:
                self["logo_%d" % i] = _Pixmap() if _Pixmap else Label("")
            except Exception:
                self["logo_%d" % i] = Label("")
            self["tile_bg_%d" % i] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "WizardActions",
             "ChannelSelectBaseActions", "ColorActions"],
            {
                "ok":           self.on_ok,
                "cancel":       self.on_cancel,
                "up":           self.key_up,
                "down":         self.key_down,
                "left":         self.key_left,
                "right":        self.key_right,
                "nextBouquet":  self.page_next,
                "prevBouquet":  self.page_prev,
                "red":          self.key_red,
                "green":        self.key_green,
                "yellow":       self.open_download_manager,
            },
            -1,
        )
        self.onShow.append(self.__on_show)
        _log("MainScreen init OK")

    def __on_show(self):
        try:
            self._refresh_page()
        except Exception as e:
            _log("MainScreen onShow: " + str(e))
        self._update_download_hint()
        self._update_legend()

    def _update_download_hint(self):
        t = _active_downloader and _active_downloader._thread
        if (t and t.is_alive()) or _download_queue:
            self["hint_yellow"].setText(_b("Downloads"))
        else:
            self["hint_yellow"].setText(_b(""))

    def open_download_manager(self):
        t = _active_downloader and _active_downloader._thread
        if not ((t and t.is_alive()) or _download_queue):
            return
        self.session.open(
            OeMediathekDownloadManagerScreen,
            lambda: _active_downloader,
            lambda: _download_queue,
        )

    def _refresh_page(self):
        """Kacheln und Logos der aktuellen Seite neu befuellen."""
        offset = self.main_page * TILES_PER_PAGE
        for i in range(TILES_PER_PAGE):
            src_idx = offset + i
            has_src = src_idx < len(SOURCES)
            # Logo leeren
            try:
                self["logo_%d" % i].instance.setPixmap(None)
            except Exception:
                pass
            # Kachelhintergrund und Logo ein- oder ausblenden
            try:
                if has_src:
                    self["tile_bg_%d" % i].instance.show()
                    self["logo_%d" % i].instance.show()
                else:
                    self["tile_bg_%d" % i].instance.hide()
                    self["logo_%d" % i].instance.hide()
            except Exception:
                pass

        total_pages = (len(SOURCES) + TILES_PER_PAGE - 1) // TILES_PER_PAGE
        self["page_label"].setText("%d / %d" % (self.main_page + 1, total_pages))

        self._move_selector()
        self._load_logos_page(self.main_page)

    def _load_logos_page(self, page):
        if not _LoadPixmap:
            return
        offset = page * TILES_PER_PAGE
        for i in range(TILES_PER_PAGE):
            src_idx = offset + i
            if src_idx >= len(SOURCES):
                break
            _, _, logo = SOURCES[src_idx]
            if not logo:
                continue
            path = os.path.join(LOGO_DIR, logo)
            if not os.path.exists(path):
                _log("Logo fehlt: " + path)
                continue
            try:
                pix = _LoadPixmap(path)
                if pix:
                    self["logo_%d" % i].instance.setPixmap(pix)
            except Exception as e:
                _log("Logo %d Fehler: " % i + str(e))

    def _move_selector(self):
        try:
            tile_idx = self.selected % TILES_PER_PAGE
            x, y = TILE_POSITIONS[tile_idx]
            self["selector"].instance.move(ePoint(x, y))
        except Exception as e:
            _log("selector: " + str(e))

    def _set_selector_color(self, grabbed):
        """Selektor-Farbe: Gelb-transparent wenn Kachel gegriffen, sonst Grau-transparent."""
        try:
            from enigma import gRGB
            if grabbed:
                col = gRGB(0xFF, 0xD7, 0x00, 0x55)  # Gelb, halbtransparent (alpha=0x55)
            else:
                col = gRGB(0x33, 0x33, 0x33, 0x1A)  # Grau, leicht transparent
            self["selector"].instance.setBackgroundColor(col)
            self._move_selector()
            try:
                self["selector"].instance.invalidate()
            except Exception:
                pass
        except Exception as e:
            _log("selector color: " + str(e))

    def _update_legend(self):
        """Legende je nach Modus aktualisieren."""
        if not self._sort_mode:
            self["hint_red"].setText(_b("Sortieren"))
            self["hint_green"].setText(_b("Einstellungen"))
            self["hint_ok"].setText(_b("OK = \xc3\x96ffnen"))
            self["hint_ch"].setText(_b("CH+/- = Seite bl\xc3\xa4ttern"))
            self["hint_nav"].setText(_b("EXIT = Beenden"))
        elif self._sort_grabbed is None:
            # Sortiermodus, noch nichts gegriffen
            self["hint_red"].setText(_b("Fertig"))
            self["hint_green"].setText(_b("R\xc3\xbcckg\xc3\xa4ngig"))
            self["hint_ok"].setText(_b("OK = Greifen"))
            self["hint_ch"].setText(_b("CH+/- = Seite bl\xc3\xa4ttern"))
            self["hint_nav"].setText(_b("EXIT = Abbrechen"))
        else:
            # Kachel gegriffen
            self["hint_red"].setText(_b("Fertig"))
            self["hint_green"].setText(_b("R\xc3\xbcckg\xc3\xa4ngig"))
            self["hint_ok"].setText(_b("OK = Ablegen"))
            self["hint_ch"].setText(_b("CH+/- = Seite bl\xc3\xa4ttern"))
            self["hint_nav"].setText(_b("EXIT = Abbrechen"))

    # ------------------------------------------------------------------
    # Sortiermodus
    # ------------------------------------------------------------------
    _ORDER_FILE = "/etc/enigma2/oemediathek_order.json"

    @staticmethod
    def _save_order():
        try:
            import json as _json
            order = [s[0] for s in SOURCES]
            with open(OeMediathekMainScreen._ORDER_FILE, "w") as f:
                _json.dump(order, f)
            _log("Reihenfolge gespeichert")
        except Exception as e:
            _log("Reihenfolge speichern Fehler: " + str(e))

    @staticmethod
    def load_order():
        """Gespeicherte Reihenfolge auf SOURCES anwenden (beim Start aufrufen)."""
        try:
            import json as _json
            if not os.path.exists(OeMediathekMainScreen._ORDER_FILE):
                return
            with open(OeMediathekMainScreen._ORDER_FILE, "r") as f:
                order = _json.load(f)
            name_to_src = {s[0]: s for s in SOURCES}
            reordered = []
            for name in order:
                if name in name_to_src:
                    reordered.append(name_to_src[name])
            # Sender die neu dazugekommen sind (nicht in der gespeicherten Reihenfolge) hinten anhaengen
            existing = set(order)
            for s in SOURCES:
                if s[0] not in existing:
                    reordered.append(s)
            SOURCES[:] = reordered
            _log("Reihenfolge geladen")
        except Exception as e:
            _log("Reihenfolge laden Fehler: " + str(e))

    def key_red(self):
        if not self._sort_mode:
            # Sortiermodus einschalten
            self._sort_mode = True
            self._sort_grabbed = None
            self._sort_order_backup = list(SOURCES)
            _log("Sortiermodus ein")
        else:
            # Sortiermodus verlassen und Reihenfolge speichern
            self._sort_mode = False
            self._sort_grabbed = None
            self._sort_order_backup = None
            self._set_selector_color(False)
            self._save_order()
            _log("Sortiermodus aus, Reihenfolge gespeichert")
        self._refresh_page()
        self._update_legend()

    def key_green(self):
        if self._sort_mode:
            # Reset auf Ausgangsreihenfolge
            SOURCES[:] = self._sort_order_backup
            self._sort_grabbed = None
            self._set_selector_color(False)
            self._refresh_page()
            self._update_legend()
            _log("Sortierung zurueckgesetzt")
        else:
            self.open_settings()

    def _sort_move(self, new_idx):
        """Kachel von self._sort_grabbed an new_idx einf\xc3\xbcgen (alle anderen rutschen)."""
        src = self._sort_grabbed
        if src == new_idx:
            self.selected = new_idx
            new_page = new_idx // TILES_PER_PAGE
            if new_page != self.main_page:
                self.main_page = new_page
                self._refresh_page()
            else:
                self._move_selector()
            return
        item = SOURCES.pop(src)
        SOURCES.insert(new_idx, item)
        self._sort_grabbed = new_idx
        self.selected = new_idx
        new_page = new_idx // TILES_PER_PAGE
        if new_page != self.main_page:
            self.main_page = new_page
        self._refresh_page()

    def on_cancel(self):
        if self._sort_mode:
            # Sortiermodus abbrechen ohne speichern
            if self._sort_order_backup is not None:
                SOURCES[:] = self._sort_order_backup
            self._sort_mode = False
            self._sort_grabbed = None
            self._sort_order_backup = None
            self._set_selector_color(False)
            self._refresh_page()
            self._update_legend()
        else:
            self.close()

    def doClose(self):
        if self._sort_mode and self._sort_order_backup is not None:
            SOURCES[:] = self._sort_order_backup
            _log("doClose: Sortiermodus verworfen")
        try:
            Screen.doClose(self)
        except TypeError as e:
            _log("doClose TypeError: " + str(e))

    def _select(self, idx):
        if idx < 0 or idx >= len(SOURCES):
            return
        if self._sort_mode and self._sort_grabbed is not None:
            self._sort_move(idx)
        else:
            self.selected = idx
            self._move_selector()

    def page_next(self):
        total_pages = (len(SOURCES) + TILES_PER_PAGE - 1) // TILES_PER_PAGE
        new_page = (self.main_page + 1) % total_pages
        new_idx = new_page * TILES_PER_PAGE
        if self._sort_mode and self._sort_grabbed is not None:
            self._sort_move(new_idx)
        else:
            self.main_page = new_page
            self.selected = new_idx
            self._refresh_page()

    def page_prev(self):
        total_pages = (len(SOURCES) + TILES_PER_PAGE - 1) // TILES_PER_PAGE
        new_page = (self.main_page - 1) % total_pages
        new_idx = new_page * TILES_PER_PAGE
        if self._sort_mode and self._sort_grabbed is not None:
            self._sort_move(new_idx)
        else:
            self.main_page = new_page
            self.selected = new_idx
            self._refresh_page()

    def key_right(self):
        tile_idx = self.selected % TILES_PER_PAGE
        col = tile_idx % TILE_COLS
        if col == TILE_COLS - 1:
            if not get_tile_wrap_lr():
                row = tile_idx // TILE_COLS
                new = self.main_page * TILES_PER_PAGE + row * TILE_COLS
                self._select(new)
            else:
                new = self.selected + 1
                if new >= len(SOURCES):
                    new = 0
                new_page = new // TILES_PER_PAGE
                self.main_page = new_page
                self.selected = new
                self._refresh_page()
        else:
            new = self.selected + 1
            if new < len(SOURCES):
                self._select(new)

    def key_left(self):
        tile_idx = self.selected % TILES_PER_PAGE
        col = tile_idx % TILE_COLS
        if col == 0:
            if not get_tile_wrap_lr():
                row = tile_idx // TILE_COLS
                new = self.main_page * TILES_PER_PAGE + row * TILE_COLS + (TILE_COLS - 1)
                if new >= len(SOURCES):
                    new = len(SOURCES) - 1
                self._select(new)
            else:
                new = self.selected - 1
                if new < 0:
                    new = len(SOURCES) - 1
                new_page = new // TILES_PER_PAGE
                self.main_page = new_page
                self.selected = new
                self._refresh_page()
        else:
            self._select(self.selected - 1)

    def key_down(self):
        tile_idx = self.selected % TILES_PER_PAGE
        row = tile_idx // TILE_COLS
        col = tile_idx % TILE_COLS
        new_tile = ((row + 1) % TILE_ROWS) * TILE_COLS + col
        new = self.main_page * TILES_PER_PAGE + new_tile
        if new >= len(SOURCES):
            new = self.main_page * TILES_PER_PAGE + col
        self._select(new)

    def key_up(self):
        tile_idx = self.selected % TILES_PER_PAGE
        row = tile_idx // TILE_COLS
        col = tile_idx % TILE_COLS
        new_tile = ((row - 1) % TILE_ROWS) * TILE_COLS + col
        new = self.main_page * TILES_PER_PAGE + new_tile
        if new >= len(SOURCES):
            new = self.main_page * TILES_PER_PAGE + col
        self._select(new)

    def on_ok(self):
        if self._sort_mode:
            if self._sort_grabbed is None:
                # Kachel greifen
                self._sort_grabbed = self.selected
                self._set_selector_color(True)
                _log("Sortierung: gegriffen idx=%d" % self.selected)
            else:
                # Kachel ablegen
                self._sort_grabbed = None
                self._set_selector_color(False)
                _log("Sortierung: abgelegt idx=%d" % self.selected)
            self._update_legend()
            return
        try:
            name, loader, _ = SOURCES[self.selected]
            _log("Oeffne: " + name)
            self.session.open(OeMediathekScreen, name, loader)
        except Exception:
            _log("on_ok: " + _fmt_exc())

    def open_settings(self):
        try:
            self.session.open(OeMediathekSettingsScreen)
        except Exception:
            _log("open_settings: " + _fmt_exc())


# ------------------------------------------------------------------
# Suchverlauf-Screen  (Vorschalt-Dialog vor der Tastatur)
# ------------------------------------------------------------------
class OeMediathekSearchHistoryScreen(Screen):

    _NEW_SEARCH = u"\u25ba Neue Suche..."

    @staticmethod
    def _make_skin():
        if IS_FHD:
            return """
        <screen name="OeMediathekSearchHistoryScreen" position="0,0" size="1920,1080" flags="wfNoBorder">
            <eLabel position="0,0" size="1920,1080" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="560,200" size="800,680" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="600,230" size="720,60" font="Regular;38" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <eLabel position="600,306" size="720,2" backgroundColor="#33FFFFFF" zPosition="-4" />
            <widget name="menu_list" position="600,320" size="720,420" font="Regular;34" itemHeight="56" foregroundColor="#CCCCCC" backgroundColor="#33000000" transparent="1" />
            <eLabel position="560,770" size="800,110" backgroundColor="#1A000000" zPosition="-4" />
            <eLabel position="580,790" size="8,60" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red"   position="598,770" size="260,110" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_ok"    position="800,770" size="260,110" font="Regular;32" halign="center" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_label" position="560,770" size="780,110" font="Regular;32" halign="right" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
        </screen>
            """
        else:
            return """
        <screen name="OeMediathekSearchHistoryScreen" position="0,0" size="1280,720" flags="wfNoBorder">
            <eLabel position="0,0" size="1280,720" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="373,133" size="534,453" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="400,153" size="480,40" font="Regular;25" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <eLabel position="400,204" size="480,1" backgroundColor="#33FFFFFF" zPosition="-4" />
            <widget name="menu_list" position="400,213" size="480,280" font="Regular;22" itemHeight="37" foregroundColor="#CCCCCC" backgroundColor="#33000000" transparent="1" />
            <eLabel position="373,513" size="534,73" backgroundColor="#1A000000" zPosition="-4" />
            <eLabel position="387,527" size="5,40" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red"   position="399,513" size="170,73" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_ok"    position="533,513" size="170,73" font="Regular;21" halign="center" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_label" position="373,513" size="520,73" font="Regular;21" halign="right" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
        </screen>
            """

    def __init__(self, session):
        self.skin = self._make_skin()
        Screen.__init__(self, session)

        self["title_label"] = Label(_b("Letzte Suchen"))
        self["menu_list"]   = MenuList([])
        self["hint_red"]    = Label(_b("L\xc3\xb6schen"))
        self["hint_ok"]     = Label(_b("OK = Ausw\xc3\xa4hlen"))
        self["hint_label"]  = Label(_b("EXIT = Abbrechen"))

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "ok":     self.on_ok,
                "cancel": self.on_cancel,
                "red":    self.on_delete,
            },
            1,
        )
        self.onShow.append(self._populate)
        self["menu_list"].onSelectionChanged.append(self._on_selection_changed)

    def _populate(self):
        history = load_search_history()
        entries = [self._NEW_SEARCH] + history
        self["menu_list"].setList([_b(e) for e in entries])
        self._on_selection_changed()

    def _on_selection_changed(self):
        sel = self["menu_list"].getCurrent()
        try:
            text = sel.decode("utf-8", "replace") if sel else ""
        except Exception:
            text = str(sel) if sel else ""
        if text == self._NEW_SEARCH:
            self["hint_red"].setText(_b(""))
        else:
            self["hint_red"].setText(_b("L\xc3\xb6schen"))

    def on_ok(self):
        sel = self["menu_list"].getCurrent()
        if sel is None:
            self.close(None)
            return
        try:
            text = sel.decode("utf-8", "replace")
        except Exception:
            text = str(sel)
        if text == self._NEW_SEARCH:
            self.close("__new__")
        else:
            self.close(text)

    def on_delete(self):
        """Rote Taste: aktuellen Eintrag aus dem Verlauf entfernen."""
        sel = self["menu_list"].getCurrent()
        if sel is None:
            return
        try:
            text = sel.decode("utf-8", "replace")
        except Exception:
            text = str(sel)
        if text == self._NEW_SEARCH:
            return
        from mediathek import load_search_history, SEARCH_HISTORY_FILE
        import json as _json
        history = load_search_history()
        history = [e for e in history if e != text]
        try:
            with open(SEARCH_HISTORY_FILE, "w") as f:
                _json.dump(history, f, ensure_ascii=False)
        except Exception:
            pass
        self._populate()

    def on_cancel(self):
        self.close(None)

    def doClose(self):
        try:
            Screen.doClose(self)
        except TypeError:
            pass


# ------------------------------------------------------------------
# Inhalts-Screen  (Split-Screen Card Layout mit Deep-Fetch)
# ------------------------------------------------------------------
class OeMediathekScreen(Screen):

    @staticmethod
    def _make_skin():
        if IS_FHD:
            return """
        <screen name="OeMediathekScreen" position="0,0" size="1920,1080" flags="wfNoBorder">
            <eLabel position="0,0" size="1920,1080" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="30,30" size="1860,80" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="50,30" size="850,80" font="Regular;42" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="sort_label" position="910,30" size="220,80" font="Regular;28" halign="left" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <widget name="status_label" position="1140,30" size="690,80" font="Regular;28" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <eLabel position="30,140" size="1100,780" backgroundColor="#33000000" zPosition="-5" />
            <widget name="menu_list" position="40,150" size="1080,760" font="Regular;34" scrollbarMode="showOnDemand" itemHeight="58" backgroundColor="#33000000" transparent="1" />
            <eLabel position="1160,140" size="730,780" backgroundColor="#33000000" zPosition="-5" />
            <widget name="description_text" position="1190,160" size="670,740" font="Regular;34" foregroundColor="#CCCCCC" backgroundColor="#33000000" valign="top" halign="left" transparent="1" />
            <eLabel position="30,960" size="1860,100" backgroundColor="#1A000000" zPosition="-5" />
            <eLabel position="50,980" size="8,60" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red" position="68,960" size="350,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="450,980" size="8,60" backgroundColor="#1A00AA00" zPosition="2" />
            <widget name="hint_green" position="468,960" size="300,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="808,980" size="8,60" backgroundColor="#1AAAAA00" zPosition="2" />
            <widget name="hint_yellow" position="826,960" size="292,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="1158,980" size="8,60" backgroundColor="#1A0044DD" zPosition="2" />
            <widget name="hint_blue" position="1176,960" size="242,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_info" position="1468,960" size="220,100" font="Regular;26" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_page" position="1698,960" size="172,100" font="Regular;32" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#1A000000" transparent="1" />
        </screen>
            """
        else:
            return """
        <screen name="OeMediathekScreen" position="0,0" size="1280,720" flags="wfNoBorder">
            <eLabel position="0,0" size="1280,720" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="30,20" size="1220,53" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="43,20" size="560,53" font="Regular;28" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="sort_label" position="610,20" size="147,53" font="Regular;18" halign="left" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <widget name="status_label" position="760,20" size="460,53" font="Regular;18" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <eLabel position="30,90" size="733,524" backgroundColor="#33000000" zPosition="-5" />
            <widget name="menu_list" position="36,97" size="720,510" font="Regular;22" scrollbarMode="showOnDemand" itemHeight="38" backgroundColor="#33000000" transparent="1" />
            <eLabel position="773,90" size="477,524" backgroundColor="#33000000" zPosition="-5" />
            <widget name="description_text" position="790,103" size="443,504" font="Regular;22" foregroundColor="#CCCCCC" backgroundColor="#33000000" valign="top" halign="left" transparent="1" />
            <eLabel position="30,634" size="1220,60" backgroundColor="#1A000000" zPosition="-5" />
            <eLabel position="43,649" size="5,30" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red" position="51,634" size="220,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="293,649" size="5,30" backgroundColor="#1A00AA00" zPosition="2" />
            <widget name="hint_green" position="301,634" size="190,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="513,649" size="5,30" backgroundColor="#1AAAAA00" zPosition="2" />
            <widget name="hint_yellow" position="521,634" size="190,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="733,649" size="5,30" backgroundColor="#1A0044DD" zPosition="2" />
            <widget name="hint_blue" position="741,634" size="175,60" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_info" position="930,634" size="150,60" font="Regular;17" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_page" position="1080,634" size="170,60" font="Regular;21" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#1A000000" transparent="1" />
        </screen>
            """

    def __init__(self, session, source_name, loader):
        self.skin = self._make_skin()
        _log("ContentScreen init: " + source_name)
        Screen.__init__(self, session)
        self.session       = session
        self.source_name   = source_name
        self.loader        = loader

        self.page            = 0
        self.mode            = MODE_GROUPS
        self._has_more       = True
        self.all_items       = []
        self.groups          = []
        self.groups_filtered = []
        self.cur_episodes    = []
        self.cur_group_name  = b""
        self.ep_page         = 0
        self.ep_total        = 0
        self.ep_has_more     = False

        self.current_search  = None
        self.min_duration    = 0
        self.sort_mode       = "timestamp"
        self._sv_mode        = False   # True = Sendung-verpasst?-Filter aktiv
        self._sv_sn_pending  = None    # "sv" oder "sn" — wartet auf Prefetch
        self._fav_sort_mode  = False   # True = Favoriten-Sortiermodus aktiv
        self._fav_grabbed    = None    # Index des gegriffenen Favoriten
        self._fav_order_backup = None  # Backup der Reihenfolge vor dem Sortieren
        self._fav_show_episodes = False  # True = Episode-Favoriten-Ansicht aktiv
        self._ep_fav_sort_mode = False   # True = Sortiermodus in Episode-Favoriten
        self._ep_fav_grabbed   = None    # Index des gegriffenen Episode-Favoriten
        self._ep_fav_order_backup = None # Backup der Reihenfolge
        self._ep_sort_mode     = "timestamp"  # Sortierung in Episodenansicht
        self._ep_api_has_more = False  # API hat noch Daten nach letztem Episoden-Fetch
        self._ep_next_api_offset = 0  # Naechster API-Offset fuer Episoden-Paging
        self._sn_mode        = False   # True = Demnächst-Filter aktiv

        self._fetching      = False
        self._loaded        = False   # False bis erster Fetch abgeschlossen
        self._fetch_target  = "groups"
        self._fetch_result  = []
        self._fetch_episodes_result = []
        self._fetch_alpha_result = []
        self._fetch_total   = 0
        self._fetch_error   = None
        self._az_items      = []  # A-Z Fetch-Ergebnis, trennt Paging-Items von all_items
        self._paged_total   = 0   # _fetch_total des letzten normalen (paginieren) Fetches
        self._paged_has_more = False
        self._groups_next_api_offset = 0  # Naechster API-Offset fuer Gruppen-Paging

        self.last_index = -1
        self.cur_group_idx = -1
        self.alpha_letter  = None

        self["title_label"]  = Label(source_name)
        self["status_label"] = Label("Lade Inhalte ...")
        self["menu_list"]    = MenuList([])
        self["description_text"] = ScrollLabel(_b(""))
        
        self["sort_label"]   = Label("")
        self["hint_red"]     = Label("")
        self["hint_green"]   = Label("")
        self["hint_yellow"]  = Label("")
        self["hint_blue"]    = Label("")
        self["hint_info"]    = Label("")
        self["hint_page"]    = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions",
             "ChannelSelectBaseActions", "EPGSelectActions"],
            {
                "ok":           self.on_ok,
                "cancel":       self.on_cancel,
                "red":          self.on_red,
                "green":        self.on_green,
                "yellow":       self.on_yellow,
                "blue":         self.on_blue,
                "info":         self.on_info,
                "epg":          self.on_info,
                "nextBouquet":  self.next_page,
                "prevBouquet":  self.prev_page,
                "up":           self.on_up,
                "down":         self.on_down,
            },
            -1,
        )
        self.onShow.append(self.__on_show)

        self._start_timer = eTimer()
        self._start_timer.callback.append(self._start_fetch)
        self._start_timer.start(300, True)

        self._poll_timer = eTimer()
        self._poll_timer.callback.append(self._poll_fetch)
        
        self._desc_timer = eTimer()
        self._desc_timer.callback.append(self._update_desc)
        self._desc_timer.start(250, False)

        self._toast_timer  = eTimer()
        self._toast_timer.callback.append(self._clear_toast)
        self._saved_status = None

        self._first_desc_timer = None

        self.onClose.append(self.__stop_timers)

    def __on_show(self):
        try:
            self["menu_list"].instance.moveSelectionTo(
                self["menu_list"].getSelectedIndex() or 0
            )
        except Exception:
            pass

    def __stop_timers(self):
        try:
            self._first_desc_timer.stop()
        except Exception:
            pass
        for timer, cb in ((self._start_timer, self._start_fetch),
                          (self._poll_timer,  self._poll_fetch),
                          (self._desc_timer,  self._update_desc),
                          (self._toast_timer, self._clear_toast)):
            try:
                if timer:
                    timer.stop()
                    timer.callback.remove(cb)
            except Exception:
                pass
        self._start_timer = None
        self._poll_timer  = None
        self._desc_timer  = None
        self._toast_timer = None

    def doClose(self):
        _log("doClose")
        self.__stop_timers()
        try:
            Screen.doClose(self)
        except TypeError as e:
            _log("doClose TypeError: " + str(e))

    def _start_fetch(self):
        if self._fetching:
            return
        _log("Fetch Seite %d" % self.page)
        self._fetching     = True
        self._fetch_target = "groups"
        self._fetch_result = []
        self._fetch_error  = None
        self["status_label"].setText("Verbinde ...")
        t = threading.Thread(target=self._fetch_thread)
        t.daemon = True
        t.start()
        if self._poll_timer:
            self._poll_timer.start(300, True)

    def _fetch_thread(self):
        try:
            api_sort = self.sort_mode if self.sort_mode != "az" else "timestamp"
            if self.sort_mode == "az":
                # A-Z: einmalig alle Ergebnisse ab Offset 0
                result, total, _rc = self.loader(
                    offset=0,
                    size=1000,
                    search_term=self.current_search,
                    min_duration=self.min_duration,
                    sort_by=api_sort,
                )
                self._fetch_result = result
                self._fetch_total  = total
            else:
                # Normales Paging: einen Request mit groesserer Treffermenge
                FETCH_SIZE = PAGE_SIZE * 5
                api_offset = self._groups_next_api_offset
                res, total, rc = self.loader(
                    offset=api_offset,
                    size=FETCH_SIZE,
                    search_term=self.current_search,
                    min_duration=self.min_duration,
                    sort_by=api_sort,
                )
                self._fetch_result = res
                self._fetch_total  = total
                self._fetch_next_api_offset = api_offset + FETCH_SIZE
                self._fetch_last_rc_full    = (rc >= FETCH_SIZE)
        except Exception:
            self._fetch_error = _fmt_exc()
        self._fetching = False

    def _poll_fetch(self):
        if self._fetching:
            if self._poll_timer:
                self._poll_timer.start(300, True)
            return

        if self._fetch_target == "episodes":
            self._on_episodes_fetch_done()
        elif self._fetch_target == "ep_az_load":
            self._on_ep_az_load_done()
        elif self._fetch_target == "alpha":
            self._on_alpha_fetch_done()
        elif self._fetch_target == "sv_sn_prefetch":
            self._on_sv_sn_prefetch_done()
        else:
            self._on_fetch_done()

    def _on_fetch_done(self):
        if self._fetch_error:
            _log("Fehler: " + self._fetch_error)
            self["status_label"].setText("Fehler beim Laden!")
            return
        
        raw = self._fetch_result
        _log("Fetch ok: %d Eintraege Seite %d" % (len(raw), self.page))

        if not raw and self.page == 0:
            self["status_label"].setText("Keine Inhalte gefunden.")
            self["menu_list"].setList([])
            return

        if self.sort_mode == "az":
            self._az_items = raw
            self._has_more = False
        else:
            self._has_more = getattr(self, "_fetch_last_rc_full", False)
            self._groups_next_api_offset = getattr(self, "_fetch_next_api_offset", (self.page + 1) * PAGE_SIZE)
            self.all_items = raw
            self._paged_total    = self._fetch_total
            self._paged_has_more = self._has_more

        self.groups          = _build_groups(raw, self.sort_mode)
        self.groups_filtered = _relevance_sort(self.groups, self.current_search)
        self.groups_filtered = _inject_direct_hits(self.groups_filtered, self.current_search)
        self._show_groups()

    def _update_desc(self):
        global _bg_download_result
        if _bg_download_result is not None:
            result = _bg_download_result
            _bg_download_result = None
            if result == "ok":
                self["status_label"].setText(_b("Alle Downloads abgeschlossen!"))
            else:
                self._show_toast("Download fehlgeschlagen!", added=False)
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is None or idx < 0:
                return
            if idx == self.last_index:
                return
            self.last_index = idx

            if self._fetching or not self._loaded:
                return

            if self.mode == MODE_GROUPS:
                self._update_red_hint()
                self._update_blue_hint()
                offset = self._sv_sn_offset()
                if idx is not None and idx >= offset:
                    real_idx = idx - offset
                    if real_idx < len(self.groups_filtered):
                        gname, _ = self.groups_filtered[real_idx]
                        try:
                            gname_str = gname.decode("utf-8", "replace") if isinstance(gname, bytes) else gname
                        except Exception:
                            gname_str = str(gname)
                        self["description_text"].setText(_b(gname_str))
                    else:
                        self["description_text"].setText(_b(""))
                else:
                    self["description_text"].setText(_b(""))
            elif self.mode == MODE_EPISODES:
                self._update_info_hint()
                self._update_ep_sort_hint()
                if idx is not None and idx < len(self.cur_episodes):
                    item = self.cur_episodes[idx]
                    try:
                        title = item["title"].decode("utf-8", "replace") if isinstance(item["title"], bytes) else item["title"]
                    except Exception:
                        title = str(item["title"])
                    desc = item.get("description", _b("Keine Beschreibung verfügbar."))
                    dur  = item.get("duration", b"Unbekannt")
                    full_text = _b(title) + _b("\n\n[") + _b(dur) + _b("]\n\n") + _b(desc)
                    self["description_text"].setText(full_text)
        except Exception:
            pass

    def _force_first_desc(self):
        """Einmaliger Timer-Callback: erzwingt Beschreibung nach VTi-Rendering."""
        self.last_index = -2
        self._update_desc()


    # Sender mit zu wenigen Eintraegen fuer "Sendung verpasst?" / "Demnaechst" (< 50/Woche)
    _NO_SN_SOURCES = frozenset([
        "Radio Bremen", "funk", "DW", "ZDF Info", "ZDF Neo",
        "KiKA", "tagesschau24", "ORF", "SRF",
    ])

    def _sv_sn_offset(self):
        """Offset der echten Gruppen in der MenuList — 1 oder 2 wenn SV/SN eingeblendet, sonst 0."""
        if self.current_search:
            return 0
        if self.alpha_letter:
            return 0
        if self.source_name in ("Meine Favoriten", "Alle Mediatheken"):
            return 0
        if self.source_name in self._NO_SN_SOURCES:
            return 1  # nur SV
        return 2  # SV + SN

    def _show_groups(self, restore_pos=False):
        self.mode = MODE_GROUPS
        self._loaded = True
        self.last_index = -1
        self._update_sort_label()
        # Sondereinträge nur bei echten Mediatheken, nicht bei Favoriten, "Alle" oder aktiver Suche
        offset = self._sv_sn_offset() if not self.current_search else 0
        if offset == 2:
            entries = [_SV_ENTRY, _SN_ENTRY]
        elif offset == 1:
            entries = [_SV_ENTRY]
        else:
            entries = []
        for gname, gitems in self.groups_filtered:
            # Keine Zahlen mehr in der Vorschau anhängen
            entries.append(gname)
        self["menu_list"].setList(entries)

        if self._sv_mode:
            status_text = "Sendung verpasst? \xe2\x80\x94 %d Sendungen" % len(self.groups_filtered)
        elif self._sn_mode:
            status_text = "Demn\xc3\xa4chst \xe2\x80\x94 %d Sendungen" % len(self.groups_filtered)
        else:
            status_text = "%d Sendungen" % len(self.groups_filtered)
        if self.current_search:
            status_text += " (Suche: %s)" % self.current_search
        self["status_label"].setText(_b(status_text))

        if self._fav_sort_mode:
            # Im Sortiermodus: Hints werden von _fav_update_hints gesetzt
            self._fav_update_hints()
        else:
            self._update_red_hint()
            current = self._current_sort_label()
            next_hint = self._next_sort_hint()
            if next_hint:
                self["hint_green"].setText(_b(current + " > " + next_hint))
            else:
                self["hint_green"].setText(_b(current))
            if self.source_name != "Meine Favoriten":
                self["hint_yellow"].setText("Suche (Server)")
                self._update_blue_hint()
            else:
                self._fav_update_hints()
                self._update_blue_hint()

        self["hint_info"].setText(_b(""))
        self._update_page_hint()
        pos = self.cur_group_idx if restore_pos and self.cur_group_idx is not None else 0
        self._focus_list(pos)
        self.last_index = -2
        # Einmaliger Timer: nach 300ms nochmals erzwingen, damit VTi das Widget
        # tatsaechlich neu zeichnet (setText direkt nach setList wird ignoriert).
        try:
            self._first_desc_timer.stop()
        except Exception:
            pass
        self._first_desc_timer = eTimer()
        self._first_desc_timer.callback.append(self._force_first_desc)
        self._first_desc_timer.start(300, True)

    def _prefetch_sv_sn(self, mode):
        """Laedt bis zu 1000 Eintraege bevor SV/SN-Datepicker geoeffnet wird."""
        SV_SN_FETCH_SIZE = 1000
        if len(self.all_items) >= SV_SN_FETCH_SIZE or self._fetching:
            # Genug Daten vorhanden oder Fetch laeuft bereits
            if mode == "sv":
                self._open_sv_date_picker()
            else:
                self._open_sn_date_picker()
            return
        self["status_label"].setText("Lade Sendungen ...")
        self._fetching = True
        self._fetch_target = "sv_sn_prefetch"
        self._sv_sn_pending = mode
        self._fetch_result = []
        self._fetch_error = None
        t = threading.Thread(target=self._sv_sn_prefetch_thread, args=(SV_SN_FETCH_SIZE,))
        t.daemon = True
        t.start()
        if self._poll_timer:
            self._poll_timer.start(300, True)

    def _sv_sn_prefetch_thread(self, size):
        try:
            self._fetch_result, self._fetch_total, _rc = self.loader(
                offset=0,
                size=size,
                search_term=None,
                min_duration=0,
                sort_by="timestamp",
            )
        except Exception:
            self._fetch_error = _fmt_exc()
        self._fetching = False

    def _on_sv_sn_prefetch_done(self):
        if self._fetch_error:
            _log("SV/SN Prefetch Fehler: " + self._fetch_error)
            self["status_label"].setText("Fehler beim Laden!")
            return
        self.all_items = self._fetch_result
        self.groups = _build_groups(self.all_items, self.sort_mode)
        self.groups_filtered = list(self.groups)
        self._show_groups()
        if getattr(self, "_sv_sn_pending", None) == "sv":
            self._open_sv_date_picker()
        else:
            self._open_sn_date_picker()

    def _open_sv_date_picker(self):
        import time as _time
        _WEEKDAYS = [
            b"Montag", b"Dienstag", b"Mittwoch", b"Donnerstag",
            b"Freitag", b"Samstag", b"Sonntag",
        ]
        choices = []
        now    = _time.localtime()
        # Mitternacht des heutigen Tages als Ankerpunkt — vermeidet Sommerzeit-Fehler
        today_midnight = int(_time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))
        for i in range(8):
            day_ts = today_midnight - i * 86400
            t   = _time.localtime(day_ts)
            ds  = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
            dsp = "%02d.%02d.%04d" % (t.tm_mday, t.tm_mon, t.tm_year)
            if i == 0:
                label = _b("Heute (%s)" % dsp)
            elif i == 1:
                label = _b("Gestern (%s)" % dsp)
            else:
                wd    = _WEEKDAYS[t.tm_wday]
                label = wd + _b(" (%s)" % dsp)
            choices.append((label, ds))
        self.session.openWithCallback(
            self._on_sv_date_chosen,
            ChoiceBox,
            title="Sendung verpasst? — Datum wählen:",
            list=choices,
        )

    def _on_sv_date_chosen(self, choice):
        if not choice:
            return
        date_str = choice[1]   # "YYYY-MM-DD"
        try:
            import time as _time
            parts = date_str.split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            # Mitternacht bis 23:59:59 in der Lokalzeit der Box
            start_ts = int(_time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)))
            end_ts   = start_ts + 86399
        except Exception:
            self["status_label"].setText(_b("Datum ungueltig!"))
            return

        filtered = [item for item in self.all_items
                    if start_ts <= item.get("timestamp", 0) <= end_ts]

        self._sv_mode = True
        if not filtered:
            self.groups_filtered = []
            self._show_groups()
            self["status_label"].setText(_b("Keine Sendungen am %s" % date_str))
            return

        built = _build_groups(filtered, self.sort_mode, flat=True)
        self.groups_filtered = _relevance_sort(built, self.current_search)
        self._show_groups()

    def _open_sn_date_picker(self):
        import time as _time
        _WEEKDAYS = [
            b"Montag", b"Dienstag", b"Mittwoch", b"Donnerstag",
            b"Freitag", b"Samstag", b"Sonntag",
        ]
        choices = []
        now    = _time.localtime()
        today_midnight = int(_time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1)))
        for i in range(1, 8):
            day_ts = today_midnight + i * 86400
            t   = _time.localtime(day_ts)
            ds  = "%04d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
            dsp = "%02d.%02d.%04d" % (t.tm_mday, t.tm_mon, t.tm_year)
            if i == 1:
                label = _b("Morgen (%s)" % dsp)
            else:
                wd    = _WEEKDAYS[t.tm_wday]
                label = wd + _b(" (%s)" % dsp)
            choices.append((label, ds))
        self.session.openWithCallback(
            self._on_sn_date_chosen,
            ChoiceBox,
            title="Demn\xc3\xa4chst — Datum w\xc3\xa4hlen:",
            list=choices,
        )

    def _on_sn_date_chosen(self, choice):
        if not choice:
            return
        date_str = choice[1]
        try:
            import time as _time
            parts = date_str.split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            # Mitternacht bis 23:59:59 in der Lokalzeit der Box
            start_ts = int(_time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)))
            end_ts   = start_ts + 86399
        except Exception:
            self["status_label"].setText(_b("Datum ungueltig!"))
            return

        filtered = [item for item in self.all_items
                    if start_ts <= item.get("timestamp", 0) <= end_ts]

        self._sn_mode = True
        if not filtered:
            self.groups_filtered = []
            self._show_groups()
            self["status_label"].setText(_b("Keine Sendungen am %s" % date_str))
            return

        built = _build_groups(filtered, self.sort_mode, flat=True)
        self.groups_filtered = _relevance_sort(built, self.current_search)
        self._show_groups()

    def _sv_reset(self):
        """SV/SN-Filter aufheben — zurück zur vollständigen Gruppenansicht."""
        self._sv_mode = False
        self._sn_mode = False
        self.groups_filtered = _relevance_sort(self.groups, self.current_search)
        self._show_groups()

    def _update_page_hint(self):
        if self.mode == MODE_EPISODES:
            if self._ep_sort_mode == "az":
                self["hint_page"].setText(_b(""))
                return
            if self.ep_has_more:
                self["hint_page"].setText("CH+/- Seite %d" % (self.ep_page + 1))
            elif self.ep_page > 0:
                self["hint_page"].setText("Seite %d (letzte)" % (self.ep_page + 1))
            else:
                self["hint_page"].setText(_b(""))
            return
        if self._fav_sort_mode:
            return
        if self.source_name == "Meine Favoriten":
            self["hint_page"].setText(_b(""))
            return
        if self._sv_mode or self._sn_mode:
            self["hint_page"].setText(_b(""))
            return
        if self.mode == MODE_GROUPS and self.sort_mode == "az":
            self["hint_page"].setText(_b(""))
            return
        page_num = self.page + 1
        has_more = self._paged_has_more if self._paged_total > 0 else self._has_more
        if not has_more:
            page_info = "Seite %d (letzte)" % page_num
        else:
            page_info = "CH+/- Seite %d" % page_num
        self["hint_page"].setText(page_info)

    def _start_episode_fetch(self, group_idx):
        if self._fetching:
            return
            
        self.mode = MODE_EPISODES
        self.last_index = -1
        self.cur_group_idx = group_idx + self._sv_sn_offset()
        self.ep_page = 0
        self.ep_total = 0
        self.ep_has_more = False
        self._ep_next_api_offset = 0
        self.sort_mode = "timestamp"
        self._fetching = True
        self._fetch_target = "episodes"
        self._fetch_episodes_result = []
        
        gname, gitems = self.groups_filtered[group_idx]
        self.cur_group_name = gname
        
        try:
            group_str = gname.decode("utf-8", "replace").encode("utf-8")
        except Exception:
            group_str = gname
            
        title_text = self.source_name + b" | " + group_str
        self["title_label"].setText(title_text)
        
        self["status_label"].setText("Lade alle Folgen ...")
            
        self["menu_list"].setList([])
        self["description_text"].setText(_b(""))
        
        self._update_red_hint()
        self._update_page_hint()
        
        t = threading.Thread(target=self._fetch_episodes_thread, args=(gname, gitems))
        t.daemon = True
        t.start()
        if self._poll_timer:
            self._poll_timer.start(300, True)

    def _fetch_episodes_thread(self, gname, local_items):
        try:
            try:
                raw_str = gname.decode("utf-8", "replace")
            except Exception:
                raw_str = str(gname)
            
            # Trennt Sender-Praefixe wie "NDR: " vom eigentlichen Sendungsnamen
            if ": " in raw_str:
                pure_topic = raw_str.split(": ", 1)[1]
            else:
                pure_topic = raw_str

            api_sort = self.sort_mode if self.sort_mode != "az" else "timestamp"

            # Sender aus erstem lokalem Item lesen fuer gezielten Channel-Filter
            ch = None
            if local_items:
                ch_bytes = local_items[0].get("channel", b"") or b""
                try:
                    ch = ch_bytes.decode("utf-8", "replace") or None
                except Exception:
                    ch = None

            # Adaptiver Fetch: solange nachladen bis PAGE_SIZE Treffer gesammelt
            # oder keine weiteren Daten mehr kommen. Jeder Schritt laedt PAGE_SIZE
            # Eintraege, da AD/GS-Filter viele rauswirft.
            exact_items = []
            api_offset = self._ep_next_api_offset
            total = 0
            last_res_full = False
            MAX_ROUNDS = 10  # max. 10x PAGE_SIZE = 1000 Eintraege pro Seite
            for _ in range(MAX_ROUNDS):
                res, total, raw_cnt = _mvw_query(
                    channel=ch,
                    offset=api_offset,
                    size=PAGE_SIZE,
                    search_term=pure_topic,
                    min_duration=self.min_duration,
                    sort_by=api_sort,
                    search_fields=["topic"],
                )
                for item in res:
                    ig = item.get("group", b"")
                    try:
                        ig_str = ig.decode("utf-8", "replace")
                    except Exception:
                        ig_str = str(ig)
                    if ig_str == raw_str:
                        exact_items.append(item)
                api_offset += PAGE_SIZE
                last_res_full = (raw_cnt >= PAGE_SIZE)
                if not last_res_full:
                    break
                if len(exact_items) >= PAGE_SIZE:
                    break
            self.ep_total = total
            self._ep_api_has_more = last_res_full
            self._ep_next_api_offset = api_offset

            if not exact_items:
                exact_items = list(local_items)

            self._fetch_episodes_result = exact_items
            self._fetch_error = None
        except Exception:
            self._fetch_error = _fmt_exc()
            self._fetch_episodes_result = list(local_items)
            
        self._fetching = False

    def _on_episodes_fetch_done(self):
        if self._fetch_error:
            _log("Episoden Fetch Fehler: " + str(self._fetch_error))

        self.cur_episodes = self._fetch_episodes_result
        self.ep_has_more = getattr(self, "_ep_api_has_more", False)
        self._ep_sort_mode = "timestamp"

        is_direct_hits = self.cur_group_name.startswith(b">> Direkte Treffer")
        show_group = is_direct_hits or self.source_name == "Meine Favoriten"
        self["menu_list"].setList([_episode_label(i["title"], i.get("group") if show_group else None, watched=is_watched(i.get("stream_url_hd") or i.get("stream_url_sd") or b"")) for i in self.cur_episodes])

        if self.ep_total > 0 and (self.ep_has_more or self.ep_page > 0):
            self["status_label"].setText(_b("%d  \xc2\xb7  ~%d gesamt" % (len(self.cur_episodes), self.ep_total)))
        else:
            self["status_label"].setText("%d Folgen" % len(self.cur_episodes))
        self["sort_label"].setText(_b("Neueste zuerst"))

        self["hint_red"].setText(_b("Download"))
        if self.source_name != "Meine Favoriten":
            self["hint_yellow"].setText("Suche (Server)")
        else:
            self["hint_yellow"].setText(_b(""))
        self["hint_blue"].setText(_b("Favorit"))
        self._update_ep_sort_hint()

        self._update_page_hint()
        self._update_info_hint()
        self._focus_list(0)
        self.last_index = -2
        try:
            self._first_desc_timer.stop()
        except Exception:
            pass
        self._first_desc_timer = eTimer()
        self._first_desc_timer.callback.append(self._force_first_desc)
        self._first_desc_timer.start(300, True)

    def _focus_list(self, idx=0):
        try:
            self["menu_list"].instance.moveSelectionTo(idx)
        except Exception:
            pass

    def on_download(self):
        global _active_downloader, _download_queue
        if self.mode != MODE_EPISODES:
            return
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is None or idx >= len(self.cur_episodes):
                return
            item = self.cur_episodes[idx]
            url_hd = item.get("stream_url_hd", b"")
            url_sd = item.get("stream_url_sd", b"")
            if isinstance(url_hd, bytes):
                url_hd = url_hd.decode("utf-8", "replace")
            if isinstance(url_sd, bytes):
                url_sd = url_sd.decode("utf-8", "replace")
            url = url_hd if url_hd else url_sd
            if not url:
                self["status_label"].setText(_b("Kein Stream verfügbar"))
                return

            desc = item.get("description", b"")
            dur  = item.get("duration", b"")

            # Läuft bereits ein Download → in Queue einreihen
            if _active_downloader is not None:
                t = _active_downloader._thread
                if t is not None and t.is_alive():
                    _download_queue.append({
                        "title":       item["title"],
                        "url":         url,
                        "topic":       self.cur_group_name,
                        "description": desc,
                        "duration":    dur,
                    })
                    self._show_toast("Zur Warteschlange hinzugef\xc3\xbcgt", added=True)
                    return
                # Thread bereits beendet aber Queue hat noch Items: neuen Download
                # einreihen und Queue komplett abarbeiten (kein Screen öffnen)
                _active_downloader = None
                if _download_queue:
                    _download_queue.append({
                        "title":       item["title"],
                        "url":         url,
                        "topic":       self.cur_group_name,
                        "description": desc,
                        "duration":    dur,
                    })
                    self._show_toast("Zur Warteschlange hinzugef\xc3\xbcgt", added=True)
                    _queue_next()
                    return

            # Kein laufender Download → Screen öffnen
            self.session.open(OeMediathekDownloadScreen, item["title"], url, topic=self.cur_group_name, description=desc, duration=dur)
        except Exception:
            _log("on_download Fehler: " + _fmt_exc())


    def on_ok(self):
        try:
            idx = self["menu_list"].getSelectedIndex()
            _log("on_ok mode=%d idx=%s" % (self.mode, str(idx)))
            if idx is None:
                return
            # Episode-Favoriten-Sortiermodus: OK = Greifen oder Ablegen
            if self._ep_fav_sort_mode:
                if self._ep_fav_grabbed is None:
                    self._ep_fav_grabbed = idx
                    self["menu_list"].setList(self._ep_fav_list_entries())
                    self._focus_list(idx)
                    self._ep_fav_update_hints()
                else:
                    self._ep_fav_grabbed = None
                    self["menu_list"].setList(self._ep_fav_list_entries())
                    self._ep_fav_update_hints()
                return
            # Favoriten-Sortiermodus: OK = Greifen oder Ablegen
            if self._fav_sort_mode:
                if self._fav_grabbed is None:
                    self._fav_grabbed = idx
                    self["menu_list"].setList(self._fav_list_entries())
                    self._focus_list(idx)
                    self._fav_update_hints()
                else:
                    self._fav_grabbed = None
                    self._show_groups(restore_pos=True)
                return
            if self.mode == MODE_GROUPS:
                offset = self._sv_sn_offset()
                if offset >= 1 and idx == 0:
                    self._prefetch_sv_sn("sv")
                elif offset == 2 and idx == 1:
                    self._prefetch_sv_sn("sn")
                elif idx - offset < len(self.groups_filtered):
                    self._start_episode_fetch(idx - offset)
            else:
                if idx < len(self.cur_episodes):
                    item = self.cur_episodes[idx]
                    
                    url_hd = item.get("stream_url_hd", b"")
                    url_sd = item.get("stream_url_sd", b"")
                    
                    options = []
                    if url_hd:
                        options.append(("Hohe Qualitaet (HD)", url_hd))
                    if url_sd and url_sd != url_hd:
                        options.append(("Normale Qualitaet (SD - datensparend)", url_sd))
                        
                    if len(options) > 1:
                        self.session.openWithCallback(
                            lambda ret: self.play_selected_quality(ret, item["title"]),
                            ChoiceBox,
                            title="Qualität wählen:",
                            list=options
                        )
                    elif len(options) == 1:
                        _log("Starte direkt: " + str(item["title"]))
                        play_stream(self.session, options[0][1], item["title"])
                    else:
                        self["status_label"].setText("Kein Stream gefunden!")
                        _log("Kein abspielbarer Stream fuer: " + str(item["title"]))
        except Exception:
            _log("on_ok Fehler: " + _fmt_exc())

    def play_selected_quality(self, ret, title):
        if ret:
            _log("Starte (Auswahl): " + str(title))
            play_stream(self.session, ret[1], title)

    def on_cancel(self):
        if self._ep_fav_sort_mode:
            if self._ep_fav_order_backup is not None:
                self.cur_episodes = list(self._ep_fav_order_backup)
            self._ep_fav_sort_mode = False
            self._ep_fav_grabbed = None
            self._ep_fav_order_backup = None
            self["menu_list"].setList(self._ep_fav_list_entries())
            self._ep_fav_update_hints()
            return
        if self._fav_sort_mode:
            # Sortiermodus abbrechen: Reihenfolge wiederherstellen
            if self._fav_order_backup is not None:
                orig = self._fav_order_backup
                backed = {gname: gitems for gname, gitems in self.groups_filtered}
                self.groups_filtered = [(g, backed.get(g, [])) for g in orig if g in backed]
            self._fav_sort_mode = False
            self._fav_grabbed = None
            self._fav_order_backup = None
            self._show_groups()
            return
        if self.mode == MODE_EPISODES:
            if self._fav_show_episodes:
                self.close()
                return
            self["title_label"].setText(self.source_name)
            self._show_groups(restore_pos=True)
            return
        elif self._sv_mode or self._sn_mode:
            self._sv_reset()
        elif self.alpha_letter:
            self.alpha_letter = None
            self.page = 0
            self._paged_total = 0
            self._paged_has_more = False
            self._groups_next_api_offset = 0
            self.all_items = []
            self.groups = []
            self.groups_filtered = []
            self["menu_list"].setList([])
            self["description_text"].setText(_b(""))
            self._start_fetch()
        else:
            self.close()

    def on_red(self):
        if self.mode == MODE_EPISODES:
            if self._fav_show_episodes:
                self._ep_fav_toggle_sort_mode()
                return
            self.on_download()
            return
        elif self.source_name == "Meine Favoriten":
            self._fav_toggle_sort_mode()
        else:
            self.open_alpha_picker()

    # ------------------------------------------------------------------
    # Favoriten-Sortiermodus
    # ------------------------------------------------------------------
    def _fav_toggle_sort_mode(self):
        if not self._fav_sort_mode:
            # Sortiermodus einschalten
            self._fav_sort_mode = True
            self._fav_grabbed = None
            self._fav_order_backup = [gname for gname, _ in self.groups_filtered]
            self._fav_update_hints()
            self._show_groups(restore_pos=True)
        else:
            # Sortiermodus beenden und speichern
            self._fav_sort_mode = False
            self._fav_grabbed = None
            self._fav_order_backup = None
            reorder_favorites([gname for gname, _ in self.groups_filtered])
            self._show_toast(_b("Reihenfolge gespeichert"), added=True)
            self._show_groups()

    def _fav_update_hints(self):
        if self._fav_sort_mode:
            self["hint_red"].setText(_b("Fertig"))
            self["hint_green"].setText(_b("R\xc3\xbcckg\xc3\xa4ngig"))
            self["hint_yellow"].setText(_b(""))
            self["hint_blue"].setText(_b("Favorit l\xc3\xb6schen"))
            if self._fav_grabbed is None:
                self["hint_page"].setText(_b("OK = Greifen"))
            else:
                self["hint_page"].setText(_b("OK = Ablegen"))
        else:
            self._update_red_hint()
            if self._fav_show_episodes:
                self["hint_yellow"].setText(_b("Folgen > Gruppen"))
            else:
                self["hint_yellow"].setText(_b("Gruppen > Folgen"))
            self["hint_page"].setText(_b(""))

    def _fav_move(self, direction):
        """Gegriffenen Favoriten um eine Position nach oben (-1) oder unten (+1) verschieben."""
        if not self._fav_sort_mode or self._fav_grabbed is None:
            return
        idx = self._fav_grabbed
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.groups_filtered):
            return
        # Tauschen
        self.groups_filtered[idx], self.groups_filtered[new_idx] = \
            self.groups_filtered[new_idx], self.groups_filtered[idx]
        self._fav_grabbed = new_idx
        # Liste neu aufbauen und Cursor auf neuer Position setzen
        self["menu_list"].setList(self._fav_list_entries())
        self._focus_list(new_idx)

    def _fav_list_entries(self):
        """Erstellt die MenuList-Eintraege fuer den Favoriten-Sortiermodus.
        Der gegriffene Eintrag bekommt einen Pfeil-Prefix als visuellen Marker."""
        entries = []
        for i, (gname, _) in enumerate(self.groups_filtered):
            if i == self._fav_grabbed:
                entries.append(_b("\xc2\xbb ") + gname)
            else:
                entries.append(gname)
        return entries

    def _list_len(self):
        if self.mode == MODE_EPISODES:
            return len(self.cur_episodes)
        # SV/SN-Sondereinträge sind in der MenuList, aber nicht in groups_filtered
        return len(self.groups_filtered) + (self._sv_sn_offset() if not self.current_search else 0)

    def on_up(self):
        if self._ep_fav_sort_mode and self._ep_fav_grabbed is not None:
            self._ep_fav_move(-1)
        elif self._fav_sort_mode and self._fav_grabbed is not None:
            self._fav_move(-1)
        else:
            try:
                idx = self["menu_list"].getSelectedIndex()
                if idx == 0:
                    last = self._list_len() - 1
                    if last > 0:
                        self["menu_list"].instance.moveSelectionTo(last)
                else:
                    self["menu_list"].up()
            except Exception:
                pass

    def on_down(self):
        if self._ep_fav_sort_mode and self._ep_fav_grabbed is not None:
            self._ep_fav_move(1)
        elif self._fav_sort_mode and self._fav_grabbed is not None:
            self._fav_move(1)
        else:
            try:
                idx = self["menu_list"].getSelectedIndex()
                n = self._list_len()
                if n > 0 and idx >= n - 1:
                    self["menu_list"].instance.moveSelectionTo(0)
                else:
                    self["menu_list"].down()
            except Exception:
                pass

    def open_alpha_picker(self):
        try:
            self.session.openWithCallback(self.do_alpha_filter, OeMediathekAlphaPickerScreen)
        except Exception:
            _log("open_alpha_picker: " + _fmt_exc())

    def do_alpha_filter(self, letter):
        if letter is None:
            return
        _log("Starte ABC Deep-Fetch fuer: " + letter)
        self.alpha_letter = letter
        self.mode = MODE_GROUPS

        self["status_label"].setText("Suche '%s' ..." % letter)
        self["menu_list"].setList([])
        self["description_text"].setText(_b(""))

        self._fetching = True
        self._fetch_target = "alpha"
        self._fetch_alpha_result = []
        self._fetch_error = None

        t = threading.Thread(target=self._fetch_alpha_thread, args=(letter,))
        t.daemon = True
        t.start()
        if self._poll_timer:
            self._poll_timer.start(300, True)

    def _fetch_alpha_thread(self, letter):
        try:
            api_sort = self.sort_mode if self.sort_mode != "az" else "timestamp"

            def _pure_name(item):
                """Gruppenname ohne Sender-Prefix (z.B. 'ARD: ' entfernen)."""
                group_val = item.get("group") or item.get("title") or b"Sonstige"
                try:
                    g_str = group_val.decode("utf-8", "replace")
                except Exception:
                    g_str = str(group_val)
                if ": " in g_str:
                    return g_str.split(": ", 1)[1]
                return g_str

            # Fuer normale Buchstaben: Buchstabe als search_term, API macht die Arbeit
            # Fuer Sonderzeichen (#): kein search_term moeglich, grosse Menge laden und lokal filtern
            if letter == "#":
                res, _, _rc = self.loader(
                    offset=0,
                    size=2000,
                    search_term=self.current_search,
                    min_duration=self.min_duration,
                    sort_by=api_sort,
                )
                filtered = [
                    item for item in res
                    if _pure_name(item)[0:1].upper() not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                ]
                self._fetch_alpha_result = filtered
                self._fetching = False
                return

            # search_fields=["topic"] damit nur Topics durchsucht werden (nicht Titel)
            # Dadurch kommen alle Gruppen/Topics die den Buchstaben enthalten
            ch = None
            try:
                ch_map = {
                    "ARD Mediathek": "ARD", "ZDF Mediathek": "ZDF", "Arte": "ARTE",
                    "3sat": "3Sat", "NDR Mediathek": "NDR", "WDR Mediathek": "WDR",
                    "BR Mediathek": "BR", "MDR Mediathek": "MDR", "HR Mediathek": "HR",
                    "SWR Mediathek": "SWR", "rbb Mediathek": "RBB", "SR Mediathek": "SR",
                    "ZDF Info": "ZDFinfo", "ZDF Neo": "ZDFneo", "KiKA": "KiKA",
                    "Phoenix": "PHOENIX", "Radio Bremen": "Radio Bremen TV", "funk": "Funk.net",
                    "ARD alpha": "ARD-alpha", "ONE": "ONE", "tagesschau24": "tagesschau24",
                    "DW": "DW", "ORF": "ORF", "SRF": "SRF",
                }
                ch = ch_map.get(self.source_name)
            except Exception:
                pass

            res, _, _rc = _mvw_query(
                channel=ch,
                offset=0,
                size=500,
                search_term=letter,
                min_duration=self.min_duration,
                sort_by=api_sort,
                search_fields=["topic"],
            )

            # Lokal auf exakten Anfangsbuchstaben einengen, Sender-Prefix ignorieren
            filtered = []
            for item in res:
                if _pure_name(item)[0:1].upper() == letter:
                    filtered.append(item)

            self._fetch_alpha_result = filtered
        except Exception:
            self._fetch_error = _fmt_exc()
        self._fetching = False

    def _on_alpha_fetch_done(self):
        if self._fetch_error:
            _log("Alpha Fetch Fehler: " + str(self._fetch_error))
            self["status_label"].setText("Fehler bei der Suche!")
            return

        self.groups = _build_groups(self._fetch_alpha_result, self.sort_mode)
        self.groups_filtered = list(self.groups)

        count = len(self.groups_filtered)
        _log("Alpha Deep-Fetch beendet: %d Gruppen" % count)
        self._show_groups()
        self["status_label"].setText("%d Sendungen  [%s]" % (count, self.alpha_letter))

    def toggle_favorite(self):
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is None:
                return
            # Sondereintraege koennen nicht als Favorit hinzugefuegt werden,
            # aber falls versehentlich gespeichert: loeschen ermoeglichen
            offset = self._sv_sn_offset()
            if idx < offset:
                return
            real_idx = idx - offset
            if real_idx >= len(self.groups_filtered):
                return
            gname, gitems = self.groups_filtered[real_idx]

            # Sondergruppen koennen nicht als Favorit gespeichert werden
            if gname.startswith(b">> "):
                return

            # Kanal direkt aus dem ersten Item der Gruppe lesen — zuverlaessig auch
            # in der Favoriten-Ansicht und bei "Alle Mediatheken"
            channel = b""
            if gitems:
                channel = gitems[0].get("channel", b"") or b""

            if is_favorite(gname):
                remove_favorite(gname)
                self._show_toast("Favorit entfernt", added=False)
                # In der Favoriten-Ansicht den Eintrag sofort aus der Liste entfernen
                if self.source_name == "Meine Favoriten":
                    self.groups = [(n, i) for n, i in self.groups if n != gname]
                    self.groups_filtered = [(n, i) for n, i in self.groups_filtered if n != gname]
                    self._show_groups()
                    # Cursor auf sinnvolle Position setzen (bleibt beim gleichen Index, clamped)
                    new_len = len(self.groups_filtered)
                    if new_len > 0:
                        new_pos = min(real_idx, new_len - 1)
                        self["menu_list"].instance.moveSelectionTo(new_pos)
            else:
                add_favorite(gname, channel)
                self._show_toast("Favorit hinzugefügt!", added=True)
            self._update_red_hint()
            self._update_blue_hint()
        except Exception:
            _log("toggle_favorite: " + _fmt_exc())

    def _show_toast(self, msg, added=True):
        try:
            self._toast_timer.stop()
            if self._saved_status is None:
                try:
                    self._saved_status = self["status_label"].getText()
                except Exception:
                    self._saved_status = ""
            prefix = "[+] " if added else "[-] "
            self["status_label"].setText(_b(prefix + msg))
            self._toast_timer.start(2500, True)
        except Exception:
            pass

    def _clear_toast(self):
        try:
            if self._saved_status is not None:
                self["status_label"].setText(_b(self._saved_status))
                self._saved_status = None
            else:
                self["status_label"].setText(_b(""))
        except Exception:
            pass

    def _update_info_hint(self):
        if self.mode != MODE_EPISODES:
            return
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is not None and idx < len(self.cur_episodes):
                item = self.cur_episodes[idx]
                url = item.get("stream_url_hd") or item.get("stream_url_sd") or b""
                if url and is_watched(url):
                    self["hint_info"].setText(_b("INFO/EPG = Markierung l\xc3\xb6schen"))
                    return
        except Exception:
            pass
        self["hint_info"].setText(_b("INFO/EPG = Markieren"))

    def _ep_az_available(self):
        return True

    def _update_ep_sort_hint(self):
        if self.mode != MODE_EPISODES:
            return
        if self._ep_az_available():
            if self._ep_sort_mode == "az":
                self["hint_green"].setText(_b("A-Z > Neueste zuerst"))
            else:
                self["hint_green"].setText(_b("Neueste zuerst > A-Z"))
        else:
            self["hint_green"].setText(_b(""))

    def cycle_ep_sort(self):
        if not self._ep_az_available():
            return
        if self._ep_sort_mode == "timestamp":
            # Wechsel auf A-Z: alle Episoden laden (auch wenn wir auf Seite 2+)
            if self.ep_has_more or self.ep_page > 0:
                self._start_ep_az_load()
            else:
                self._apply_ep_az_sort()
        else:
            self._ep_sort_mode = "timestamp"
            self.ep_page = 0
            self._ep_next_api_offset = 0
            self.ep_has_more = False
            self["menu_list"].setList([])
            self["description_text"].setText(_b(""))
            self._start_episode_fetch(self.cur_group_idx - self._sv_sn_offset())

    @staticmethod
    def _ep_sort_key(i):
        import re
        title = (i.get("title") or b"").decode("utf-8", "replace")
        m = re.search(r'\(S(\d+)/E(\d+)\)', title)
        if m:
            return (0, int(m.group(1)), int(m.group(2)), title.lower())
        return (1, 0, 0, title.lower())

    def _apply_ep_az_sort(self):
        self._ep_sort_mode = "az"
        is_direct_hits = self.cur_group_name.startswith(b">> Direkte Treffer")
        show_group = is_direct_hits or self.source_name == "Meine Favoriten"
        self.cur_episodes = sorted(self.cur_episodes, key=self._ep_sort_key)
        self["sort_label"].setText(_b("A-Z"))
        self["menu_list"].setList([_episode_label(i["title"], i.get("group") if show_group else None, watched=is_watched(i.get("stream_url_hd") or i.get("stream_url_sd") or b"")) for i in self.cur_episodes])
        self._update_ep_sort_hint()

    def _start_ep_az_load(self):
        if self._fetching:
            return
        real_idx = self.cur_group_idx - self._sv_sn_offset()
        if real_idx < 0 or real_idx >= len(self.groups_filtered):
            return
        gname, gitems = self.groups_filtered[real_idx]
        self._fetching = True
        self._fetch_target = "ep_az_load"
        self._fetch_episodes_result = []
        self._fetch_error = None
        self["status_label"].setText("Lade alle Folgen ...")
        self["hint_green"].setText(_b(""))
        t = threading.Thread(target=self._fetch_ep_az_thread, args=(gname, gitems))
        t.daemon = True
        t.start()
        if self._poll_timer:
            self._poll_timer.start(300, True)

    def _fetch_ep_az_thread(self, gname, local_items):
        try:
            try:
                raw_str = gname.decode("utf-8", "replace")
            except Exception:
                raw_str = str(gname)
            if ": " in raw_str:
                pure_topic = raw_str.split(": ", 1)[1]
            else:
                pure_topic = raw_str
            ch = None
            if local_items:
                ch_bytes = local_items[0].get("channel", b"") or b""
                try:
                    ch = ch_bytes.decode("utf-8", "replace") or None
                except Exception:
                    ch = None
            exact_items = []
            api_offset = 0
            MAX_ROUNDS = 10  # bis zu 1000 API-Eintraege durchsuchen
            for _ in range(MAX_ROUNDS):
                res, total, raw_cnt = _mvw_query(
                    channel=ch,
                    offset=api_offset,
                    size=PAGE_SIZE,
                    search_term=pure_topic,
                    min_duration=self.min_duration,
                    sort_by="timestamp",
                    search_fields=["topic"],
                )
                for item in res:
                    ig = item.get("group", b"")
                    try:
                        ig_str = ig.decode("utf-8", "replace")
                    except Exception:
                        ig_str = str(ig)
                    if ig_str == raw_str:
                        exact_items.append(item)
                api_offset += PAGE_SIZE
                if raw_cnt < PAGE_SIZE:
                    break
            _log("ep_az: raw_str=%r exact=%d local=%d" % (raw_str, len(exact_items), len(local_items)))
            self._fetch_episodes_result = exact_items if exact_items else list(local_items)
            self._fetch_error = None
        except Exception:
            self._fetch_error = _fmt_exc()
            self._fetch_episodes_result = list(local_items)
        self._fetching = False

    def _on_ep_az_load_done(self):
        if self._fetch_error:
            _log("ep_az_load Fehler: " + str(self._fetch_error))
        self.cur_episodes = self._fetch_episodes_result
        self.ep_has_more = False
        self.ep_page = 0
        self["status_label"].setText("%d Folgen" % len(self.cur_episodes))
        self._apply_ep_az_sort()
        self._update_page_hint()
        self._update_info_hint()

    def _update_blue_hint(self):
        if self.mode == MODE_EPISODES:
            try:
                idx = self["menu_list"].getSelectedIndex()
                if idx is not None and idx < len(self.cur_episodes):
                    item = self.cur_episodes[idx]
                    url = item.get("stream_url_hd") or item.get("stream_url_sd") or b""
                    if url and is_episode_favorite(url):
                        self["hint_blue"].setText(_b("Favorit l\xc3\xb6schen"))
                        return
            except Exception:
                pass
            self["hint_blue"].setText(_b("Favorit"))
            return
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is not None:
                offset = self._sv_sn_offset()
                if idx < offset:
                    self["hint_blue"].setText(_b(""))
                    return
                real_idx = idx - offset
                if real_idx < len(self.groups_filtered):
                    gname, _ = self.groups_filtered[real_idx]
                    if gname.startswith(b">> "):
                        self["hint_blue"].setText(_b(""))
                        return
                    if is_favorite(gname):
                        self["hint_blue"].setText(_b("Favorit l\xc3\xb6schen"))
                        return
        except Exception:
            pass
        self["hint_blue"].setText(_b("Favorit"))

    def _update_red_hint(self):
        if self.mode == MODE_EPISODES:
            self["hint_red"].setText(_b("Download"))
        elif self.source_name == "Meine Favoriten":
            if self._fav_sort_mode:
                self["hint_red"].setText(_b("Fertig"))
            else:
                self["hint_red"].setText(_b("Sortieren"))
        else:
            self["hint_red"].setText("ABC-Auswahl")

    def next_page(self):
        if self._fetching:
            return
        if self.mode == MODE_EPISODES:
            if self._ep_sort_mode == "az":
                return
            if not self.ep_has_more:
                return
            self.ep_page += 1
            self._start_episode_page_fetch()
            return
        if not self._has_more:
            _log("Keine weiteren Seiten")
            return
        if self._sv_mode or self._sn_mode:
            return
        if self.mode == MODE_GROUPS and self.sort_mode == "az":
            return
        self.page += 1
        self._start_fetch()

    def prev_page(self):
        if self._fetching:
            return
        if self.mode == MODE_EPISODES:
            if self._ep_sort_mode == "az":
                return
            if self.ep_page == 0:
                return
            self.ep_page -= 1
            self._ep_next_api_offset = self.ep_page * PAGE_SIZE
            self._start_episode_page_fetch()
            return
        if self.page == 0:
            return
        if self._sv_mode or self._sn_mode:
            return
        if self.mode == MODE_GROUPS and self.sort_mode == "az":
            return
        self.page -= 1
        self._groups_next_api_offset = self.page * (PAGE_SIZE * 5)
        self._paged_total = 0
        self._paged_has_more = False
        self.all_items = []
        self._start_fetch()

    def _start_episode_page_fetch(self):
        """Laedt eine andere Seite der aktuell geoeffneten Gruppe nach."""
        if self._fetching:
            return
        # Aktuelle Gruppe aus groups_filtered holen
        real_idx = self.cur_group_idx - self._sv_sn_offset()
        if real_idx < 0 or real_idx >= len(self.groups_filtered):
            return
        gname, gitems = self.groups_filtered[real_idx]
        self._fetching = True
        self._fetch_target = "episodes"
        self._fetch_episodes_result = []
        self["status_label"].setText("Lade Seite %d ..." % (self.ep_page + 1))
        self["menu_list"].setList([])
        self._update_page_hint()
        t = threading.Thread(target=self._fetch_episodes_thread, args=(gname, gitems))
        t.daemon = True
        t.start()
        if self._poll_timer:
            self._poll_timer.start(300, True)

    # In der Episodenansicht nur "timestamp" (kein "az" — wuerde nur aktuelle Seite sortieren)
    _SORT_CYCLE_GROUPS   = ["timestamp", "az"]
    _SORT_CYCLE_EPISODES = ["timestamp"]
    _SORT_LABELS = {
        "timestamp": "Neueste zuerst",
        "az":        "A-Z",
    }

    def _update_sort_label(self):
        if self.mode == MODE_GROUPS:
            self["sort_label"].setText(_b(self._current_sort_label()))

    def _current_sort_label(self):
        return OeMediathekScreen._SORT_LABELS.get(self.sort_mode, "Neueste zuerst")

    def _next_sort_hint(self):
        """Gibt das Label des naechsten Sortier-Modus zurueck (ohne Prefix)."""
        cycle = self._SORT_CYCLE_GROUPS if self.mode == MODE_GROUPS else self._SORT_CYCLE_EPISODES
        if len(cycle) <= 1:
            return ""
        idx = cycle.index(self.sort_mode) if self.sort_mode in cycle else 0
        next_mode = cycle[(idx + 1) % len(cycle)]
        return OeMediathekScreen._SORT_LABELS.get(next_mode, "Neueste zuerst")

    def cycle_sort(self):
        try:
            # Im Episode-Favoriten-Sortiermodus: Gruen = Rückgängig
            if self._ep_fav_sort_mode:
                if self._ep_fav_order_backup is not None:
                    self.cur_episodes = list(self._ep_fav_order_backup)
                    self._ep_fav_grabbed = None
                    self["menu_list"].setList(self._ep_fav_list_entries())
                    self._ep_fav_update_hints()
                    self._show_toast(_b("Reihenfolge zur\xc3\xbcckgesetzt"), added=True)
                return
            # Im Favoriten-Sortiermodus: Gruen = Rückgängig
            if self._fav_sort_mode:
                if self._fav_order_backup is not None:
                    backed = {gname: gitems for gname, gitems in self.groups_filtered}
                    self.groups_filtered = [(g, backed.get(g, [])) for g in self._fav_order_backup if g in backed]
                    self._fav_grabbed = None
                    self["menu_list"].setList(self._fav_list_entries())
                    self._fav_update_hints()
                    self._show_toast(_b("Reihenfolge zur\xc3\xbcckgesetzt"), added=True)
                return
            cycle = self._SORT_CYCLE_GROUPS if self.mode == MODE_GROUPS else self._SORT_CYCLE_EPISODES
            if len(cycle) <= 1:
                return
            idx = cycle.index(self.sort_mode) if self.sort_mode in cycle else 0
            self.sort_mode = cycle[(idx + 1) % len(cycle)]
            _log("Sortierung: " + self.sort_mode)

            if self.mode == MODE_GROUPS:
                if self.sort_mode == "az":
                    if self.alpha_letter:
                        # ABC-Modus: nur lokale Daten neu sortieren, kein neuer Fetch
                        self.groups = _build_groups(self._fetch_alpha_result, self.sort_mode)
                        self.groups_filtered = list(self.groups)
                        self._show_groups()
                        self["status_label"].setText("%d Sendungen  [%s]" % (len(self.groups_filtered), self.alpha_letter))
                    else:
                        # A-Z: vollstaendigen Fetch mit size=1000 neu starten
                        self.page = 0
                        self._az_items = []
                        self.groups = []
                        self.groups_filtered = []
                        self["menu_list"].setList([])
                        self["description_text"].setText(_b(""))
                        self._start_fetch()
                else:
                    # Neueste zuerst: bei alpha-Filter lokale Daten reichen, sonst neu fetchen
                    if self.alpha_letter:
                        self.groups = _build_groups(self._fetch_alpha_result, self.sort_mode)
                        self.groups_filtered = list(self.groups)
                        self._show_groups()
                    else:
                        # Frischen Fetch starten damit Paging und _has_more korrekt sind
                        self.page = 0
                        self._groups_next_api_offset = 0
                        self._paged_total = 0
                        self._paged_has_more = False
                        self.all_items = []
                        self.groups = []
                        self.groups_filtered = []
                        self["menu_list"].setList([])
                        self["description_text"].setText(_b(""))
                        self._start_fetch()
            else:
                self.ep_page = 0
                self["menu_list"].setList([])
                self["description_text"].setText(_b(""))
                self._start_episode_fetch(self.cur_group_idx - self._sv_sn_offset())
        except Exception:
            _log("cycle_sort: " + _fmt_exc())

    def on_green(self):
        if self._ep_fav_sort_mode:
            self.cycle_sort()
        elif self.mode == MODE_EPISODES:
            self.cycle_ep_sort()
        else:
            self.cycle_sort()

    def on_info(self):
        if self.mode == MODE_EPISODES:
            self.on_toggle_watched()

    def on_toggle_watched(self):
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is None or idx >= len(self.cur_episodes):
                return
            item = self.cur_episodes[idx]
            url = item.get("stream_url_hd") or item.get("stream_url_sd") or b""
            if not url:
                return
            toggle_watched(url)
            is_direct_hits = self.cur_group_name.startswith(b">> Direkte Treffer")
            show_group = is_direct_hits or self.source_name == "Meine Favoriten"
            self["menu_list"].setList([_episode_label(i["title"], i.get("group") if show_group else None, watched=is_watched(i.get("stream_url_hd") or i.get("stream_url_sd") or b"")) for i in self.cur_episodes])
            self["menu_list"].moveToIndex(idx)
            self._update_info_hint()
        except Exception:
            _log("on_toggle_watched: " + _fmt_exc())

    def toggle_episode_favorite(self):
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is None or idx >= len(self.cur_episodes):
                return
            item = self.cur_episodes[idx]
            url = item.get("stream_url_hd") or item.get("stream_url_sd") or b""
            if not url:
                return
            if is_episode_favorite(url):
                remove_episode_favorite(url)
                self._show_toast(_b("Favorit entfernt"), added=False)
                if self.source_name == "Meine Favoriten" and self._fav_show_episodes:
                    self.cur_episodes = [i for i in self.cur_episodes if (i.get("stream_url_hd") or i.get("stream_url_sd") or b"") != url]
                    self["menu_list"].setList([_episode_label(i["title"], i.get("group"), watched=is_watched(i.get("stream_url_hd") or i.get("stream_url_sd") or b"")) for i in self.cur_episodes])
                    self["status_label"].setText(_b("%d Einzelfolgen" % len(self.cur_episodes)))
                    new_idx = min(idx, len(self.cur_episodes) - 1)
                    if new_idx >= 0:
                        self["menu_list"].instance.moveSelectionTo(new_idx)
            else:
                add_episode_favorite(item)
                self._show_toast(_b("Favorit hinzugef\xc3\xbcgt!"), added=True)
            self._update_blue_hint()
        except Exception:
            _log("toggle_episode_favorite: " + _fmt_exc())

    def on_blue(self):
        if self.mode == MODE_EPISODES:
            self.toggle_episode_favorite()
        else:
            self.toggle_favorite()

    def on_yellow(self):
        if self.source_name == "Meine Favoriten" and not self._fav_sort_mode:
            self._fav_toggle_episode_view()
        else:
            self.open_search()

    def _fav_toggle_episode_view(self):
        if self._fav_sort_mode or self._ep_fav_sort_mode:
            return
        self._fav_show_episodes = not self._fav_show_episodes
        self._fav_update_hints()
        if self._fav_show_episodes:
            self._show_episode_favorites()
        else:
            self._reload_favorites()

    def _show_episode_favorites(self):
        items = get_episode_favorites()
        self["status_label"].setText(_b("%d Einzelfolgen" % len(items)))
        self["menu_list"].setList([_episode_label(i["title"], i.get("group"), watched=is_watched(i.get("stream_url_hd") or i.get("stream_url_sd") or b"")) for i in items])
        self.cur_episodes = items
        self.mode = MODE_EPISODES
        self.last_index = -1
        self._ep_fav_sort_mode = False
        self._ep_fav_grabbed   = None
        self._ep_fav_order_backup = None
        self._ep_fav_update_hints()
        self._update_info_hint()

    def _ep_fav_update_hints(self):
        if self._ep_fav_sort_mode:
            self["hint_red"].setText(_b("Fertig"))
            self["hint_green"].setText(_b("R\xc3\xbcckg\xc3\xa4ngig"))
            self["hint_yellow"].setText(_b(""))
            self["hint_blue"].setText(_b("Favorit l\xc3\xb6schen"))
            if self._ep_fav_grabbed is None:
                self["hint_page"].setText(_b("OK = Greifen"))
            else:
                self["hint_page"].setText(_b("OK = Ablegen"))
        else:
            self["hint_red"].setText(_b("Sortieren"))
            self["hint_green"].setText(_b(""))
            self["hint_yellow"].setText(_b("Folgen > Gruppen"))
            self["hint_blue"].setText(_b("Favorit l\xc3\xb6schen"))
            self["hint_info"].setText(_b("INFO/EPG = Markieren"))
            self["hint_page"].setText(_b(""))

    def _ep_fav_toggle_sort_mode(self):
        if not self._ep_fav_sort_mode:
            self._ep_fav_sort_mode = True
            self._ep_fav_grabbed = None
            self._ep_fav_order_backup = list(self.cur_episodes)
            self._ep_fav_update_hints()
            self["menu_list"].setList(self._ep_fav_list_entries())
        else:
            self._ep_fav_sort_mode = False
            self._ep_fav_grabbed = None
            self._ep_fav_order_backup = None
            from mediathek import _save_episode_favorites, _item_to_unicode
            _save_episode_favorites([_item_to_unicode(i) for i in self.cur_episodes])
            self._show_toast(_b("Reihenfolge gespeichert"), added=True)
            self["menu_list"].setList(self._ep_fav_list_entries())
            self._ep_fav_update_hints()

    def _ep_fav_list_entries(self):
        entries = []
        for i, item in enumerate(self.cur_episodes):
            label = _episode_label(item["title"], item.get("group"), watched=is_watched(item.get("stream_url_hd") or item.get("stream_url_sd") or b""))
            if i == self._ep_fav_grabbed:
                label = _b("\xc2\xbb ") + label
            entries.append(label)
        return entries

    def _ep_fav_move(self, direction):
        if not self._ep_fav_sort_mode or self._ep_fav_grabbed is None:
            return
        idx = self._ep_fav_grabbed
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.cur_episodes):
            return
        self.cur_episodes[idx], self.cur_episodes[new_idx] = \
            self.cur_episodes[new_idx], self.cur_episodes[idx]
        self._ep_fav_grabbed = new_idx
        self["menu_list"].setList(self._ep_fav_list_entries())
        self._focus_list(new_idx)

    def _reload_favorites(self):
        self.mode = MODE_GROUPS
        self.page = 0
        self._paged_total = 0
        self._paged_has_more = False
        self.all_items = []
        self.groups = []
        self.groups_filtered = []
        self["menu_list"].setList([])
        self["description_text"].setText(_b(""))
        self._start_fetch()

    def open_search(self):
        if self.source_name == "Meine Favoriten":
            return
        try:
            self.session.openWithCallback(
                self._on_history_choice,
                OeMediathekSearchHistoryScreen,
            )
        except Exception:
            _log("open_search: " + _fmt_exc())

    def _on_history_choice(self, choice):
        """Callback vom History-Screen: None=Abbruch, '__new__'=Tastatur, sonst direkt suchen."""
        if choice is None:
            return
        if choice == "__new__":
            try:
                self.session.openWithCallback(
                    self.do_search, VirtualKeyBoard,
                    title="Suchen:", text="",
                )
            except Exception:
                _log("open_search VirtualKeyBoard: " + _fmt_exc())
        else:
            self.do_search(choice)

    def do_search(self, term):
        try:
            if term is not None:
                # Sicherstellen dass term ein str ist (kein unicode in Python 2)
                if isinstance(term, bytes):
                    term = term.decode("utf-8", "replace")
                try:
                    if isinstance(term, unicode):
                        term = term.encode("utf-8")
                except NameError:
                    pass  # Python 3: unicode == str, kein encode noetig
                term = term.strip()
                if not term:
                    self.current_search = None
                else:
                    self.current_search = term
                    save_search_history(term)

                self.page = 0
                self._paged_total = 0
                self._paged_has_more = False
                self._groups_next_api_offset = 0
                self.all_items = []
                self.groups = []
                self.groups_filtered = []
                self["menu_list"].setList([])
                self["description_text"].setText(_b(""))
                self._start_fetch()
        except Exception:
            _log("do_search: " + _fmt_exc())


# --------------------------------------------------------------------------
# Dateibrowser für Ordnerauswahl
# --------------------------------------------------------------------------

class OeMediathekDirBrowser(Screen):
    if IS_FHD:
        skin = """
        <screen name="OeMediathekDirBrowser" position="260,140" size="1400,800" flags="wfNoBorder">
            <eLabel position="0,0" size="1400,800" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label" position="40,20" size="1320,60" font="Regular;38" halign="center" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="path_label" position="40,90" size="1320,50" font="Regular;32" foregroundColor="#AAAAAA" transparent="1" />
            <widget name="menu_list" position="40,150" size="1320,560" font="Regular;34" scrollbarMode="showOnDemand" itemHeight="58" backgroundColor="#33000000" transparent="1" />
            <widget name="hint_label" position="40,730" size="1320,50" font="Regular;32" halign="center" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""
    else:
        skin = """
        <screen name="OeMediathekDirBrowser" position="173,93" size="933,534" flags="wfNoBorder">
            <eLabel position="0,0" size="933,534" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label" position="27,13" size="880,40" font="Regular;25" halign="center" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="path_label" position="27,60" size="880,33" font="Regular;21" foregroundColor="#AAAAAA" transparent="1" />
            <widget name="menu_list" position="27,100" size="880,373" font="Regular;22" scrollbarMode="showOnDemand" itemHeight="38" backgroundColor="#33000000" transparent="1" />
            <widget name="hint_label" position="27,487" size="880,33" font="Regular;21" halign="center" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""

    def __init__(self, session, start_dir=None):
        Screen.__init__(self, session)
        self._cur = start_dir or "/"

        self["title_label"] = Label(_b("Ordner auswählen"))
        self["path_label"]  = Label(_b(self._cur))
        self["menu_list"]   = MenuList([])
        self["hint_label"]  = Label(_b("OK = Öffnen/Wählen   |   Gelb = Neuer Ordner   |   EXIT = Abbrechen"))

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok":     self._on_ok,
                "cancel": self._on_cancel,
                "yellow": self._new_folder,
            },
            -1,
        )

        self._entries = []
        self._fill(self._cur)
        self.onClose.append(self._on_close_cb)
        self._result = None

    @staticmethod
    def _normalize_path(path):
        """Pfad immer als UTF-8 Byte-String zurückgeben (Python 2 kompatibel)."""
        if isinstance(path, unicode):
            return path.encode("utf-8")
        return path

    def _fill(self, path):
        path = self._normalize_path(path)
        self._cur = path  # _cur immer synchron halten
        entries = []
        # ".." falls nicht Wurzel
        if path not in (b"/", "/"):
            entries.append(("[..] Übergeordneter Ordner", None))
        # "Hier speichern" direkt oben — nicht erst nach Scrollen durch Dateien
        entries.append(("»  Hier speichern", path))
        try:
            names = sorted(os.listdir(path))
            for name in names:
                if isinstance(name, unicode):
                    name = name.encode("utf-8")
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    try:
                        label = "[" + name.decode("utf-8", "replace") + "]"
                    except Exception:
                        label = "[" + repr(name) + "]"
                    entries.append((label, full))
        except Exception:
            _log("DirBrowser _fill Fehler: " + _fmt_exc())

        self._entries = entries
        self["menu_list"].setList([_b(e[0]) for e in entries])
        self["path_label"].setText(_b(path))

    def _on_ok(self):
        idx = self["menu_list"].getSelectedIndex()
        if idx is None or idx >= len(self._entries):
            return
        label, full = self._entries[idx]
        if full is None:
            # ".." — eine Ebene hoch
            cur = self._cur if isinstance(self._cur, str) else self._cur.decode("utf-8", "replace")
            parent = os.path.dirname(cur.rstrip("/")) or "/"
            self._fill(parent)
        elif full == self._cur:
            # "Hier speichern"
            self._result = self._cur
            self.close()
        else:
            self._cur = full
            self._fill(full)

    def _new_folder(self):
        try:
            self.session.openWithCallback(self._create_folder, VirtualKeyBoard,
                title="Neuer Ordnername:", text="")
        except Exception:
            _log("DirBrowser _new_folder: " + _fmt_exc())

    def _create_folder(self, name):
        if not name:
            return
        name = name.strip()
        if not name:
            return
        try:
            new_path = os.path.join(self._cur, self._normalize_path(name))
            os.makedirs(new_path)
            self._fill(self._cur)
        except Exception as e:
            _log("DirBrowser _create_folder: " + str(e))

    def _on_cancel(self):
        self._result = None
        self.close()

    def _on_close_cb(self):
        pass

    def doClose(self):
        try:
            Screen.doClose(self)
        except TypeError:
            pass


# --------------------------------------------------------------------------
# Settings-Screen
# --------------------------------------------------------------------------

class OeMediathekSettingsScreen(Screen):

    # Einträge: (label_text, action_id, status_fn_or_None)
    # action_id: 0=Browse, 1=ToggleConvert, 2=ResetOrder, 3=ToggleWrapLR
    _ENTRIES = [
        ("Download-Ordner",               0, None),
        ("MP4 -> TS Konvertierung:",       1, get_auto_convert),
        ("Seite wechseln mit Links/Rechts:", 3, get_tile_wrap_lr),
        ("Reihenfolge zur\xc3\xbccksetzen", 2, None),
    ]

    # Layout-Konstanten je Auflösung: (x, y_first_row, row_h, font_title, font_row, font_hint, w, h, status_w)
    if IS_FHD:
        _L = (40, 115, 56, 42, 34, 28, 800, 516, 120)
    else:
        _L = (27,  76, 37, 28, 22, 19, 534, 344,  80)

    @classmethod
    def _make_skin(cls):
        x, y0, rh, ft, fr, fh, w, h, sw = cls._L
        iw = w - 2 * x        # innere Breite
        lw = iw - sw - 10     # Label-Breite (links)
        n  = len(cls._ENTRIES)
        list_h = n * rh
        y_line1 = y0 - 5
        y_hint  = y0 + list_h + 10
        y_line2 = y_hint - 5
        total_h = y_hint + fh + 15

        rows = ""
        for i in range(n):
            ry = y0 + i * rh
            rows += """
            <widget name="sel_{i}"   position="{x},{ry}"  size="{iw},{rh}" backgroundColor="#1A0066FF" zPosition="-3" />
            <widget name="lbl_{i}"   position="{x},{ry}"  size="{lw},{rh}" font="Regular;{fr}" valign="center" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="stat_{i}"  position="{sx},{ry}" size="{sw},{rh}" font="Regular;{fr}" valign="center" halign="right" foregroundColor="#FFDD00" transparent="1" />
            """.format(i=i, x=x, ry=ry, iw=iw, rh=rh, lw=lw, sx=x+lw+10, sw=sw, fr=fr)

        return """
        <screen name="OeMediathekSettingsScreen" position="{px},{py}" size="{w},{total_h}" flags="wfNoBorder">
            <eLabel position="0,0" size="{w},{total_h}" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label" position="{x},10" size="{iw},{ft_h}" font="Regular;{ft}" halign="center" foregroundColor="#FFFFFF" transparent="1" />
            <eLabel position="{x},{y_line1}" size="{iw},2" backgroundColor="#44FFFFFF" zPosition="-4" />
            {rows}
            <eLabel position="{x},{y_line2}" size="{iw},2" backgroundColor="#44FFFFFF" zPosition="-4" />
            <widget name="hint_label" position="{x},{y_hint}" size="{iw},{fh}" font="Regular;{fh_size}" halign="center" foregroundColor="#AAAAAA" transparent="1" />
        </screen>""".format(
            px=(1920 - w) // 2 if IS_FHD else (1280 - w) // 2,
            py=(1080 - total_h) // 2 if IS_FHD else (720 - total_h) // 2,
            w=w, total_h=total_h, x=x, iw=iw,
            ft=ft, ft_h=ft + 10,
            y_line1=y_line1, y_line2=y_line2,
            rows=rows,
            y_hint=y_hint, fh=fh + 4, fh_size=fh,
        )

    def __init__(self, session):
        self.skin = self._make_skin()
        Screen.__init__(self, session)
        self._sel = 0

        self["title_label"] = Label(_b("Einstellungen"))
        self["hint_label"]  = Label(_b("OK = Ausw\xc3\xa4hlen   |   EXIT = Schlie\xc3\x9fen"))

        for i, (label, _, _fn) in enumerate(self._ENTRIES):
            self["lbl_%d"  % i] = Label(_b(label))
            self["stat_%d" % i] = Label(_b(""))
            self["sel_%d"  % i] = Label(_b(""))
            self["sel_%d"  % i].hide()

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions"],
            {
                "ok":     self._on_ok,
                "cancel": self.close,
                "up":     self._move_up,
                "down":   self._move_down,
            },
            -1,
        )
        self.onShow.append(self._refresh)

    def _refresh(self):
        for i, (_label, _aid, fn) in enumerate(self._ENTRIES):
            if fn is not None:
                self["stat_%d" % i].setText(_b("[EIN]" if fn() else "[AUS]"))
            else:
                self["stat_%d" % i].setText(_b(""))
        self._update_highlight()

    def _update_highlight(self):
        for i in range(len(self._ENTRIES)):
            try:
                if i == self._sel:
                    self["sel_%d" % i].show()
                else:
                    self["sel_%d" % i].hide()
            except Exception:
                pass

    def _move_up(self):
        self._sel = (self._sel - 1) % len(self._ENTRIES)
        self._update_highlight()

    def _move_down(self):
        self._sel = (self._sel + 1) % len(self._ENTRIES)
        self._update_highlight()

    def _on_ok(self):
        _, action_id, _ = self._ENTRIES[self._sel]
        if action_id == 0:
            self._browse()
        elif action_id == 1:
            self._toggle_convert()
        elif action_id == 3:
            self._toggle_tile_wrap_lr()
        elif action_id == 2:
            self._reset_order()

    def _browse(self):
        try:
            cur = get_save_dir()
            start = cur
            while start and start != "/" and not os.path.isdir(start):
                start = os.path.dirname(start)
            if not start or not os.path.isdir(start):
                start = "/media"
            self._browser = self.session.open(OeMediathekDirBrowser, start)
            self._browser.onClose.append(self._dir_browser_closed)
        except Exception:
            _log("Settings _browse: " + _fmt_exc())

    def _dir_browser_closed(self):
        try:
            result = self._browser._result
            if result:
                set_save_dir(result)
        except Exception:
            _log("Settings _dir_browser_closed: " + _fmt_exc())

    def _toggle_convert(self):
        set_auto_convert(not get_auto_convert())
        self._refresh()

    def _toggle_tile_wrap_lr(self):
        set_tile_wrap_lr(not get_tile_wrap_lr())
        self._refresh()

    def _reset_order(self):
        try:
            if os.path.exists(OeMediathekMainScreen._ORDER_FILE):
                os.remove(OeMediathekMainScreen._ORDER_FILE)
            # SOURCES auf Original-Reihenfolge zurücksetzen
            SOURCES[:] = _SOURCES_DEFAULT[:]
            _log("Reihenfolge auf Standard zurückgesetzt")
        except Exception as e:
            _log("Settings reset_order: " + str(e))
        self.close()


    def doClose(self):
        try:
            Screen.doClose(self)
        except TypeError:
            pass


# --------------------------------------------------------------------------
# Download-Screen
# --------------------------------------------------------------------------

class OeMediathekDownloadScreen(Screen):
    if IS_FHD:
        skin = """
        <screen name="OeMediathekDownloadScreen" position="460,300" size="1000,450" flags="wfNoBorder">
            <eLabel position="0,0" size="1000,450" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label" position="40,30" size="920,110" font="Regular;36" halign="center" valign="top" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="status_label" position="40,160" size="920,170" font="Regular;34" halign="center" valign="center" foregroundColor="#AAAAAA" transparent="1" />
            <eLabel position="200,383" size="8,28" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow" position="216,380" size="260,36" font="Regular;26" halign="left" foregroundColor="#CCCCCC" transparent="1" />
            <widget name="hint_label" position="520,380" size="280,36" font="Regular;26" halign="left" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""
    else:
        skin = """
        <screen name="OeMediathekDownloadScreen" position="307,200" size="666,300" flags="wfNoBorder">
            <eLabel position="0,0" size="666,300" backgroundColor="#33000000" zPosition="-6" />
            <widget name="title_label" position="27,20" size="613,76" font="Regular;24" halign="center" valign="top" foregroundColor="#FFFFFF" transparent="1" />
            <widget name="status_label" position="27,106" size="613,120" font="Regular;22" halign="center" valign="center" foregroundColor="#AAAAAA" transparent="1" />
            <eLabel position="130,258" size="5,20" backgroundColor="#FFD700" zPosition="2" />
            <widget name="hint_yellow" position="140,254" size="175,28" font="Regular;19" halign="left" foregroundColor="#CCCCCC" transparent="1" />
            <widget name="hint_label" position="345,254" size="190,28" font="Regular;19" halign="left" foregroundColor="#AAAAAA" transparent="1" />
        </screen>"""

    def __init__(self, session, title, url, topic=None, description=None, duration=None):
        Screen.__init__(self, session)
        self._url         = url
        self._topic       = topic
        self._description = description
        self._duration    = duration
        self._done  = False
        self._err   = None

        # Shared state zwischen Thread und Hauptthread (nur schreiben im Thread, lesen im Timer)
        self._dl_downloaded  = 0
        self._dl_total       = 0
        self._dl_done        = False
        self._dl_err         = None
        self._dl_filepath    = None
        self._dl_converting  = False

        if isinstance(title, bytes):
            title_str = title.decode("utf-8", "replace")
        else:
            title_str = title
        self._title_str = title_str

        self["title_label"]  = Label(_b(title_str))
        self["status_label"] = Label(_b("Starte Download ..."))
        self["hint_yellow"]  = Label(_b("Im Hintergrund"))
        self["hint_label"]   = Label(_b("EXIT = Abbrechen"))

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self._on_cancel,
                "ok":     self._on_cancel,
                "yellow": self._to_background,
            },
            -1,
        )

        self._downloader = None

        # Einmaliger Start-Timer
        self._start_timer = eTimer()
        self._start_timer.callback.append(self._start_download)
        self._start_timer.start(300, True)

        # Poll-Timer: aktualisiert UI aus dem Hauptthread
        self._poll_timer = eTimer()
        self._poll_timer.callback.append(self._poll)

        self.onClose.append(self.__stop_timers)

    def __stop_timers(self):
        try:
            self._start_timer.stop()
        except Exception:
            pass
        try:
            self._poll_timer.stop()
        except Exception:
            pass

    def _start_download(self):
        try:
            self._downloader = Downloader(
                self._url,
                self._title_str,
                topic=self._topic,
                description=self._description,
                duration=self._duration,
                on_progress=self._cb_progress,
                on_done=self._cb_done,
                on_error=self._cb_error,
            )
            self._downloader.start()
            # Poll alle 500ms
            self._poll_timer.start(500, False)
        except Exception:
            _log("DownloadScreen _start_download: " + _fmt_exc())
            self["status_label"].setText(_b("Fehler beim Starten"))

    # Callbacks aus dem Background-Thread — NUR einfache Wertzuweisungen, kein UI!
    def _cb_progress(self, downloaded, total):
        self._dl_downloaded = downloaded
        self._dl_total      = total

    def _cb_done(self, filepath):
        self._dl_filepath = filepath
        self._dl_done     = True

    def _cb_error(self, msg):
        self._dl_err = msg

    def _cb_convert_done(self, ts_path):
        self._dl_filepath   = ts_path
        self._dl_converting = False
        self._dl_done       = True

    def _cb_convert_error(self, msg):
        self._dl_converting = False
        self._dl_err = "Konvertierung fehlgeschlagen: " + msg

    # Poll läuft im Hauptthread — darf UI anfassen
    def _poll(self):
        if self._dl_err is not None:
            self._poll_timer.stop()
            self._dl_converting = False
            self["status_label"].setText(_b("Fehler: " + self._dl_err))
            self["hint_label"].setText(_b("OK / EXIT = Schlie\xc3\x9fen"))
            return

        if self._dl_converting:
            # Konvertierung läuft — Anzeige stabil halten bis _cb_convert_done feuert
            return

        if self._dl_done:
            self._poll_timer.stop()
            fp = self._dl_filepath or ""
            if get_auto_convert() and fp.lower().endswith(".mp4"):
                self._dl_done = False
                self._dl_converting = True
                self["status_label"].setText(_b("Konvertiere zu TS ..."))
                self["hint_label"].setText(_b("Bitte warten ..."))
                self["hint_yellow"].setText(_b(""))
                convert_mp4_to_ts(fp, on_done=self._cb_convert_done, on_error=self._cb_convert_error)
                self._poll_timer.start(500, False)
            else:
                fname = os.path.basename(fp)
                self["status_label"].setText(_b("Fertig: " + fname))
                self["hint_label"].setText(_b("OK / EXIT = Schlie\xc3\x9fen"))
            return

        downloaded = self._dl_downloaded
        total      = self._dl_total
        if total > 0:
            pct = int(downloaded * 100 / total)
            self["status_label"].setText(_b("%d%% von %s" % (pct, format_size(total))))
        elif downloaded > 0:
            self["status_label"].setText(_b("%s heruntergeladen" % format_size(downloaded)))

    def _to_background(self):
        global _active_downloader
        if not self._downloader or self._dl_done or self._dl_err is not None or self._dl_converting:
            return

        self._downloader.on_done     = _bg_download_done
        self._downloader.on_error    = lambda msg: _queue_error(msg)
        self._downloader.on_progress = lambda *a: None

        _active_downloader = self._downloader
        self._downloader   = None  # verhindert cancel() in doClose
        self.close()

    def _on_cancel(self):
        if self._downloader:
            self._downloader.cancel()
        self.close()

    def doClose(self):
        self.__stop_timers()
        if self._downloader:
            self._downloader.cancel()
        try:
            Screen.doClose(self)
        except TypeError:
            pass


def main(session, **kwargs):
    _log("Plugin gestartet")
    OeMediathekMainScreen.load_order()
    session.open(OeMediathekMainScreen)


def Plugins(**kwargs):
    return PluginDescriptor(
        name="ÖR Mediathek",
        description="Alle öffentlich-rechtlichen Mediatheken",
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon="plugin.png",
        fnc=main,
    )