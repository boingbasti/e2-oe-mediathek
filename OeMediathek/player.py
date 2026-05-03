# -*- coding: utf-8 -*-
# player.py
# Startet einen Stream im angepassten Enigma2-Mediaplayer

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


_ORF_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_TMP_DIR      = "/tmp/OeMediathek"
_TMP_PLAYLIST = _TMP_DIR + "/live.m3u8"


def _has_serviceapp():
    """Prueft lautlos, ob das Systemplugin ServiceApp auf der Box installiert ist."""
    return os.path.exists("/usr/lib/enigma2/python/Plugins/SystemPlugins/ServiceApp")


def _configure_serviceapp_for_live():
    """Setzt serviceapp-Einstellungen fuer synchrone HLS-Live-Streams (einmalig)."""
    try:
        from Components.config import config
        key = "serviceexteplayer3"
        opts  = config.plugins.serviceapp.options[key]
        ext3  = config.plugins.serviceapp.exteplayer3[key]
        changed = False
        if not opts.hls_explorer.value:
            opts.hls_explorer.value = True;   opts.hls_explorer.save();   changed = True
        if opts.autoselect_stream.value:
            opts.autoselect_stream.value = False; opts.autoselect_stream.save(); changed = True
        if not ext3.aac_swdecoding.value:
            ext3.aac_swdecoding.value = True; ext3.aac_swdecoding.save(); changed = True
        if not ext3.downmix.value:
            ext3.downmix.value = True;        ext3.downmix.save();        changed = True
        return changed
    except Exception:
        return False


def _build_single_quality_playlist(master_url):
    """
    Laedt die HLS-Master-Playlist, waehlt die beste Variante und schreibt eine
    modifizierte Playlist nach /tmp/, die nur diese eine Variante enthaelt
    (kein ABR-Wechsel) aber alle Audio-Tracks behaelt.
    Gibt 'file:///tmp/...' zurueck, oder master_url bei Fehler.
    """
    try:
        req = _Request(master_url)
        req.add_header('User-Agent', 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36')
        resp = urlopen(req, timeout=4)
        content = resp.read().decode('utf-8', 'replace')
        lines = content.splitlines()

        # Beste Variante (hoechste Bandbreite) finden
        best_bw        = -1
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
            return master_url

        # Neue Playlist: Header + alle EXT-X-MEDIA + beste Variante
        out = ['#EXTM3U', '#EXT-X-VERSION:4', '#EXT-X-INDEPENDENT-SEGMENTS', '']

        for line in lines:
            if line.startswith('#EXT-X-MEDIA'):
                # Relative URI="..." auf absolute URL umschreiben
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

        if not os.path.isdir(_TMP_DIR):
            os.makedirs(_TMP_DIR)
        with open(_TMP_PLAYLIST, 'w') as f:
            f.write('\n'.join(out))

        return 'file://' + _TMP_PLAYLIST

    except Exception:
        pass
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

    is_orf = "apasfiis.sf.apa.at" in stream_url_str

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
        if is_live and autoconfigure_serviceapp:
            _configure_serviceapp_for_live()
        player_id = 5002
    else:
        player_id = 4097

    ref = eServiceReference(player_id, 0, stream_url_bytes)
    ref.setName(title_bytes)

    session.open(OeStreamPlayer, ref)
