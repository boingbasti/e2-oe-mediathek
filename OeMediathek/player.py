# -*- coding: utf-8 -*-
# player.py
# Startet einen Stream im angepassten Enigma2-Mediaplayer

import hashlib
import io
import os
import re
import threading

try:
    import traceback
    def _fmt_exc():
        return traceback.format_exc()
except ImportError:
    def _fmt_exc():
        return "(traceback nicht verfügbar)"

try:
    from urllib2 import urlopen, Request as _Request
except ImportError:
    from urllib.request import urlopen, Request as _Request

try:
    from urlparse import urljoin as _urljoin
except ImportError:
    from urllib.parse import urljoin as _urljoin

from enigma import eServiceReference

from downloader import get_debug_logging, get_force_exteplayer, get_live_tv_background, load_settings, save_settings

_LOG_FILE = "/tmp/OeMediathek/oemediathek.log"


def _log(msg):
    if not get_debug_logging():
        return
    import time
    line = "[OeMediathek %s] PL: %s" % (time.strftime("%H:%M:%S", time.localtime()), str(msg))
    print(line)
    try:
        if not os.path.isdir(_TMP_DIR):
            os.makedirs(_TMP_DIR)
        with open(_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

try:
    from Screens.MoviePlayer import MoviePlayer
except ImportError:
    from Screens.InfoBar import MoviePlayer


class OeStreamPlayer(MoviePlayer):
    ENABLE_RESUME_SUPPORT = False

    def __init__(self, session, service, streams=None, stream_index=0, autoconfigure_serviceapp=True):
        MoviePlayer.__init__(self, session, service)
        self.skinName = ["MoviePlayer", "InfoBar"]
        self._streams         = streams or []
        self._stream_index    = stream_index
        self._autoconfigure   = autoconfigure_serviceapp
        self._switching       = False
        self._closed          = False
        self._target_stream_index = stream_index
        self._switch_token         = 0
        self._showing_offline = False
        self.onClose.append(self.__on_close)
        if len(self._streams) > 1:
            from Components.ActionMap import ActionMap
            self["_oem_nav"] = ActionMap(
                [b"ChannelSelectBaseActions"],
                {
                    b"nextBouquet": lambda: self._switch_channel(1),
                    b"prevBouquet": lambda: self._switch_channel(-1),
                },
                -1,
            )

    def __on_close(self):
        self._closed = True
        _restore_serviceapp_settings()

    def _switch_channel(self, direction):
        # Calculate next target stream index immediately in main thread
        target_idx = getattr(self, "_target_stream_index", self._stream_index) + direction
        if target_idx < 0 or target_idx >= len(self._streams):
            return
            
        self._target_stream_index = target_idx
        name, url = self._streams[target_idx]
        
        # Increment token so only the LATEST zap thread's results are applied
        self._switch_token = getattr(self, "_switch_token", 0) + 1
        current_token = self._switch_token
        
        self._switching = True
        
        t = threading.Thread(target=self.__switch_bg, args=(target_idx, url, name, current_token))
        t.daemon = True
        t.start()

    def __switch_bg(self, new_idx, url, name, token=0):
        try:
            stream_url_bytes, title_bytes, player_id = _resolve_stream(
                url, name, is_live=True, autoconfigure_serviceapp=self._autoconfigure
            )
        except Exception:
            _log("OeStreamPlayer._switch_channel: Fehler: " + _fmt_exc())
            if getattr(self, "_switch_token", 0) == token:
                self._switching = False
            return

        def _apply():
            if getattr(self, "_switch_token", 0) != token:
                return
            offline_call = getattr(self, "_offline_call", None)
            if offline_call is not None and offline_call.active():
                offline_call.cancel()
            self._offline_call = None
            self._switching = False
            if self._closed:
                return
            self._stream_index    = new_idx
            self._showing_offline = False
            ref = eServiceReference(player_id, 0, stream_url_bytes)
            ref.setName(title_bytes)
            self.session.nav.playService(ref)

        try:
            from twisted.internet import reactor
            reactor.callFromThread(_apply)
        except Exception:
            _log("OeStreamPlayer._switch_channel: callFromThread Fehler: " + _fmt_exc())
            if getattr(self, "_switch_token", 0) == token:
                self._switching = False

    def leavePlayer(self):
        self.close()

    def doEofInternal(self, playing):
        if getattr(self, "_showing_offline", False):
            _log("doEofInternal: Already showing offline stream, closing player to prevent loop/deadlock")
            self.close()
            return
        if len(self._streams) > 1:
            self._showing_offline = True
            stream_name = self._streams[self._stream_index][0] if self._streams else ""
            try:
                from twisted.internet import reactor
                self._offline_call = reactor.callLater(0.5, self.session.nav.playService, _offline_ref(stream_name))
            except Exception:
                self._offline_call = None
                self.session.nav.playService(_offline_ref(stream_name))
            return
        self.close()

    def showResumePoint(self):
        pass


_ORF_USER_AGENT = "OeMediathek/1.0"

_TMP_DIR = "/tmp/OeMediathek"

_OFFLINE_VIDEO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline_stream.mp4")


def _offline_ref(name=""):
    path = _OFFLINE_VIDEO.encode("utf-8") if isinstance(_OFFLINE_VIDEO, str) else _OFFLINE_VIDEO
    ref = eServiceReference(4097, 0, path)
    if name:
        title = (name + " (Offline)").encode("utf-8") if isinstance(name, type(u"")) else (name + b" (Offline)")
        ref.setName(title)
    return ref


_BLACK_BACKGROUND_VIDEO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "black_background.mp4")


