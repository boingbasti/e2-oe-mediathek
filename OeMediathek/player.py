# -*- coding: utf-8 -*-
# player.py
# Startet einen Stream im angepassten Enigma2-Mediaplayer

import hashlib
import os
import re

try:
    from urllib2 import urlopen, Request as _Request
except ImportError:
    from urllib.request import urlopen, Request as _Request

try:
    from urlparse import urljoin as _urljoin
except ImportError:
    from urllib.parse import urljoin as _urljoin

from enigma import eServiceReference

from downloader import get_debug_logging

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
    def __init__(self, session, service):
        MoviePlayer.__init__(self, session, service)
        # Nimmt den normalen MoviePlayer-Skin (für OSD/Statusleiste)
        self.skinName = ["MoviePlayer", "InfoBar"]

    def leavePlayer(self):
        # Verhindert die "Wiedergabe beenden?" Abfrage, wenn man EXIT drückt
        self.close()

    def doEofInternal(self, playing):
        # Schließt den Player sofort sauber, wenn der Stream von selbst zu Ende ist
        self.close()

    def showResumePoint(self):
        # Verhindert die Abfrage "An letzter Position fortsetzen?"
        pass


_ORF_USER_AGENT = "OeMediathek/1.0"

_TMP_DIR = "/tmp/OeMediathek"


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


def _has_new_exteplayer3():
    """exteplayer3 >= v181 (feedplus) bringt eigene Libs in /usr/lib/exteplayer3_deps/."""
    return os.path.isdir("/usr/lib/exteplayer3_deps")


def _configure_serviceapp_for_live():
    """Setzt serviceapp-Einstellungen fuer synchrone HLS-Live-Streams.
    Bei exteplayer3 >= v181 wird aac_swdecoding nicht gesetzt (inkompatibel mit
    altem serviceapp.so: generiert '-a' ohne Wert, v181 erwartet '-a 0|1|2|3').
    """
    try:
        from Components.config import config
        from Plugins.SystemPlugins.ServiceApp.serviceapp_client import (
            setExtEplayer3Settings, setServiceAppSettings, OPTIONS_SERVICEEXTEPLAYER3
        )
        key  = "serviceexteplayer3"
        opts = config.plugins.serviceapp.options[key]
        ext3 = config.plugins.serviceapp.exteplayer3[key]
        changed = False

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

        # Bei v181 aac_swdecoding=False erzwingen: altes serviceapp.so wuerde sonst
        # '-a' ohne Wert generieren (Boolean-Flag statt 0|1|2|3) -> exteplayer3 v181 haengt.
        aac_sw = False if _has_new_exteplayer3() else ext3.aac_swdecoding.value
        setExtEplayer3Settings(
            OPTIONS_SERVICEEXTEPLAYER3,
            aac_sw,
            ext3.dts_swdecoding.value,
            ext3.wma_swdecoding.value,
            ext3.lpcm_injecion.value,
            ext3.downmix.value
        )
        setServiceAppSettings(
            OPTIONS_SERVICEEXTEPLAYER3,
            opts.hls_explorer.value,
            opts.autoselect_stream.value,
            opts.connection_speed_kb.value,
            opts.autoturnon_subtitles.value
        )
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
            with open(tmp_path, 'w') as f:
                f.write(playlist)
            _log("build_single_quality_playlist: serviere via Datei " + tmp_path)
            return 'file://' + tmp_path

    except Exception as e:
        _log("build_single_quality_playlist: Fehler " + str(e) + " -> master_url unveraendert")
    return master_url


def play_stream(session, stream_url, title="ÖR Mediathek", force_player_id=None, is_live=False, autoconfigure_serviceapp=True):
    """
    Spielt eine URL im eigenen, angepassten Enigma2-Player ab.
    Nutzt standardmaessig 4097 (GStreamer). Nur bei ORF-Streams wird,
    falls verfuegbar, auf 5002 (exteplayer3) gewechselt.
    Bei is_live=True wird die Master-Playlist auf eine fixe Qualitaet reduziert
    und, falls autoconfigure_serviceapp=True, serviceapp fuer synchrone Wiedergabe konfiguriert.
    force_player_id erzwingt einen bestimmten Service-Typ.
    """
    if isinstance(stream_url, bytes):
        stream_url_str = stream_url.decode('utf-8', 'replace')
    else:
        stream_url_str = stream_url

    _log("play_stream: title=" + str(title) + " is_live=" + str(is_live) + " url=" + str(stream_url_str))

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
    if is_orf and "#" not in stream_url_str:
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
    elif (is_live or is_orf) and _has_serviceapp():
        if (is_live or is_orf) and autoconfigure_serviceapp:
            _configure_serviceapp_for_live()
        player_id = 5002
    else:
        player_id = 4097

    _log("play_stream: finale url=" + str(stream_url_str) + " player_id=" + str(player_id))

    ref = eServiceReference(player_id, 0, stream_url_bytes)
    ref.setName(title_bytes)

    session.open(OeStreamPlayer, ref)
