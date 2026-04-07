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
    get_favorites,
    add_favorite,
    remove_favorite,
    is_favorite,
    _mvw_query,
)
from player import play_stream

LOGO_DIR = os.path.join(os.path.dirname(__file__), "logos")
LOG_FILE = "/tmp/oemediathek.log"
PAGE_SIZE = 500
DEBUG = False

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
]

# Kachel-Layout 3×3 (vertikal zentriert zwischen Titel und Legende)
TILE_COLS = 3
TILE_ROWS = 3
TILES_PER_PAGE = TILE_COLS * TILE_ROWS  # 9
if IS_FHD:
    TILE_W, TILE_H = 560, 180
    _TX = [80, 680, 1280]
    _TY = [187, 444, 701]
else:
    TILE_W, TILE_H = 373, 120
    _TX = [53, 453, 853]
    _TY = [124, 296, 467]
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
}

MODE_GROUPS   = 0
MODE_EPISODES = 1


def _episode_label(title_bytes):
    """
    Gibt einen Listeneintrag zurueck. Falls der Titel (SXX/EYY) enthaelt,
    wird 'S34 · <Titel ohne Tag>' vorangestellt, sonst unveraendert.
    """
    import re
    try:
        title = title_bytes.decode("utf-8", "replace")
    except Exception:
        title = str(title_bytes)
    m = re.search(r'\(S(\d+)/E(\d+)\)', title)
    if m:
        season = int(m.group(1))
        clean  = re.sub(r'\s*\(S\d+/E\d+\)', '', title).strip()
        label  = "S%d  %s" % (season, clean)
    else:
        label = title
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