def black_background_ref():
    """Service-Referenz auf einen stillen schwarzen Platzhalter-Clip (10 Min, loopt
    sich durch Neustart bei jedem Menue-Aufruf effektiv selbst). Wird als Hintergrund
    abgespielt wenn 'Live TV im Hintergrund' aus ist: einzelne Listen-/Kachel-Widgets
    haben eigene halbtransparente Hintergrundfarben und blenden unabhaengig vom
    Root-Layer direkt gegen die Video-Ebene - ein blosses stopService() laesst dort
    ein haengengebliebenes Standbild vom zuletzt geschlossenen Player durchscheinen.
    Ersetzt die Video-Ebene komplett durch garantiert Schwarz statt zu versuchen,
    sie zu verdecken."""
    path = _BLACK_BACKGROUND_VIDEO.encode("utf-8") if isinstance(_BLACK_BACKGROUND_VIDEO, str) else _BLACK_BACKGROUND_VIDEO
    return eServiceReference(4097, 0, path)


def _tmp_playlist_path(master_url):
    """Eindeutiger Dateiname pro Stream-URL, damit alte exteplayer3-Versionen
    (hls_explorer) zwei verschiedene Sender nicht ueber denselben file://-Pfad
    verwechseln und gecachte Sub-Streams des vorherigen Senders weiterspielen."""
    url_bytes = master_url.encode("utf-8") if isinstance(master_url, str) else master_url
    h = hashlib.md5(url_bytes).hexdigest()[:12]
    return _TMP_DIR + "/live_" + h + ".m3u8"


def _has_serviceapp():
    """Prueft lautlos, ob das Systemplugin ServiceApp auf der Box installiert ist."""
    return os.path.exists("/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp")


_NEW_EXTEPLAYER3 = None


