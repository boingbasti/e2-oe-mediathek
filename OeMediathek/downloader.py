# -*- coding: utf-8 -*-
# downloader.py
# HTTP-Download fuer OeMediathek — laedt MP4/TS-Streams direkt auf die Festplatte

import os
import json
import threading
import re

# Python 2/3 Kompatibilitaet
try:
    from urllib2 import urlopen, Request, HTTPRedirectHandler, build_opener, HTTPSHandler
except ImportError:
    from urllib.request import urlopen, Request, HTTPRedirectHandler, build_opener, HTTPSHandler

try:
    import ssl
    _ssl_context = ssl._create_unverified_context()
except Exception:
    _ssl_context = None

SETTINGS_FILE    = "/etc/enigma2/oemediathek_settings.json"
DEFAULT_SAVE_DIR = "/media/hdd/movie/OeMediathek"

_ORF_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --------------------------------------------------------------------------
# Redirect-Handler (Behaelt Tarn-Header bei, blockiert aber falschen Host)
# --------------------------------------------------------------------------
class KeepHeadersRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newreq = HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if newreq:
            if hasattr(req, 'headers'):
                for key, val in req.headers.items():
                    if key.lower() not in ['host', 'content-length']:
                        newreq.add_header(key, val)
            if hasattr(req, 'unredirected_hdrs'):
                for key, val in req.unredirected_hdrs.items():
                    if key.lower() not in ['host', 'content-length']:
                        newreq.add_unredirected_header(key, val)
        return newreq

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
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.replace(u"\xe4", "ae").replace(u"\xf6", "oe").replace(u"\xfc", "ue")
    text = text.replace(u"\xdf", "ss")
    text = text.replace(u"\xc4", "Ae").replace(u"\xd6", "Oe").replace(u"\xdc", "Ue")
    text = re.sub(r'[^\w\s\-]', '', text)
    return text.strip().replace(" ", "_")

def _make_filename(title, url, topic=None):
    # m3u8 Playlisten werden als Enigma2-freundliche .ts Datei gespeichert
    ext = ".ts" if url.split("?")[0].lower().endswith((".m3u8", ".m3u")) else ".mp4"
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
    try:
        req = Request(url)
        req.add_header("User-Agent", _ORF_USER_AGENT)
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        req.add_header("Accept-Language", "de-DE,de;q=0.9,en-AT;q=0.8,en;q=0.7")
        req.get_method = lambda: "HEAD"
        
        handlers = [KeepHeadersRedirectHandler()]
        if _ssl_context:
            handlers.append(HTTPSHandler(context=_ssl_context))
        opener = build_opener(*handlers)
        
        resp = opener.open(req, timeout=10)
        length = resp.headers.get("Content-Length") or resp.info().get("Content-Length")
        if length:
            return int(length)
    except Exception:
        pass
    return 0

def format_size(size_bytes):
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
    CHUNK_SIZE = 256 * 1024

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
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(save_dir, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(save_dir, "%s_%d%s" % (base, counter, ext))
            counter += 1
        self.filepath = candidate

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def cancel(self):
        self._cancelled = True

    def _download_m3u8(self, opener, url):
        """Laedt HLS-Streams (m3u8) herunter, indem alle .ts Segmente aneinandergehaengt werden."""
        req = Request(url)
        req.add_header("User-Agent", _ORF_USER_AGENT)
        req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        req.add_header("Accept-Language", "de-DE,de;q=0.9,en-AT;q=0.8,en;q=0.7")
        
        resp = opener.open(req, timeout=30)
        manifest = resp.read().decode('utf-8', 'ignore')
        lines = manifest.split('\n')
        
        # 1. Ist es eine Master-Playlist? Dann die beste Qualitaet wahlen
        if "#EXT-X-STREAM-INF" in manifest:
            sub_url = None
            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF"):
                    for j in range(i+1, len(lines)):
                        if lines[j].strip() and not lines[j].startswith("#"):
                            sub_url = lines[j].strip()
                            break
                    break
            if sub_url:
                try:
                    from urlparse import urljoin
                except ImportError:
                    from urllib.parse import urljoin
                if not sub_url.startswith("http"):
                    sub_url = urljoin(url, sub_url)
                return self._download_m3u8(opener, sub_url)

        # 2. Es ist eine Segmente-Playlist (.ts Dateien sammeln)
        segments = []
        try:
            from urlparse import urljoin
        except ImportError:
            from urllib.parse import urljoin
            
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                if not line.startswith("http"):
                    line = urljoin(url, line)
                segments.append(line)
                
        if not segments:
            raise Exception("Keine Videosegmente im Stream gefunden")
            
        # UI-Update: Wir setzen _total auf 0, damit der Ladebalken MB statt % anzeigt
        self._total = 0
        self._downloaded = 0
        
        with open(self.filepath, "wb") as f:
            for seg_url in segments:
                if self._cancelled:
                    break
                seg_req = Request(seg_url)
                seg_req.add_header("User-Agent", _ORF_USER_AGENT)
                seg_resp = opener.open(seg_req, timeout=30)
                chunk = seg_resp.read()
                f.write(chunk)
                self._downloaded += len(chunk)
                if self.on_progress:
                    self.on_progress(self._downloaded, 0)

    def _run(self):
        try:
            save_dir = get_save_dir()
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)

            handlers = [KeepHeadersRedirectHandler()]
            if _ssl_context:
                handlers.append(HTTPSHandler(context=_ssl_context))
            opener = build_opener(*handlers)
            
            is_m3u8 = self.url.split("?")[0].lower().endswith((".m3u8", ".m3u"))
            
            if is_m3u8:
                # Nutzt den neuen HLS/m3u8 Ripper
                self._download_m3u8(opener, self.url)
            else:
                # Standard MP4-Download
                req = Request(self.url)
                req.add_header("User-Agent", _ORF_USER_AGENT)
                req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                req.add_header("Accept-Language", "de-DE,de;q=0.9,en-AT;q=0.8,en;q=0.7")
                
                resp = opener.open(req, timeout=30)

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
            try:
                if os.path.exists(self.filepath):
                    os.remove(self.filepath)
            except Exception:
                pass
            if self.on_error:
                self.on_error(str(e))
