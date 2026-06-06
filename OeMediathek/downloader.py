# -*- coding: utf-8 -*-
# downloader.py
# HTTP-Download fuer OeMediathek — laedt MP4/TS-Streams direkt auf die Festplatte

import os
import json
import threading
import re
import subprocess
import time

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
_LOG_FILE        = "/tmp/OeMediathek/oemediathek.log"

_ORF_USER_AGENT = "OeMediathek/1.0"

def _log(msg):
    if not get_debug_logging():
        return
    line = "[OeMediathek %s] DL: %s" % (time.strftime("%H:%M:%S", time.localtime()), str(msg))
    print(line)
    try:
        log_dir = os.path.dirname(_LOG_FILE)
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        with open(_LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

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

def get_auto_convert():
    return load_settings().get("auto_convert_ts", False)

def set_auto_convert(enabled):
    s = load_settings()
    s["auto_convert_ts"] = bool(enabled)
    save_settings(s)

def get_tile_wrap_lr():
    return load_settings().get("tile_wrap_lr", True)

def set_tile_wrap_lr(enabled):
    s = load_settings()
    s["tile_wrap_lr"] = bool(enabled)
    save_settings(s)

def get_serviceapp_autoconfigure():
    return load_settings().get("serviceapp_autoconfigure", True)

def set_serviceapp_autoconfigure(enabled):
    s = load_settings()
    s["serviceapp_autoconfigure"] = bool(enabled)
    save_settings(s)

def get_debug_logging():
    return load_settings().get("debug_logging", False)

def set_debug_logging(enabled):
    s = load_settings()
    s["debug_logging"] = bool(enabled)
    save_settings(s)

def write_info_txt(filepath, title, description=None, duration=None, topic=None):
    """Schreibt eine .txt Datei mit Sendungsinfos neben die Download-Datei."""
    try:
        txt_path = os.path.splitext(filepath)[0] + ".txt"
        def _dec(v):
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return v or ""
        lines = []
        t = _dec(title)
        if t:
            lines.append(t)
        d = _dec(description)
        if d:
            lines.append(d)
        dur = _dec(duration)
        if dur:
            lines.append(u"Laufzeit: " + dur)
        top = _dec(topic)
        if top and top.lower() != t.lower():
            lines.append(u"Sendung: " + top)
        if lines:
            with open(txt_path, "w") as f:
                f.write(u"\n\n".join(lines).encode("utf-8"))
    except Exception:
        pass

def write_meta(filepath, title, description=None, duration=None):
    """Schreibt eine Enigma2 .meta Datei neben die Download-Datei (Datum, Titel, Beschreibung)."""
    try:
        meta_path = filepath + ".meta"
        def _dec(v):
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return v or u""
        display_name = os.path.splitext(os.path.basename(filepath))[0]
        desc_str  = _dec(description)
        ts        = int(time.time())
        dur_secs  = 0
        dur_str   = _dec(duration)
        if dur_str:
            parts = dur_str.strip().split(":")
            try:
                if len(parts) == 3:
                    dur_secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    dur_secs = int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError):
                pass
        lines = [
            u"",
            display_name,
            desc_str,
            str(ts),
            u"",
            str(dur_secs) if dur_secs else u"",
        ]
        with open(meta_path, "w") as f:
            f.write(u"\n".join(lines).encode("utf-8"))
    except Exception:
        pass


def convert_mp4_to_ts(mp4_path, on_done=None, on_error=None):
    """Konvertiert mp4_path verlustfrei zu .ts (ffmpeg -c copy) in einem Background-Thread."""
    def _run():
        ts_path = os.path.splitext(mp4_path)[0] + ".ts"
        try:
            _log("ffmpeg Start: %s" % mp4_path)
            cmd = ["ffmpeg", "-y", "-i", mp4_path, "-c", "copy", ts_path]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _out, _err = proc.communicate()
            if proc.returncode != 0:
                raise Exception("ffmpeg Fehler (Code %d)" % proc.returncode)
            try:
                os.remove(mp4_path)
            except Exception:
                pass
            try:
                mp4_meta = mp4_path + ".meta"
                if os.path.exists(mp4_meta):
                    os.rename(mp4_meta, ts_path + ".meta")
            except Exception:
                pass
            _log("ffmpeg Fertig: %s" % ts_path)
            if on_done:
                on_done(ts_path)
        except Exception as e:
            _log("ffmpeg Fehler: %s — %s" % (mp4_path, str(e)))
            try:
                if os.path.exists(ts_path):
                    os.remove(ts_path)
            except Exception:
                pass
            if on_error:
                on_error(str(e))
    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()

# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def _sanitize(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    allowed = set(u"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_\xe4\xf6\xfc\xc4\xd6\xdc\xdf")
    return u"".join(c for c in text if c in allowed).strip()

def _make_filename(title, url, topic=None):
    # m3u8 Playlisten werden als Enigma2-freundliche .ts Datei gespeichert
    ext = ".ts" if url.split("?")[0].lower().endswith((".m3u8", ".m3u")) else ".mp4"
    safe_title = _sanitize(title) or "download"
    if topic:
        safe_topic = _sanitize(topic)
        if safe_topic and safe_topic.lower() != safe_title.lower():
            combined = safe_topic + " - " + safe_title
        else:
            combined = safe_title
    else:
        combined = safe_title
    result = combined[:100] + ext
    if isinstance(result, bytes):
        return result
    return result.encode("utf-8")

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

    def __init__(self, url, title, topic=None, description=None, duration=None, on_progress=None, on_done=None, on_error=None):
        # ORF _episodes: Q-Varianten gesperrt, QXA funktioniert
        if "apasfiis.sf.apa.at" in url and "_episodes" in url:
            url = re.sub(r'_Q[^./]+\.mp4', '_QXA.mp4', url)
        self.url         = url
        self.title       = title
        self.description = description
        self.duration    = duration
        self.topic       = topic
        self.on_progress = on_progress
        self.on_done     = on_done
        self.on_error    = on_error

        self._cancelled  = False
        self._thread     = None
        self._downloaded = 0
        self._total      = 0

        save_dir = get_save_dir()
        if isinstance(save_dir, bytes):
            save_dir = save_dir.decode("utf-8", "replace")
        filename = _make_filename(title, url, topic=topic)
        if isinstance(filename, bytes):
            filename = filename.decode("utf-8", "replace")
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(save_dir, filename).encode("utf-8")
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(save_dir, u"%s_%d%s" % (base, counter, ext)).encode("utf-8")
            counter += 1
        self.filepath = candidate

    def start(self):
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def cancel(self):
        self._cancelled = True

    def _download_hls_parallel(self, workers=4):
        """Laedt HLS-Segmente parallel (workers gleichzeitig) und muxiert Audio+Video mit ffmpeg."""
        import threading
        try:
            from urlparse import urljoin
        except ImportError:
            from urllib.parse import urljoin

        try:
            _fetch_opener = build_opener(HTTPSHandler(context=_ssl_context)) if _ssl_context else None
        except Exception:
            _fetch_opener = None

        def fetch(url, retries=4):
            for attempt in range(retries):
                try:
                    r = Request(url)
                    r.add_header("User-Agent", _ORF_USER_AGENT)
                    if _fetch_opener:
                        return _fetch_opener.open(r, timeout=30).read()
                    return urlopen(r, timeout=30).read()
                except Exception as e:
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                    else:
                        raise

        def get_segments(playlist_url):
            data = fetch(playlist_url).decode("utf-8", "ignore")
            return [urljoin(playlist_url, l.strip())
                    for l in data.splitlines()
                    if l.strip() and not l.strip().startswith("#")]

        # Master-Playlist auswerten
        master = fetch(self.url).decode("utf-8", "ignore")
        lines = master.splitlines()
        audio_url, best_bw, best_video_url = None, -1, None
        for line in lines:
            if line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line:
                m = re.search(r'URI="([^"]+)"', line)
                if m:
                    audio_url = urljoin(self.url, m.group(1))
        i = 0
        while i < len(lines):
            if lines[i].startswith("#EXT-X-STREAM-INF"):
                bw_m = re.search(r"BANDWIDTH=(\d+)", lines[i])
                bw = int(bw_m.group(1)) if bw_m else 0
                for j in range(i + 1, len(lines)):
                    v = lines[j].strip()
                    if v and not v.startswith("#"):
                        if bw > best_bw:
                            best_bw, best_video_url = bw, urljoin(self.url, v)
                        break
            i += 1
        if not best_video_url:
            best_video_url = self.url

        video_segs = get_segments(best_video_url)
        audio_segs = get_segments(audio_url) if audio_url else []
        _log("ORF parallel: %d Video + %d Audio Segmente, %d workers" % (len(video_segs), len(audio_segs), workers))

        fp = self.filepath if isinstance(self.filepath, str) else self.filepath.decode("utf-8", "replace")
        vid_tmp = fp + ".vid.tmp"
        aud_tmp = fp + ".aud.tmp"
        self._total = 0
        self._downloaded = 0

        def download_batched(segs, out_path):
            with open(out_path, "wb") as f:
                for start in range(0, len(segs), workers):
                    if self._cancelled:
                        return
                    batch = segs[start:start + workers]
                    results = [None] * len(batch)
                    errors = [None]

                    def _worker(url, idx):
                        try:
                            results[idx] = fetch(url)
                        except Exception as e:
                            errors[0] = e

                    threads = [threading.Thread(target=_worker, args=(url, idx))
                               for idx, url in enumerate(batch)]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join()
                    if errors[0]:
                        raise errors[0]
                    for data in results:
                        if data:
                            f.write(data)
                            self._downloaded += len(data)
                            if self.on_progress:
                                self.on_progress(self._downloaded, 0)

        download_batched(video_segs, vid_tmp)
        if self._cancelled:
            for p in (vid_tmp,):
                try: os.remove(p)
                except Exception: pass
            return

        if audio_segs:
            download_batched(audio_segs, aud_tmp)
            if self._cancelled:
                for p in (vid_tmp, aud_tmp):
                    try: os.remove(p)
                    except Exception: pass
                return
            cmd = ["ffmpeg", "-y", "-i", vid_tmp, "-i", aud_tmp,
                   "-c", "copy", "-f", "mpegts", fp]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.wait()
            for p in (vid_tmp, aud_tmp):
                try: os.remove(p)
                except Exception: pass
            if proc.returncode != 0:
                err = proc.stderr.read()[-300:]
                raise Exception("ffmpeg Mux Fehler (Code %d): %s" % (proc.returncode, err))
        else:
            os.rename(vid_tmp, fp)
        _log("ORF parallel fertig: %s" % fp)

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
            _log("Start: %s" % self.title)
            save_dir = get_save_dir()
            save_dir_b = save_dir if isinstance(save_dir, bytes) else save_dir.encode("utf-8")
            if not os.path.exists(save_dir_b):
                os.makedirs(save_dir_b)

            handlers = [KeepHeadersRedirectHandler()]
            if _ssl_context:
                handlers.append(HTTPSHandler(context=_ssl_context))
            opener = build_opener(*handlers)
            
            is_m3u8 = self.url.split("?")[0].lower().endswith((".m3u8", ".m3u"))
            
            if is_m3u8 and "apasfiis.sf.apa.at" in self.url:
                # ORF HLS: parallele Segment-Downloads (umgeht CDN-Drosselung pro Verbindung)
                self._download_hls_parallel(workers=4)
            elif is_m3u8:
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
                _log("Abgebrochen: %s" % self.title)
                if self.on_error:
                    self.on_error("Abgebrochen")
            else:
                write_info_txt(self.filepath, self.title, self.description, self.duration, self.topic)
                write_meta(self.filepath, self.title, self.description, self.duration)
                _log("Fertig: %s" % self.title)
                if self.on_done:
                    self.on_done(self.filepath)

        except Exception as e:
            _log("Fehler: %s — %s" % (self.title, str(e)))
            try:
                if os.path.exists(self.filepath):
                    os.remove(self.filepath)
            except Exception:
                pass
            if self.on_error:
                self.on_error(str(e))