def _detect_new_exteplayer3():
    if os.path.isdir("/usr/lib/exteplayer3_deps"):
        return True
    # Nicht jedes Image bringt den Ordner mit (z.B. andere Feeds/Architekturen).
    # exteplayer3 ab v181 meldet sich beim Start ohne Argumente mit einer
    # Versionszeile {"EPLAYER3_EXTENDED":{"version":181}} und beendet sich
    # danach sofort mit der Usage-Ausgabe.
    try:
        import subprocess
        proc = subprocess.Popen(["exteplayer3"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        chunks = []

        def _read():
            try:
                data = proc.stdout.read()
                # Sofort zu Text dekodieren: py3_port.py macht aus b""-Literalen
                # Strings, ein spaeteres b"".join() waere unter Python 3 ein TypeError.
                if isinstance(data, bytes):
                    data = data.decode("utf-8", "replace")
                chunks.append(data)
            except Exception:
                pass
        # Harte Zeitgrenze im Lese-Thread statt communicate(): ein haengender
        # Prozess (oder Kindprozess, der die Pipe offen haelt) darf die
        # Erkennung nie laenger als 2s blockieren.
        reader = threading.Thread(target=_read)
        reader.daemon = True
        reader.start()
        reader.join(2.0)
        if reader.is_alive():
            try:
                proc.kill()
            except Exception:
                pass
        out = "".join(chunks)
        m = re.search(r'"EPLAYER3_EXTENDED"\s*:\s*\{\s*"version"\s*:\s*(\d+)', out or "")
        return bool(m and int(m.group(1)) >= 181)
    except Exception:
        return False


def _has_new_exteplayer3():
    """exteplayer3 >= v181: Ordner /usr/lib/exteplayer3_deps (feedplus) oder
    Versionszeile beim Start. Das Ergebnis wird pro Sitzung gemerkt."""
    global _NEW_EXTEPLAYER3
    if _NEW_EXTEPLAYER3 is None:
        _NEW_EXTEPLAYER3 = _detect_new_exteplayer3()
        _log("exteplayer3 >= v181 erkannt: " + str(_NEW_EXTEPLAYER3))
    return _NEW_EXTEPLAYER3


# Felder, die _configure_serviceapp_for_live() live-tunt und die deshalb vor
# der ersten Aenderung gesichert und beim Verlassen der Live-Wiedergabe
# wiederhergestellt werden muessen. debugLoggingEnabled/pcmAudioExportEnabled
# gehoeren NICHT hierher - die werden nie .save()t, nur als Kwargs an
# setExtEplayer3Settings() durchgereicht (siehe _push_serviceapp_native_settings).
_SERVICEAPP_BACKUP_FIELDS = (
    ("opts", "hls_explorer"),
    ("opts", "autoselect_stream"),
    ("opts", "hls_audio_filter"),
    ("ext3", "downmix"),
    ("ext3", "aac_swdecoding"),
    ("ext3", "hls_quality_mode"),
    ("ext3", "hls_audio_default_only"),
)

# Bekannte Settings-Dateien der drei Schwester-Plugins desselben Autors
# (StreamAnything/OeMediathek/MagentaMusik), die alle dieselbe globale
# ServiceApp-Config antasten. Fuer plugin-uebergreifendes Self-Healing, siehe
# _self_heal_all_serviceapp_backups().
_SIBLING_SERVICEAPP_BACKUP_SOURCES = (
    ("/etc/enigma2/streamanything.json",       "settings"),
    ("/etc/enigma2/oemediathek_settings.json", None),
    ("/etc/enigma2/magentamusik.json",         "settings"),
)


def _capture_serviceapp_field_values(opts, ext3):
    objs = {"opts": opts, "ext3": ext3}
    out = {}
    for obj_name, attr in _SERVICEAPP_BACKUP_FIELDS:
        obj = objs[obj_name]
        if hasattr(obj, attr):
            out["%s.%s" % (obj_name, attr)] = getattr(obj, attr).value
    return out


def _load_serviceapp_backup():
    try:
        return load_settings().get("serviceapp_backup")
    except Exception:
        return None


def _save_serviceapp_backup(backup):
    try:
        s = load_settings()
        s["serviceapp_backup"] = backup
        save_settings(s)
    except Exception:
        pass


def _clear_serviceapp_backup():
    try:
        s = load_settings()
        s.pop("serviceapp_backup", None)
        save_settings(s)
    except Exception:
        pass


def _push_serviceapp_native_settings(opts, ext3):
    """Schreibt die aktuellen opts/ext3-Werte in ServiceApps globalen,
    prozessweiten C-Struct (setExtEplayer3Settings()/setServiceAppSettings()).
    Aus _configure_serviceapp_for_live() herausgezogen, damit
    _restore_serviceapp_settings() dieselbe has_*-gated Kwargs-Logik
    wiederverwenden kann - genau deren Duplizierung hat den
    debugLoggingEnabled- und pcmAudioExportEnabled-Bug verursacht
    (siehe Commits d40b0b1 / 4340f6e)."""
    try:
        from Components.config import config
        from Plugins.SystemPlugins.ServiceApp.serviceapp_client import (
            setExtEplayer3Settings, setServiceAppSettings, OPTIONS_SERVICEEXTEPLAYER3
        )
        debug_logging = config.plugins.serviceapp.debug_logging.value

        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_NATIVE_REFERER as has_new_serviceapp
        except ImportError:
            has_new_serviceapp = False
        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_HLS_QUALITY_SELECT as has_quality_select
        except ImportError:
            has_quality_select = False
        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_DEBUG_LOGGING_CONTROL as has_debug_logging_control
        except ImportError:
            has_debug_logging_control = False
        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_PCM_AUDIO_EXPORT as has_pcm_audio_export
        except ImportError:
            has_pcm_audio_export = False

        # Bei v181 aac_swdecoding=False erzwingen: altes serviceapp.so wuerde sonst
        # '-a' ohne Wert generieren (Boolean-Flag statt 0|1|2|3) -> exteplayer3 v181 haengt.
        aac_sw = False if _has_new_exteplayer3() else ext3.aac_swdecoding.value
        # debugLoggingEnabled nur mitgeben, wenn die installierte serviceapp den
        # Parameter ueberhaupt kennt - sonst wirft der C-Aufruf bei einer aelteren
        # Version einen TypeError (zu viele Argumente).
        extra_kwargs = {"debugLoggingEnabled": debug_logging} if has_debug_logging_control else {}
        # Eigene Kopie fuer setExtEplayer3Settings: pcmAudioExportEnabled kennt nur
        # dieses Setter-Paar, nicht setServiceAppSettings() weiter unten (das teilt
        # sich extra_kwargs mit). MUSS mitgegeben werden, wenn bekannt: der
        # zugrundeliegende C-Struct ist global/prozessweit, ein fehlender Parameter
        # faellt auf Pythons Default (False) zurueck und ueberschreibt damit
        # unbemerkt den im Setup gesetzten Wert - auch fuer alle spaeteren Streams
        # anderer Plugins, bis das Setup erneut gespeichert oder Enigma2 neu
        # gestartet wird.
        extra_kwargs_ext3 = dict(extra_kwargs)
        if has_pcm_audio_export and hasattr(ext3, "pcm_audio_export"):
            extra_kwargs_ext3["pcmAudioExportEnabled"] = ext3.pcm_audio_export.value

        if has_quality_select:
            hls_qm = {"auto": 0, "lowest": 1, "highest": 2}.get(ext3.hls_quality_mode.value, 0)
            setExtEplayer3Settings(
                OPTIONS_SERVICEEXTEPLAYER3,
                aac_sw,
                ext3.dts_swdecoding.value,
                ext3.wma_swdecoding.value,
                ext3.lpcm_injecion.value,
                ext3.downmix.value,
                hls_qm,
                ext3.hls_audio_default_only.value,
                **extra_kwargs_ext3
            )
        else:
            setExtEplayer3Settings(
                OPTIONS_SERVICEEXTEPLAYER3,
                aac_sw,
                ext3.dts_swdecoding.value,
                ext3.wma_swdecoding.value,
                ext3.lpcm_injecion.value,
                ext3.downmix.value,
                **extra_kwargs_ext3
            )

        if has_new_serviceapp and hasattr(opts, "hls_audio_filter"):
            setServiceAppSettings(
                OPTIONS_SERVICEEXTEPLAYER3,
                opts.hls_explorer.value,
                opts.autoselect_stream.value,
                opts.connection_speed_kb.value,
                opts.autoturnon_subtitles.value,
                opts.hls_audio_filter.value,
                **extra_kwargs
            )
        else:
            setServiceAppSettings(
                OPTIONS_SERVICEEXTEPLAYER3,
                opts.hls_explorer.value,
                opts.autoselect_stream.value,
                opts.connection_speed_kb.value,
                opts.autoturnon_subtitles.value,
                **extra_kwargs
            )
    except Exception:
        pass


def _restore_backup_dict(backup):
    """Wendet einen einzelnen Backup-Blob auf ServiceApps aktuelle Config an.
    Restauriert pro Feld nur, wenn der aktuelle Wert noch exakt dem zuletzt
    von HIER geschriebenen Wert entspricht (last_applied) - hat der Nutzer
    (oder ein anderes Plugin/das native Setup) den Wert seitdem bewusst
    geaendert, bleibt dieses Feld unangetastet. Gibt True zurueck, wenn der
    Backup-Block verarbeitet wurde (unabhaengig davon ob dabei tatsaechlich
    etwas geaendert wurde)."""
    if not backup:
        return False
    try:
        from Components.config import config
        key  = "serviceexteplayer3"
        opts = config.plugins.serviceapp.options[key]
        ext3 = config.plugins.serviceapp.exteplayer3[key]
        objs = {"opts": opts, "ext3": ext3}
        values       = backup.get("values", {}) or {}
        last_applied = backup.get("last_applied", {}) or {}
        for field_key, orig_value in values.items():
            obj_name, attr = field_key.split(".", 1)
            obj = objs.get(obj_name)
            if obj is None or not hasattr(obj, attr):
                continue
            cfg_item = getattr(obj, attr)
            applied = last_applied.get(field_key, cfg_item.value)
            if cfg_item.value != applied:
                continue
            if cfg_item.value != orig_value:
                cfg_item.value = orig_value
                cfg_item.save()
        _push_serviceapp_native_settings(opts, ext3)
        return True
    except Exception:
        return False


def _restore_serviceapp_settings():
    """Wird beim echten Schliessen des Live-Players aufgerufen (siehe
    OeStreamPlayer.__on_close). Idempotent - No-Op, wenn kein Backup aussteht
    (z.B. weil das Self-Healing es schon behandelt hat)."""
    backup = _load_serviceapp_backup()
    if not backup:
        return
    if _restore_backup_dict(backup):
        _clear_serviceapp_backup()
        _log("_restore_serviceapp_settings: restored")


def _restore_serviceapp_settings_from(json_path, settings_subkey):
    """Plugin-uebergreifendes Self-Healing: liest/loescht ein
    'serviceapp_backup' direkt aus der Settings-JSON eines der SCHWESTER-
    Plugins (StreamAnything/OeMediathek/MagentaMusik teilen sich dieselbe
    globale ServiceApp-Config, fuehren aber jeweils ihr eigenes, isoliertes
    Backup). Kein Fehlerfall, wenn die Datei fehlt (Plugin nicht installiert)
    oder keinen Backup-Key enthaelt."""
    try:
        import json
        if not os.path.exists(json_path):
            return
        with io.open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = data.get(settings_subkey) if settings_subkey else data
        if not isinstance(settings, dict):
            return
        backup = settings.get("serviceapp_backup")
        if not backup:
            return
        if _restore_backup_dict(backup):
            del settings["serviceapp_backup"]
            with open(json_path, "wb") as f:
                f.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            _log("_restore_serviceapp_settings_from: healed %s" % json_path)
    except Exception:
        pass


# Referenztypen, unter denen eine der drei Schwester-Plugins ueberhaupt
# spielen kann (idServiceMP3/idServiceGstPlayer/idServiceExtEplayer3). Live-
# Praxistest hat gezeigt: ein reiner "spielt ueberhaupt irgendwas"-Check
# (jeder eServiceReference-Typ, auch normales DVB-Live-TV = Typ 1) blockiert
# die Selbstheilung praktisch IMMER, weil beim Oeffnen eines Plugins so gut
# wie nie wirklich NICHTS laeuft - es sei denn man zappt gezielt auf einen
# toten Kanal. Normales Live-TV kann aber gar keine unserer serviceapp_backup-
# Dateien erzeugt haben, blockiert die Heilung also nur unnoetig.
_SERVICEAPP_RELEVANT_REF_TYPES = (4097, 5001, 5002)


def _self_heal_all_serviceapp_backups(session):
    """Am Plugin-Menue-Einstieg (main()) aufgerufen: heilt ein liegen
    gebliebenes Backup aus einer nicht sauber beendeten Sitzung EINES DER DREI
    Schwester-Plugins, unabhaengig davon welches der drei gerade geoeffnet
    wird. Ein gefundenes Backup ist aber nicht automatisch eine Absturz-
    Leiche - es kann auch zu einer gerade noch laufenden Sitzung eines
    ANDEREN, noch offenen Plugins gehoeren. Restaurieren waere in dem Fall
    genau der Fehler, den der Mechanismus verhindern soll, nur durch die
    Hintertuer: das andere Plugin faende beim eigenen Schliessen kein Backup
    mehr vor und koennte seine echten Originalwerte nicht mehr
    wiederherstellen. Deshalb nur restaurieren, wenn der aktuell laufende
    Service NICHT von einem der Referenztypen ist, unter denen ueberhaupt
    eine dieser drei Plugins spielen kann (4097/5001/5002) - normales
    Live-TV (Typ 1) blockiert die Heilung also NICHT mehr, siehe
    _SERVICEAPP_RELEVANT_REF_TYPES. 4097 bleibt bewusst konservativ
    mitgezaehlt, da darueber sowohl der native Player als auch (je nach
    Wiedergabemodul) serviceapp laeuft und sich das von hier aus nicht sicher
    unterscheiden laesst. Deckt weiterhin NICHT den Fall ab, dass ein anderes
    Plugin offen, aber pausiert/idle ist ohne aktiven Service - bewusst
    akzeptierte Restluecke.

    Weitere bekannte, bewusst nicht geloeste Einschraenkung: Laufen zwei
    dieser Plugins zeitlich UEBERLAPPEND (selten, da Enigma2 i.d.R. nur einen
    Service gleichzeitig abspielt, aber nicht ausgeschlossen), sieht das
    zweite beim eigenen Snapshot bereits die vom ersten getunten Werte als
    "Original". Eine echte Loesung dafuer braeuchte Locking/Refcounting ueber
    alle drei Plugins hinweg - deutlich groesserer Scope als hier
    gerechtfertigt."""
    try:
        current_ref = session.nav.getCurrentlyPlayingServiceReference()
        if current_ref is not None and current_ref.type in _SERVICEAPP_RELEVANT_REF_TYPES:
            return
    except Exception:
        return
    for path, settings_subkey in _SIBLING_SERVICEAPP_BACKUP_SOURCES:
        _restore_serviceapp_settings_from(path, settings_subkey)


def _configure_serviceapp_for_live():
    """Setzt serviceapp-Einstellungen fuer synchrone HLS-Live-Streams.
    Bei exteplayer3 >= v181 wird aac_swdecoding nicht gesetzt (inkompatibel mit
    altem serviceapp.so: generiert '-a' ohne Wert, v181 erwartet '-a 0|1|2|3').
    """
    try:
        from Components.config import config
        key  = "serviceexteplayer3"
        opts = config.plugins.serviceapp.options[key]
        ext3 = config.plugins.serviceapp.exteplayer3[key]
        changed = False

        backup = _load_serviceapp_backup()
        if backup is None:
            # Erster Aufruf dieser Sitzung (kein Re-Zap innerhalb einer schon
            # laufenden) - jetzt, VOR jeder Aenderung, die echten
            # Originalwerte sichern.
            backup = {
                "version": 1,
                "values": _capture_serviceapp_field_values(opts, ext3),
                "last_applied": {},
            }
            _save_serviceapp_backup(backup)
            _log("_configure_serviceapp_for_live: snapshot captured")
        else:
            _log("_configure_serviceapp_for_live: backup already pending, skipping snapshot")

        if not ext3.downmix.value:
            ext3.downmix.value = True; ext3.downmix.save(); changed = True

        if _has_new_exteplayer3():
            # v181+: exteplayer3's ffmpeg parst Master-Playlist inkl. EXT-X-MEDIA selbst.
            # HLS-Explorer deaktivieren damit serviceapp die URL unveraendert durchreicht.
            if opts.hls_explorer.value:
                opts.hls_explorer.value = False; opts.hls_explorer.save(); changed = True
        else:
            # Alte exteplayer3: HLS-Explorer an, autoselect aus (kein ABR-Stutter), AAC SW-Decode an.
            if not opts.hls_explorer.value:
                opts.hls_explorer.value = True;  opts.hls_explorer.save(); changed = True
            if opts.autoselect_stream.value:
                opts.autoselect_stream.value = False; opts.autoselect_stream.save(); changed = True
            if not ext3.aac_swdecoding.value:
                ext3.aac_swdecoding.value = True; ext3.aac_swdecoding.save(); changed = True

        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_NATIVE_REFERER as has_new_serviceapp
        except ImportError:
            has_new_serviceapp = False
        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_HLS_QUALITY_SELECT as has_quality_select
        except ImportError:
            has_quality_select = False

        if has_new_serviceapp and hasattr(opts, "hls_audio_filter"):
            if not opts.hls_audio_filter.value:
                opts.hls_audio_filter.value = True; opts.hls_audio_filter.save(); changed = True

        if has_quality_select and hasattr(ext3, "hls_quality_mode"):
            if ext3.hls_quality_mode.value != "highest":
                ext3.hls_quality_mode.value = "highest"; ext3.hls_quality_mode.save(); changed = True
        if has_quality_select and hasattr(ext3, "hls_audio_default_only"):
            if not ext3.hls_audio_default_only.value:
                ext3.hls_audio_default_only.value = True; ext3.hls_audio_default_only.save(); changed = True

        _push_serviceapp_native_settings(opts, ext3)

        backup["last_applied"] = _capture_serviceapp_field_values(opts, ext3)
        _save_serviceapp_backup(backup)
        return changed
    except Exception:
        return False


def _serve_playlist_via_http(content):
    """Startet einen einmaligen localhost-HTTP-Server und gibt die URL zurueck."""
    try:
        import threading
        import socket
        try:
            from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
        except ImportError:
            from http.server import HTTPServer, BaseHTTPRequestHandler

        data = content.encode('utf-8') if isinstance(content, str) else content

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.apple.mpegurl')
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), _Handler)
        # Grosszuegig: zwischen Serverstart und dem tatsaechlichen GET durch
        # exteplayer3 liegen noch Config-Writes (_configure_serviceapp_for_live),
        # reactor callFromThread-Queueing und der exteplayer3-Prozessstart - auf
        # langsameren Boxen/Flash kann das mehrere Sekunden dauern. Bei zu kurzem
        # Timeout schliesst der Server schon, bevor exteplayer3 anfragt ->
        # "Connection refused" ohne jeden Fehler in unserem Log, Stream startet
        # einfach nicht (gemeldet von einem User mit altem serviceapp.so + neuem
        # exteplayer3 v181+, dieser Zweig servierte bisher nur 5s lang).
        server.timeout = 20.0
        port = server.server_address[1]

        t = threading.Thread(target=lambda: (server.handle_request(), server.server_close()))
        t.daemon = True
        t.start()

        return 'http://127.0.0.1:%d/live.m3u8' % port
    except Exception:
        return None