def _build_groups(items, sort_mode="timestamp"):
    groups_dict  = {}
    groups_order = []
    for item in items:
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

        self["title_label"] = Label("Buchstabe wählen")
        self["selector"]    = Label("")
        self["hint_label"]  = Label("OK = Wählen   |   EXIT = Abbrechen")

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
        self["hint_label"] = Label("Hoch/Runter = Scrollen   |   EXIT/INFO = Schließen")
        
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
# Hauptmenü – Vollbild-Kachelansicht mit Logos (Card Layout)
# ------------------------------------------------------------------
class OeMediathekMainScreen(Screen):

    @staticmethod
    def _make_skin():
        tiles_bg = ""
        logos    = ""
        labels   = ""
        for r in range(TILE_ROWS):
            for c in range(TILE_COLS):
                i   = r * TILE_COLS + c
                tx  = _TX[c]
                ty  = _TY[r]
                # Logo zentriert in der Kachel
                lx  = tx + (TILE_W - (220 if IS_FHD else 146)) // 2
                ly  = ty + (20 if IS_FHD else 13)
                lw, lh = (220, 100) if IS_FHD else (146, 66)
                # Text unterhalb Logo, zentriert in der unteren Hälfte der Kachel
                label_y = ty + lh + (20 if IS_FHD else 13) + (10 if IS_FHD else 7)
                label_h = 40 if IS_FHD else 26
                font    = 28 if IS_FHD else 18
                tiles_bg += '<eLabel position="%d,%d" size="%d,%d" backgroundColor="#1A000000" zPosition="-4" />\n' \
                            % (tx, ty, TILE_W, TILE_H)
                logos    += '<widget name="logo_%d" position="%d,%d" size="%d,%d" alphatest="blend" scale="1" transparent="1" zPosition="1" />\n' \
                            % (i, lx, ly, lw, lh)
                labels   += '<widget name="tile_%d" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" zPosition="2" />\n' \
                            % (i, tx, label_y, TILE_W, label_h, font)

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

        margin = 30 if IS_FHD else 20

        return """
        <screen name="OeMediathekMainScreen" position="0,0" size="%d,%d" flags="wfNoBorder">
            <eLabel position="0,0" size="%d,%d" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="%d,%d" size="%d,%d" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="selector" position="%d,%d" size="%d,%d" backgroundColor="#1A333333" zPosition="-3" />
            %s%s%s
            <eLabel position="%d,%d" size="%d,%d" backgroundColor="#1A000000" zPosition="-5" />
            <widget name="hint_label" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" valign="center" foregroundColor="#888888" backgroundColor="#1A000000" transparent="1" />
            <widget name="page_label" position="%d,%d" size="%d,%d" font="Regular;%d" halign="center" valign="center" foregroundColor="#AAAAAA" backgroundColor="#1A000000" transparent="1" />
        </screen>
        """ % (
            sw, sh,
            sw, sh,
            margin, hdr_y, sw - 2 * margin, hdr_h,
            margin, hdr_y, sw - 2 * margin, hdr_h, font_title,
            _TX[0], _TY[0], TILE_W, TILE_H,
            tiles_bg, logos, labels,
            margin, bar_y, sw - 2 * margin, bar_h,
            margin, bar_y, hint_w, bar_h, font_hint,
            page_x, bar_y, page_w, bar_h, font_page,
        )

    def __init__(self, session):
        self.skin = self._make_skin()
        _log("MainScreen init")
        Screen.__init__(self, session)
        self.session       = session
        self.selected      = 0
        self.main_page     = 0

        self["title_label"] = Label("ÖR Mediathek")
        self["selector"]    = Label("")
        self["hint_label"]  = Label("Links/Rechts/Hoch/Runter = Navigieren   |   OK = Öffnen   |   CH+/CH- = Seite blättern   |   EXIT = Beenden")
        self["page_label"]  = Label("")

        for i in range(TILES_PER_PAGE):
            try:
                self["logo_%d" % i] = _Pixmap() if _Pixmap else Label("")
            except Exception:
                self["logo_%d" % i] = Label("")
            self["tile_%d" % i] = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "WizardActions",
             "ChannelSelectBaseActions"],
            {
                "ok":           self.on_ok,
                "cancel":       self.close,
                "up":           self.key_up,
                "down":         self.key_down,
                "left":         self.key_left,
                "right":        self.key_right,
                "nextBouquet":  self.page_next,
                "prevBouquet":  self.page_prev,
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

    def _refresh_page(self):
        """Kacheln und Logos der aktuellen Seite neu befuellen."""
        offset = self.main_page * TILES_PER_PAGE
        for i in range(TILES_PER_PAGE):
            src_idx = offset + i
            if src_idx < len(SOURCES):
                self["tile_%d" % i].setText(SOURCES[src_idx][0])
            else:
                self["tile_%d" % i].setText("")
            # Logo leeren — wird danach neu gesetzt
            try:
                self["logo_%d" % i].instance.setPixmap(None)
            except Exception:
                pass

        total_pages = (len(SOURCES) + TILES_PER_PAGE - 1) // TILES_PER_PAGE
        self["page_label"].setText("%d / %d" % (self.main_page + 1, total_pages))

        self._move_selector()
        self._update_tile_colors()
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

    def _update_tile_colors(self):
        try:
            from enigma import gRGB
            offset = self.main_page * TILES_PER_PAGE
            for i in range(TILES_PER_PAGE):
                src_idx = offset + i
                c = gRGB(0xFF, 0xFF, 0xFF) if src_idx == self.selected else gRGB(0x88, 0x88, 0x88)
                self["tile_%d" % i].instance.setForegroundColor(c)
        except Exception as e:
            _log("tile colors: " + str(e))

    def doClose(self):
        try:
            Screen.doClose(self)
        except TypeError as e:
            _log("doClose TypeError: " + str(e))

    def _select(self, idx):
        if idx < 0 or idx >= len(SOURCES):
            return
        self.selected = idx
        self._move_selector()
        self._update_tile_colors()

    def page_next(self):
        total_pages = (len(SOURCES) + TILES_PER_PAGE - 1) // TILES_PER_PAGE
        new_page = (self.main_page + 1) % total_pages
        self.main_page = new_page
        # Selektor auf erste Kachel der neuen Seite setzen
        self.selected = new_page * TILES_PER_PAGE
        self._refresh_page()

    def page_prev(self):
        total_pages = (len(SOURCES) + TILES_PER_PAGE - 1) // TILES_PER_PAGE
        new_page = (self.main_page - 1) % total_pages
        self.main_page = new_page
        self.selected = new_page * TILES_PER_PAGE
        self._refresh_page()

    def key_right(self):
        new = self.selected + 1
        if new >= len(SOURCES):
            new = 0
        new_page = new // TILES_PER_PAGE
        if new_page != self.main_page:
            self.main_page = new_page
            self.selected = new
            self._refresh_page()
        else:
            self._select(new)

    def key_left(self):
        new = self.selected - 1
        if new < 0:
            new = len(SOURCES) - 1
        new_page = new // TILES_PER_PAGE
        if new_page != self.main_page:
            self.main_page = new_page
            self.selected = new
            self._refresh_page()
        else:
            self._select(new)

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
        try:
            name, loader, _ = SOURCES[self.selected]
            _log("Oeffne: " + name)
            self.session.open(OeMediathekScreen, name, loader)
        except Exception:
            _log("on_ok: " + _fmt_exc())


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
            <widget name="title_label" position="50,30" size="1300,80" font="Regular;42" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="status_label" position="1360,30" size="470,80" font="Regular;28" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <eLabel position="30,140" size="1100,780" backgroundColor="#33000000" zPosition="-5" />
            <widget name="menu_list" position="40,150" size="1080,760" font="Regular;34" scrollbarMode="showOnDemand" itemHeight="58" backgroundColor="#33000000" transparent="1" />
            <eLabel position="1160,140" size="730,780" backgroundColor="#33000000" zPosition="-5" />
            <widget name="description_text" position="1190,160" size="670,740" font="Regular;34" foregroundColor="#CCCCCC" backgroundColor="#33000000" valign="top" halign="left" transparent="1" />
            <eLabel position="30,960" size="1860,100" backgroundColor="#1A000000" zPosition="-5" />
            <eLabel position="50,980" size="8,60" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red" position="68,960" size="350,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="450,980" size="8,60" backgroundColor="#1A00AA00" zPosition="2" />
            <widget name="hint_green" position="468,960" size="350,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="850,980" size="8,60" backgroundColor="#1AAAAA00" zPosition="2" />
            <widget name="hint_yellow" position="868,960" size="350,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="1250,980" size="8,60" backgroundColor="#1A0044DD" zPosition="2" />
            <widget name="hint_blue" position="1268,960" size="350,100" font="Regular;32" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_page" position="1668,960" size="202,100" font="Regular;32" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#1A000000" transparent="1" />
        </screen>
            """
        else:
            return """
        <screen name="OeMediathekScreen" position="0,0" size="1280,720" flags="wfNoBorder">
            <eLabel position="0,0" size="1280,720" backgroundColor="#66000000" zPosition="-6" />
            <eLabel position="20,20" size="1240,53" backgroundColor="#33000000" zPosition="-5" />
            <widget name="title_label" position="33,20" size="866,53" font="Regular;28" halign="left" valign="center" foregroundColor="#E0E0E0" backgroundColor="#33000000" transparent="1" />
            <widget name="status_label" position="906,20" size="313,53" font="Regular;18" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#33000000" transparent="1" />
            <eLabel position="20,93" size="733,520" backgroundColor="#33000000" zPosition="-5" />
            <widget name="menu_list" position="26,100" size="720,506" font="Regular;22" scrollbarMode="showOnDemand" itemHeight="38" backgroundColor="#33000000" transparent="1" />
            <eLabel position="773,93" size="486,520" backgroundColor="#33000000" zPosition="-5" />
            <widget name="description_text" position="793,106" size="446,493" font="Regular;22" foregroundColor="#CCCCCC" backgroundColor="#33000000" valign="top" halign="left" transparent="1" />
            <eLabel position="20,640" size="1240,66" backgroundColor="#1A000000" zPosition="-5" />
            <eLabel position="33,653" size="5,40" backgroundColor="#1AEE0000" zPosition="2" />
            <widget name="hint_red" position="45,640" size="233,66" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="300,653" size="5,40" backgroundColor="#1A00AA00" zPosition="2" />
            <widget name="hint_green" position="312,640" size="233,66" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="566,653" size="5,40" backgroundColor="#1AAAAA00" zPosition="2" />
            <widget name="hint_yellow" position="578,640" size="233,66" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <eLabel position="833,653" size="5,40" backgroundColor="#1A0044DD" zPosition="2" />
            <widget name="hint_blue" position="845,640" size="233,66" font="Regular;21" halign="left" valign="center" foregroundColor="#CCCCCC" backgroundColor="#1A000000" transparent="1" />
            <widget name="hint_page" position="1112,640" size="134,66" font="Regular;21" halign="right" valign="center" foregroundColor="#888888" backgroundColor="#1A000000" transparent="1" />
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

        self.current_search  = None
        self.min_duration    = 0
        self.sort_mode       = "timestamp"

        self._fetching      = False
        self._fetch_target  = "groups"
        self._fetch_result  = []
        self._fetch_episodes_result = []
        self._fetch_alpha_result = []
        self._fetch_total   = 0
        self._fetch_error   = None

        self.last_index = -1
        self.cur_group_idx = -1
        self.alpha_letter  = None

        self["title_label"]  = Label(source_name)
        self["status_label"] = Label("Lade Inhalte ...")
        self["menu_list"]    = MenuList([])
        self["description_text"] = ScrollLabel(_b(""))
        
        self["hint_red"]     = Label("")
        self["hint_green"]   = Label("")
        self["hint_yellow"]  = Label("")
        self["hint_blue"]    = Label("")
        self["hint_page"]    = Label("")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "DirectionActions",
             "ChannelSelectBaseActions", "EPGSelectActions"],
            {
                "ok":           self.on_ok,
                "cancel":       self.on_cancel,
                "red":          self.on_red,
                "green":        self.cycle_sort,
                "yellow":       self.open_search,
                "blue":         self.on_blue,
                "info":         self.on_info,
                "epg":          self.on_info,
                "nextBouquet":  self.next_page,
                "prevBouquet":  self.prev_page,
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

        self._toast_timer = eTimer()
        self._toast_timer.callback.append(self._clear_toast)

        self.onClose.append(self.__stop_timers)

    def __on_show(self):
        try:
            self["menu_list"].instance.moveSelectionTo(
                self["menu_list"].getSelectedIndex() or 0
            )
        except Exception:
            pass

    def __stop_timers(self):
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
            self._fetch_result, self._fetch_total = self.loader(
                offset=self.page * PAGE_SIZE,
                size=PAGE_SIZE,
                search_term=self.current_search,
                min_duration=self.min_duration,
                sort_by=api_sort,
            )
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
        elif self._fetch_target == "alpha":
            self._on_alpha_fetch_done()
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
            
        loaded_so_far = (self.page + 1) * PAGE_SIZE
        self._has_more = (self._fetch_total > loaded_so_far) or (len(raw) >= PAGE_SIZE)

        self.all_items = raw
            
        self.groups          = _build_groups(self.all_items, self.sort_mode)
        self.groups_filtered = _relevance_sort(self.groups, self.current_search)
        self._show_groups()

    def _update_desc(self):
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx == self.last_index:
                return
            self.last_index = idx
            
            if self.mode == MODE_GROUPS:
                self["description_text"].setText(_b(""))
                self._update_red_hint()
                self._update_blue_hint()
            elif self.mode == MODE_EPISODES:
                if idx is not None and idx < len(self.cur_episodes):
                    item = self.cur_episodes[idx]
                    desc = item.get("description", _b("Keine Beschreibung verfügbar."))
                    dur  = item.get("duration", b"Unbekannt")
                    full_text = _b("[") + _b(dur) + _b("]\n\n") + _b(desc)
                    self["description_text"].setText(full_text)
        except Exception:
            pass

    def _show_groups(self):
        self.mode = MODE_GROUPS
        self.last_index = -1
        entries = []
        for gname, gitems in self.groups_filtered:
            # Keine Zahlen mehr in der Vorschau anhängen
            entries.append(gname)
        self["menu_list"].setList(entries)
        
        status_text = "%d Sendungen" % len(self.groups_filtered)
        if self.current_search:
            status_text += " (Suche: %s)" % self.current_search
        self["status_label"].setText(status_text)
        
        self["hint_red"].setText("ABC-Auswahl")
        self["hint_green"].setText(self._next_sort_hint())
        self["hint_yellow"].setText("Suche (Server)")
        self._update_blue_hint()

        self._update_page_hint()
        self._focus_list(0)
        self._update_desc()

    def _update_page_hint(self):
        if self.mode == MODE_EPISODES:
            self["hint_page"].setText("EXIT = Zurück")
            return
        page_num    = self.page + 1
        total_pages = (self._fetch_total + PAGE_SIZE - 1) // PAGE_SIZE if self._fetch_total > 0 else None
        if total_pages:
            page_info = "CH+/- Seite %d von %d" % (page_num, total_pages)
        elif not self._has_more:
            page_info = "Seite %d (letzte)" % page_num
        else:
            page_info = "CH+/- Seite %d" % page_num
        self["hint_page"].setText(page_info)

    def _start_episode_fetch(self, group_idx):
        if self._fetching:
            return
            
        self.mode = MODE_EPISODES
        self.last_index = -1
        self.cur_group_idx = group_idx
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
        
        # Favoriten haben ohnehin schon alle lokalen Folgen geladen (bis zu 500)
        if self.source_name != "Meine Favoriten":
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
            # Bei Favoriten koennen wir den Deep Fetch ueberspringen, da mediathek.py das bereits erledigt hat
            if self.source_name == "Meine Favoriten":
                self._fetch_episodes_result = list(local_items)
                self._fetch_error = None
                self._fetching = False
                return

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

            # Deep Fetch: nur im topic-Feld suchen, verhindert Beifang durch title-Treffer
            res, _ = _mvw_query(
                channel=ch,
                offset=0,
                size=1000,
                search_term=pure_topic,
                min_duration=self.min_duration,
                sort_by=api_sort,
                search_fields=["topic"],
            )
            
            exact_items = []
            gname_str = raw_str
            # Unscharfe Treffer serverseitig aussortieren (Filterung exakt auf den Ursprungsnamen)
            for item in res:
                ig = item.get("group", b"")
                try:
                    ig_str = ig.decode("utf-8", "replace")
                except Exception:
                    ig_str = str(ig)
                if ig_str == gname_str:
                    exact_items.append(item)
                    
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
        
        if self.sort_mode == "az":
            self.cur_episodes = sorted(
                self.cur_episodes,
                key=lambda i: i["title"].decode("utf-8", "replace").lower()
            )
            
        self["menu_list"].setList([_episode_label(i["title"]) for i in self.cur_episodes])
        self["status_label"].setText("%d Folgen" % len(self.cur_episodes))
        
        self["hint_red"].setText("")
        self["hint_green"].setText(self._next_sort_hint())
        self["hint_yellow"].setText("Suche (Server)")
        self["hint_blue"].setText("Details")
            
        self._update_page_hint()
        self.last_index = -1
        self._focus_list(0)
        # Beschreibung der ersten Folge sofort setzen, nicht auf den 250ms-Timer warten.
        # moveSelectionTo() ist in Enigma2 asynchron — getSelectedIndex() liefert
        # direkt danach u.U. noch den alten Wert, wodurch _update_desc nichts tut.
        if self.cur_episodes:
            item = self.cur_episodes[0]
            desc = item.get("description", _b("Keine Beschreibung verfügbar."))
            dur  = item.get("duration", b"Unbekannt")
            self["description_text"].setText(_b("[") + _b(dur) + _b("]\n\n") + _b(desc))
            self.last_index = 0

    def _focus_list(self, idx=0):
        try:
            self["menu_list"].instance.moveSelectionTo(idx)
        except Exception:
            pass

    def on_info(self):
        if self.mode == MODE_EPISODES:
            try:
                idx = self["menu_list"].getSelectedIndex()
                if idx is not None and idx < len(self.cur_episodes):
                    item = self.cur_episodes[idx]
                    self.session.open(
                        OeMediathekInfoScreen,
                        item["title"],
                        item.get("description", _b("Keine Beschreibung verfügbar.")),
                        item.get("duration", b"Unbekannt")
                    )
            except Exception:
                _log("on_info Fehler: " + _fmt_exc())
        else:
            self.toggle_favorite()

    def on_ok(self):
        try:
            idx = self["menu_list"].getSelectedIndex()
            _log("on_ok mode=%d idx=%s" % (self.mode, str(idx)))
            if idx is None:
                return
            if self.mode == MODE_GROUPS:
                if idx < len(self.groups_filtered):
                    self._start_episode_fetch(idx)
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
        if self.mode == MODE_EPISODES:
            self["title_label"].setText(self.source_name)
            self._show_groups()
        else:
            self.close()

    def on_red(self):
        if self.mode == MODE_EPISODES:
            self["title_label"].setText(self.source_name)
            self._show_groups()
        else:
            self.open_alpha_picker()

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
                res, _ = self.loader(
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

            res, _ = self.loader(
                offset=0,
                size=2000,
                search_term=letter,
                min_duration=self.min_duration,
                sort_by=api_sort,
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
            if idx is None or idx >= len(self.groups_filtered):
                return
            gname, gitems = self.groups_filtered[idx]

            # Kanal direkt aus dem ersten Item der Gruppe lesen — zuverlaessig auch
            # in der Favoriten-Ansicht und bei "Alle Mediatheken"
            channel = b""
            if gitems:
                channel = gitems[0].get("channel", b"") or b""

            if is_favorite(gname):
                remove_favorite(gname)
                self["status_label"].setText("Favorit entfernt.")
                self._show_toast("Favorit entfernt", added=False)
            else:
                add_favorite(gname, channel)
                self["status_label"].setText("Favorit hinzugefügt!")
                self._show_toast("Favorit hinzugefügt!", added=True)
            self._update_red_hint()
            self._update_blue_hint()
        except Exception:
            _log("toggle_favorite: " + _fmt_exc())

    def _show_toast(self, msg, added=True):
        try:
            self._toast_timer.stop()
            prefix = "[+] " if added else "[-] "
            self["status_label"].setText(_b(prefix + msg))
            self._toast_timer.start(2500, True)
        except Exception:
            pass

    def _clear_toast(self):
        try:
            self["status_label"].setText(_b(""))
        except Exception:
            pass

    def _update_blue_hint(self):
        if self.mode == MODE_EPISODES:
            self["hint_blue"].setText("Details")
            return
        try:
            idx = self["menu_list"].getSelectedIndex()
            if idx is not None and idx < len(self.groups_filtered):
                gname, _ = self.groups_filtered[idx]
                if is_favorite(gname):
                    self["hint_blue"].setText("Favorit löschen")
                    return
        except Exception:
            pass
        self["hint_blue"].setText("Favorit")

    def _update_red_hint(self):
        if self.mode == MODE_EPISODES:
            self["hint_red"].setText("")
        else:
            self["hint_red"].setText("ABC-Auswahl")

    def next_page(self):
        if self.mode == MODE_EPISODES:
            return
        if self._fetching:
            return
        if not self._has_more:
            _log("Keine weiteren Seiten")
            return
        self.page += 1
        self._start_fetch()

    def prev_page(self):
        if self.mode == MODE_EPISODES:
            return
        if self._fetching or self.page == 0:
            return
        self.page -= 1
        self._start_fetch()

    _SORT_CYCLE = ["timestamp", "az", "duration"]
    _SORT_LABELS = {
        "timestamp": "Neueste zuerst",
        "az":        "A-Z",
        "duration":  "Nach Laenge",
    }

    def _next_sort_hint(self):
        return OeMediathekScreen._SORT_LABELS.get(self.sort_mode, "Neueste zuerst")

    def cycle_sort(self):
        try:
            cycle = OeMediathekScreen._SORT_CYCLE
            idx = cycle.index(self.sort_mode) if self.sort_mode in cycle else 0
            self.sort_mode = cycle[(idx + 1) % len(cycle)]
            _log("Sortierung: " + self.sort_mode)

            if self.sort_mode == "az":
                if self.mode == MODE_GROUPS:
                    self.groups_filtered = sorted(
                        self.groups_filtered,
                        key=lambda g: g[0].decode("utf-8", "replace").lower()
                    )
                    self._show_groups()
                else:
                    self.cur_episodes = sorted(
                        self.cur_episodes,
                        key=lambda i: i["title"].decode("utf-8", "replace").lower()
                    )
                    self["menu_list"].setList([_episode_label(i["title"]) for i in self.cur_episodes])
                    self["hint_green"].setText(self._next_sort_hint())
                    self._focus_list(0)
            else:
                if self.mode == MODE_GROUPS:
                    self.page = 0
                    self.all_items = []
                    self.groups = []
                    self.groups_filtered = []
                    self["menu_list"].setList([])
                    self["description_text"].setText(_b(""))
                    self._start_fetch()
                else:
                    self["menu_list"].setList([])
                    self["description_text"].setText(_b(""))
                    self._start_episode_fetch(self.cur_group_idx)
        except Exception:
            _log("cycle_sort: " + _fmt_exc())

    def on_blue(self):
        if self.mode == MODE_EPISODES:
            self.on_info()
        else:
            self.toggle_favorite()

    def open_search(self):
        try:
            self.session.openWithCallback(
                self.do_search, VirtualKeyBoard,
                title="Suchen:", text="",
            )
        except Exception:
            _log("open_search: " + _fmt_exc())

    def do_search(self, term):
        try:
            if term is not None:
                term = term.strip()
                if not term:
                    self.current_search = None
                else:
                    self.current_search = term
                
                self.page = 0
                self.all_items = []
                self.groups = []
                self.groups_filtered = []
                self["menu_list"].setList([])
                self["description_text"].setText(_b(""))
                self._start_fetch()
        except Exception:
            _log("do_search: " + _fmt_exc())


def main(session, **kwargs):
    _log("Plugin gestartet")
    session.open(OeMediathekMainScreen)


def Plugins(**kwargs):
    return PluginDescriptor(
        name="ÖR Mediathek",
        description="Alle öffentlich-rechtlichen Mediatheken",
        where=PluginDescriptor.WHERE_PLUGINMENU,
        icon="plugin.png",
        fnc=main,
    )