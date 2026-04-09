# -*- coding: utf-8 -*-
# downloader.py
# HTTP-Download fuer OeMediathek — laedt MP4-Streams direkt auf die Festplatte

import os
import json
import threading
import re

# Python 2/3 Kompatibilitaet
try:
    from urllib2 import urlopen, Request
except ImportError:
    from urllib.request import urlopen, Request

try:
    import ssl
    _ssl_context = ssl._create_unverified_context()
except Exception:
    _ssl_context = None

SETTINGS_FILE    = "/etc/enigma2/oemediathek_settings.json"
DEFAULT_SAVE_DIR = "/media/hdd/movie/OeMediathek"

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, ensure_ascii=False)
    except Exception:
        pass


def get_save_dir():
    return load_settings().get("save_dir", DEFAULT_SAVE_DIR)


def set_save_dir(path):
    s = load_settings()
    s["save_dir"] = path
    save_settings(s)


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def _sanitize(text):
    """Umlaute ersetzen und Sonderzeichen entfernen für sichere Dateinamen."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.replace(u"\xe4", "ae").replace(u"\xf6", "oe").replace(u"\xfc", "ue")
    text = text.replace(u"\xdf", "ss")
    text = text.replace(u"\xc4", "Ae").replace(u"\xd6", "Oe").replace(u"\xdc", "Ue")
    text = re.sub(r'[^\w\s\-]', '', text)
    return text.strip().replace(" ", "_")


def _make_filename(title, url, topic=None):
    """Erstellt einen sicheren Dateinamen aus Titel (und optionalem Seriennamen)."""
    ext = ".mp4"
    safe_title = _sanitize(title) or "download"
    if topic:
        safe_topic = _sanitize(topic)
        if safe_topic and safe_topic.lower() != safe_title.lower():
            combined = safe_topic + "_-_" + safe_title
        else:
            combined = safe_title
    else:
        combined = safe_title
    return combined[:100] + ext


def get_content_length(url):
    """
    Liefert die Dateigroe&szlig;e in Bytes via HTTP HEAD-Request.
    Gibt 0 zurueck wenn nicht ermittelbar.
    """
    try:
        req = Request(url)
        req.get_method = lambda: "HEAD"
        resp = urlopen(req, timeout=10, context=_ssl_context) if _ssl_context else urlopen(req, timeout=10)
        length = resp.headers.get("Content-Length") or resp.info().get("Content-Length")
        if length:
            return int(length)
    except Exception:
        pass
    return 0


def format_size(size_bytes):
    """Lesbare Dateigroesse (z.B. '452 MB')."""
    if size_bytes <= 0:
        return "unbekannte Groesse"
    if size_bytes >= 1024 * 1024 * 1024:
        return "%.1f GB" % (size_bytes / 1024.0 / 1024.0 / 1024.0)
    if size_bytes >= 1024 * 1024:
        return "%.0f MB" % (size_bytes / 1024.0 / 1024.0)
    return "%.0f KB" % (size_bytes / 1024.0)


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

class Downloader(object):
    """
    Laedt eine Datei im Hintergrund herunter.
    Fortschritt und Status werden ueber Callbacks gemeldet.

    on_progress(downloaded_bytes, total_bytes)  — regelmaessig waehrend Download
    on_done(filepath)                           — Download erfolgreich abgeschlossen
    on_error(message)                           — Fehler aufgetreten
    """

    CHUNK_SIZE = 256 * 1024  # 256 KB pro Chunk

    def __init__(self, url, title, topic=None, on_progress=None, on_done=None, on_error=None):
        self.url         = url
        self.title       = title
        self.on_progress = on_progress
        self.on_done     = on_done
        self.on_error    = on_error

        self._cancelled  = False
        self._thread     = None
        self._downloaded = 0
        self._total      = 0

        save_dir = get_save_dir()
        filename = _make_filename(title, url, topic=topic)
        self.filepath = os.path.join(save_dir, filename)

    def start(self):
        """Startet den Download in einem Background-Thread."""
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def cancel(self):
        """Bricht den laufenden Download ab."""
        self._cancelled = True

    def _run(self):
        try:
            save_dir = get_save_dir()
            # Zielverzeichnis anlegen falls nicht vorhanden
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)

            req = Request(self.url)
            resp = urlopen(req, timeout=30, context=_ssl_context) if _ssl_context else urlopen(req, timeout=30)

            total = 0
            try:
                length = resp.headers.get("Content-Length") or resp.info().get("Content-Length")
                if length:
                    total = int(length)
            except Exception:
                pass

            downloaded = 0
            with open(self.filepath, "wb") as f:
                while not self._cancelled:
                    chunk = resp.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    self._downloaded = downloaded
                    self._total      = total
                    if self.on_progress:
                        self.on_progress(downloaded, total)

            if self._cancelled:
                # Abgebrochene Datei loeschen
                try:
                    os.remove(self.filepath)
                except Exception:
                    pass
                if self.on_error:
                    self.on_error("Abgebrochen")
            else:
                if self.on_done:
                    self.on_done(self.filepath)

        except Exception as e:
            # Unvollstaendige Datei loeschen
            try:
                if os.path.exists(self.filepath):
                    os.remove(self.filepath)
            except Exception:
                pass
            if self.on_error:
                self.on_error(str(e))