def _build_single_quality_playlist(master_url):
    """
    Laedt die HLS-Master-Playlist, waehlt die beste Variante und gibt eine
    modifizierte Playlist zurueck, die nur diese eine Variante enthaelt
    (kein ABR-Wechsel) aber alle Audio-Tracks behaelt.
    Bei exteplayer3 >= v181 wird die Playlist per localhost-HTTP bereitgestellt,
    da file:// nicht unterstuetzt wird. Sonst wird sie nach /tmp/ geschrieben.
    Gibt master_url zurueck bei Fehler.
    """
    _log("build_single_quality_playlist: master_url=" + str(master_url))
    try:
        from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_NATIVE_REFERER as has_new_serviceapp
    except ImportError:
        has_new_serviceapp = False

    if has_new_serviceapp:
        _log("build_single_quality_playlist: new serviceapp detected, playing natively")
        return master_url

    try:
        req = _Request(master_url)
        req.add_header('User-Agent', _ORF_USER_AGENT)
        resp = urlopen(req, timeout=4)
        content = resp.read().decode('utf-8', 'replace')
        lines = content.splitlines()

        best_bw         = -1
        best_stream_inf = None
        best_variant    = None

        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith('#EXT-X-STREAM-INF'):
                m  = re.search(r'BANDWIDTH=(\d+)', line)
                bw = int(m.group(1)) if m else 0
                for j in range(i + 1, len(lines)):
                    v = lines[j].strip()
                    if v and not v.startswith('#'):
                        if bw > best_bw:
                            best_bw         = bw
                            best_stream_inf = line
                            best_variant    = _urljoin(master_url, v)
                        break
            i += 1

        if not best_variant:
            _log("build_single_quality_playlist: kein best_variant gefunden, master_url unveraendert")
            return master_url

        _log("build_single_quality_playlist: best_variant=" + str(best_variant) + " bw=" + str(best_bw))

        out = ['#EXTM3U', '#EXT-X-VERSION:4', '#EXT-X-INDEPENDENT-SEGMENTS', '']

        # Nur den Default-Audio-Track der passenden Gruppe behalten.
        # ZDF hat 3 Audio-Tracks (TV Ton, Klare Sprache, Audio-Deskription) plus
        # eine Backup-CDN-Gruppe — exteplayer3 lädt alle vorab → langer Start.
        # Lösung: nur TYPE=AUDIO mit passendem GROUP-ID und DEFAULT=YES behalten.
        audio_group_m = re.search(r'AUDIO="([^"]+)"', best_stream_inf or '')
        audio_group = audio_group_m.group(1) if audio_group_m else None

        for line in lines:
            if line.startswith('#EXT-X-MEDIA'):
                if 'TYPE=AUDIO' not in line:
                    continue
                if audio_group and ('GROUP-ID="%s"' % audio_group) not in line:
                    continue
                if 'DEFAULT=YES' not in line:
                    continue
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: 'URI="' + _urljoin(master_url, m.group(1)) + '"',
                    line
                )
                out.append(line)

        out.append('')
        out.append(best_stream_inf)
        out.append(best_variant)
        out.append('')

        playlist = '\n'.join(out)

        if _has_new_exteplayer3():
            # v181: file:// funktioniert nicht, stattdessen localhost HTTP
            http_url = _serve_playlist_via_http(playlist)
            _log("build_single_quality_playlist: serviere via HTTP " + str(http_url))
            if http_url:
                return http_url
        else:
            if not os.path.isdir(_TMP_DIR):
                os.makedirs(_TMP_DIR)
            tmp_path = _tmp_playlist_path(master_url)
            playlist_bytes = playlist.encode('utf-8') if not isinstance(playlist, bytes) else playlist
            with open(tmp_path, 'wb') as f:
                f.write(playlist_bytes)
            _log("build_single_quality_playlist: serviere via Datei " + tmp_path)
            return 'file://' + tmp_path

    except Exception as e:
        _log("build_single_quality_playlist: Fehler " + str(e) + " -> master_url unveraendert")
    return master_url


def _resolve_stream(stream_url, title="ÖR Mediathek", force_player_id=None, is_live=False, autoconfigure_serviceapp=True):
    """
    Netzwerkteil von play_stream_async(): loest bei Bedarf die HLS-Master-Playlist
    auf eine fixe Qualitaet auf (_build_single_quality_playlist -> blockierender
    urlopen()-Call) und ermittelt den finalen player_id.
    MUSS in einem Hintergrundthread laufen, NIE direkt aus einem
    ActionMap-Tastendruck-Handler oder reactor.callFromThread-Callback - ein
    haengender DNS-/Netzwerkzugriff wuerde sonst wegen des GIL den kompletten
    Enigma2-Prozess inkl. WebIF einfrieren (siehe e2-magentatv Commit d1eb29d).
    Gibt (stream_url_bytes, title_bytes, player_id) zurueck.
    """
    if isinstance(stream_url, bytes):
        stream_url_str = stream_url.decode('utf-8', 'replace')
    else:
        stream_url_str = stream_url

    _log("_resolve_stream: title=" + str(title) + " is_live=" + str(is_live) + " url=" + str(stream_url_str))

    if "ard-mcdn.de" in stream_url_str:
        stream_url_str = stream_url_str.replace("https://", "http://", 1)

    is_orf = "apasfiis.sf.apa.at" in stream_url_str
    if not is_live and "ard-mcdn.de" in stream_url_str and "-progressive." not in stream_url_str and stream_url_str.split("?")[0].endswith(".m3u8"):
        is_live = True
        stream_url_str = re.sub(r'master\w+\.m3u8', 'master.m3u8', stream_url_str)


    # ORF _episodes: Q-Varianten sind gesperrt, QXA nicht (bis zu 720p, kein Login nötig)
    if is_orf and "_episodes" in stream_url_str:
        stream_url_str = re.sub(r'_Q[^./]+\.mp4', '_QXA.mp4', stream_url_str)

    # ORF VOD: Beste Qualität aus Master-Playlist wählen (VOR UA-Anhang)
    if is_orf and not is_live and stream_url_str.split("?")[0].split("#")[0].endswith(".m3u8"):
        stream_url_str = _build_single_quality_playlist(stream_url_str)

    # ORF: UA-Header setzen (nach Playlist-Auflösung, damit er an der finalen URL hängt)
    if is_orf and "#" not in stream_url_str and "|" not in stream_url_str:
        try:
            from Plugins.SystemPlugins.ServiceApp.serviceapp_caps import HAS_NATIVE_REFERER as has_new_serviceapp
        except ImportError:
            has_new_serviceapp = False

        if has_new_serviceapp:
            stream_url_str = stream_url_str + "|User-Agent=" + _ORF_USER_AGENT
        else:
            stream_url_str = stream_url_str + "#User-Agent=" + _ORF_USER_AGENT

    if is_live:
        stream_url_str = _build_single_quality_playlist(stream_url_str)

    if isinstance(stream_url_str, bytes):
        stream_url_bytes = stream_url_str
    else:
        stream_url_bytes = stream_url_str.encode('utf-8')

    if isinstance(title, bytes):
        title_bytes = title
    else:
        title_bytes = title.encode('utf-8')

    if force_player_id is not None:
        player_id = force_player_id
    elif not is_live and not is_orf and get_force_exteplayer() and _has_serviceapp():
        player_id = 5002
    elif (is_live or is_orf) and _has_serviceapp():
        if autoconfigure_serviceapp:
            _configure_serviceapp_for_live()
        player_id = 5002
    else:
        player_id = 4097

    _log("_resolve_stream: finale url=" + str(stream_url_str) + " player_id=" + str(player_id))

    return stream_url_bytes, title_bytes, player_id


def play_resolved_stream(session, stream_url_bytes, title_bytes, player_id, streams=None, stream_index=0, autoconfigure_serviceapp=True):
    """
    GUI-Thread-sicherer Teil: baut nur noch die eServiceReference und oeffnet
    den Player. Macht KEINE Netzwerkzugriffe - darf direkt aus dem GUI-/
    Reactor-Thread aufgerufen werden (z.B. per reactor.callFromThread im
    Anschluss an _resolve_stream() in play_stream_async()).
    """
    ref = eServiceReference(player_id, 0, stream_url_bytes)
    ref.setName(title_bytes)

    def _on_player_closed(*args):
        # Laeuft fuer JEDE Wiedergabe, egal aus welchem Screen gestartet - im
        # Gegensatz zu einem Hook nur in OeMediathekMainScreen.__on_show, der nie
        # feuert wenn man z.B. nur bis zur Episodenliste zurueckkehrt.
        # Zusaetzliches, unabhaengiges Sicherheitsnetz neben
        # OeStreamPlayer.__on_close - _restore_serviceapp_settings() ist
        # idempotent (No-Op ohne ausstehendes Backup), daher risikofrei doppelt.
        _restore_serviceapp_settings()
        if not get_live_tv_background():
            try:
                session.nav.playService(black_background_ref())
            except Exception:
                pass

    session.openWithCallback(_on_player_closed, OeStreamPlayer, ref, streams, stream_index, autoconfigure_serviceapp)


_active_play_thread_running = False

def play_stream_async(session, stream_url, title="ÖR Mediathek", force_player_id=None, is_live=False, autoconfigure_serviceapp=True, streams=None, stream_index=0):
    """
    Einziger Einstiegspunkt fuer ActionMap-Tastendruck-Handler, um einen Stream
    zu starten. Loest die URL in einem Hintergrundthread auf (kann blockierende
    Netzwerkzugriffe machen, siehe _resolve_stream()) und oeffnet den Player
    danach sicher per reactor.callFromThread im GUI-Thread.
    streams/stream_index: optionale flache Senderliste fuer CH+/--Wechsel im Player.
    """
    global _active_play_thread_running
    if _active_play_thread_running:
        _log("play_stream_async: Already starting a stream, ignoring.")
        return
    _active_play_thread_running = True

    def worker():
        global _active_play_thread_running
        try:
            stream_url_bytes, title_bytes, player_id = _resolve_stream(
                stream_url, title, force_player_id, is_live, autoconfigure_serviceapp
            )
        except Exception:
            _log("play_stream_async: Fehler bei _resolve_stream: " + _fmt_exc())
            _active_play_thread_running = False
            return
        try:
            from twisted.internet import reactor
            def _apply():
                global _active_play_thread_running
                _active_play_thread_running = False
                play_resolved_stream(session, stream_url_bytes, title_bytes, player_id, streams, stream_index, autoconfigure_serviceapp)
            reactor.callFromThread(_apply)
        except Exception:
            _log("play_stream_async: Fehler bei reactor.callFromThread: " + _fmt_exc())
            _active_play_thread_running = False

    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()
