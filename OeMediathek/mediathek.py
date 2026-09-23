# -*- coding: utf-8 -*-
# mediathek.py
# Holt Sendungslisten über MediathekViewWeb-API (aggregiert alle ÖR-Sender)

import io
import json
import os
import re as _re
import threading
import time

LOG_FILE            = "/tmp/OeMediathek/oemediathek.log"
FAVORITES_FILE         = "/etc/enigma2/oemediathek_favorites.json"
EPISODE_FAVORITES_FILE = "/etc/enigma2/oemediathek_episode_favorites.json"
WATCHED_FILE           = "/etc/enigma2/oemediathek_watched.json"
SEARCH_HISTORY_FILE    = "/etc/enigma2/oemediathek_search_history.json"
SEARCH_HISTORY_MAX  = 10
DEBUG               = False

try:
    from downloader import get_debug_logging as _get_debug_logging
except ImportError:
    _get_debug_logging = None

# Bekannte Sendernamen fuer die Favoriten-Bereinigung (Duplikat zu CHANNEL_MAP in plugin.py,
# aber mediathek.py soll ohne plugin.py lauffaehig bleiben).
_KNOWN_CHANNELS = {
    "ARD", "ZDF", "ARTE", "3Sat", "NDR", "WDR", "BR", "MDR", "HR", "SWR",
    "RBB", "SR", "ZDFinfo", "ZDFneo", "KiKA", "PHOENIX",
    "Radio Bremen TV", "Funk.net", "ARD-alpha", "ONE", "tagesschau24", "DW",
}

# Generische Film/Doku-Container: topic ist nur ein Genre-Label, Titel direkt als Gruppe nutzen.
_FILM_TOPICS = {
    # Spielfilme
    "film", "filme", "spielfilm", "spielfilme", "kinofilm", "kino",
    "tv-film", "fernsehfilm", "fernsehfilme", "maerchenfilm", "kurzfilm",
    "film-highlights", "film-klassiker",
    # Dokumentationen & Reportagen
    "dokumentation", "dokumentationen", "dokumentarfilm", "doku", "dokis",
    "reportage", "reportagen", "feature",
}

def _s(val):
    """Unicode-String zu UTF-8 Byte-String für Python 2 / Enigma2."""
    if isinstance(val, bytes):
        return val
    try:
        return val.encode('utf-8')
    except Exception:
        return str(val)

def _log(msg):
    enabled = _get_debug_logging() if _get_debug_logging else DEBUG
    if not enabled:
        return
    line = "[OeMediathek %s] MW: %s" % (time.strftime("%H:%M:%S", time.localtime()), str(msg))
    print(line)
    try:
        log_dir = os.path.dirname(LOG_FILE)
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# Python 2/3 Kompatibilität
try:
    from urllib2 import urlopen, Request
except ImportError:
    from urllib.request import urlopen, Request

try:
    import ssl
    _ssl_context = ssl._create_unverified_context()
except Exception:
    _ssl_context = None


def _normalize_stream_url(url):
    """MVW liefert bei manchen Feeds (bestaetigt: BR/ARD-alpha) die Stream-URL
    protokollrelativ ohne Schema zurueck ("//cdn-storage.br.de/..." statt
    "https://cdn-storage.br.de/..."). exteplayer3/serviceapp interpretiert das
    dann als lokalen Dateipfad (file:////cdn-storage.br.de/...), findet die
    Datei natuerlich nicht und beendet sich sofort ohne sichtbaren Fehler
    (serviceapp.log: PLAYBACK_OPEN sts=-1, exteplayer3 exited innerhalb weniger
    Millisekunden). Fehlendes Schema hier ergaenzen, bevor die URL irgendwo
    weiterverwendet wird."""
    if url and url.startswith("//"):
        return "https:" + url
    return url


def _dedupe_by_url(items):
    """Entfernt Duplikate anhand des URL-Pfads (Hostname ignoriert, da CDN-Varianten
    existieren). MVW liefert vereinzelt denselben Eintrag doppelt oder dreifach zurueck
    (z.B. durch Re-Indexierung eines Senderfeeds) - betraf u.a. "Sendung verpasst?".
    Behaelt die Reihenfolge, der erste Treffer gewinnt."""
    seen = set()
    result = []
    for item in items:
        url = item.get("stream_url_hd") or item.get("stream_url_sd") or b""
        url_str = url.decode("utf-8", "replace") if isinstance(url, bytes) else (url or "")
        try:
            path_key = url_str.split("://", 1)[1].split("/", 1)[1] if "://" in url_str else url_str
        except Exception:
            path_key = url_str
        if path_key and path_key in seen:
            continue
        if path_key:
            seen.add(path_key)
        result.append(item)
    return result


# ------------------------------------------------------------------
# MediathekViewWeb-API
# POST https://mediathekviewweb.de/api/query
# ------------------------------------------------------------------
def _mvw_query(channel=None, size=100, offset=0, search_term=None, min_duration=0, sort_by="timestamp", search_fields=None, topic_filter=None):
    """
    Fragt die MediathekViewWeb-API ab.
    search_fields: Liste der Felder fuer die Suche, Standard ["title", "topic"]
    sort_by: "timestamp" | "duration" | "topic" (alle API-seitig)
    topic_filter: schraenkt Suche auf exaktes topic ein (zusaetzliche Query-Bedingung)
    """
    url = "https://mediathekviewweb.de/api/query"

    queries = []
    if channel:
        queries.append({"fields": ["channel"], "query": channel})

    if topic_filter:
        queries.append({"fields": ["topic"], "query": topic_filter})

    if search_term:
        fields = search_fields if search_fields else ["title", "topic"]
        queries.append({"fields": fields, "query": search_term})

    if sort_by in ("topic", "title"):
        api_sort, sort_order = sort_by, "asc"
    elif sort_by in ("timestamp", "duration"):
        api_sort, sort_order = sort_by, "desc"
    else:
        api_sort, sort_order = "timestamp", "desc"

    body_dict = {
        "queries":   queries,
        "sortBy":    api_sort,
        "sortOrder": sort_order,
        "future":    True,
        "offset":    offset,
        "size":      size,
    }
    if min_duration > 0:
        body_dict["duration_min"] = min_duration

    body = json.dumps(body_dict)

    if isinstance(body, str):
        body = body.encode("utf-8")

    req = Request(url, data=body)
    req.add_header("Content-Type", "application/json")

    _log("MVW Abruf channel=%s offset=%d size=%d search=%s" % (channel, offset, size, search_term))
    try:
        resp = urlopen(req, timeout=15, context=_ssl_context) if _ssl_context else urlopen(req, timeout=15)
        _log("MVW HTTP %s" % resp.getcode())
        data = json.loads(resp.read())
    except Exception as e:
        _log("MVW Fehler: " + str(e))
        raise 

    results_raw  = data.get("result", {}).get("results", [])
    total_results = data.get("result", {}).get("queryInfo", {}).get("totalResults", 0)
    try:
        total_results = int(total_results)
    except Exception:
        total_results = 0
    raw_count = len(results_raw)
    _log("MVW %d Ergebnisse (gesamt: %d)" % (raw_count, total_results))

    # Sender die in DE nicht verfuegbar sind ausblenden (nur bei "Alle Mediatheken").
    # Hinweis: Die API hat keinen Exclude-Parameter. Wenn eine Seite viele geblockte
    # Eintraege enthaelt, kann die zurueckgegebene Liste kuerzer als "size" sein.
    # Das ist ein bekanntes, akzeptiertes Verhalten ohne saubere Gegenmassnahme.
    blocked = {"ORF", "SRF"} if not channel else set()

    items = []
    for entry in results_raw:
        ch         = entry.get("channel", "")
        topic      = entry.get("topic", "")
        title      = entry.get("title", "")
        timestamp  = entry.get("timestamp", 0)

        if ch in blocked:
            continue

        # Arte: nur deutschsprachige Inhalte (ARTE.DE) behalten.
        # Die API liefert auch ARTE.FR, ARTE.IT etc. bei channel="ARTE"-Abfragen.
        if ch.upper().startswith("ARTE") and ch.upper() != "ARTE.DE":
            continue

        # MVW-Channel-Suche ist eine Textsuche, kein Exact-Match: "ARD" matcht auch
        # "ARD-alpha", "ZDF" matcht auch "ZDFneo"/"ZDF-tivi", "SR" matcht auch "SRF".
        # Fuer eigene Sender-Kacheln (channel-Filter gesetzt) nur exakte Treffer
        # behalten, sonst landen Sendungen anderer, eigenstaendiger Kacheln zusaetzlich
        # in der falschen Kachel (z.B. RESPEKT/ARD-alpha in "ARD Mediathek").
        if channel and ch.upper() != channel.upper() and not (channel.upper() == "ARTE" and ch.upper() == "ARTE.DE"):
            continue

        # HD und SD getrennt auslesen
        # ORF: Q8C (HD) seit 2026-06 auf "video_not_available"-Tafel umgeleitet — kein HD anbieten
        url_hd     = "" if ch.upper() == "ORF" else _normalize_stream_url(entry.get("url_video_hd") or "")
        url_sd     = _normalize_stream_url(entry.get("url_video") or "")
        
        desc       = entry.get("description", "")
        duration   = entry.get("duration", 0)

        # API kuerzt Beschreibungen mit "\n....." — nur das API-Artefakt entfernen,
        # echte Satzpunkte aber in Ruhe lassen.
        if desc:
            desc = desc.rstrip()
            # "\n....." am Ende ist ein API-Kuerzel fuer abgeschnittenen Text
            while desc.endswith("\n.") or desc.endswith("\n..") or desc.endswith("\n...") \
                    or desc.endswith("\n....") or desc.endswith("\n....."):
                desc = desc.rsplit("\n", 1)[0].rstrip()
            if len(entry.get("description", "")) >= 400:
                desc = desc + " ..."

        # Überspringen, wenn gar kein Stream vorhanden ist
        if not title or (not url_hd and not url_sd):
            continue

        # Audiodeskriptions- und Gebaerdensprach-Fassungen ausblenden
        if title.endswith("(Audiodeskription)") or title.endswith(u"(Geb\xe4rdensprache)") or title.endswith(u"(\xd6GS)"):
            continue

        try:
            duration = int(duration)
        except:
            duration = 0

        if duration > 0:
            m, s = divmod(duration, 60)
            h, m = divmod(m, 60)
            if h > 0:
                duration_str = "%d:%02d Std." % (h, m)
            else:
                duration_str = "%d Min." % m
        else:
            duration_str = "Unbekannt"

        if not desc:
            desc = "Keine Beschreibung verfügbar."

        # Generische Film-Container aufbrechen: topic ist ein Sammelbehaelter
        # (z.B. "Filme", "Spielfilm") → Filmtitel direkt als Gruppenname verwenden.
        topic_lower = topic.lower() if topic else ""
        is_film_container = topic_lower in _FILM_TOPICS

        if is_film_container:
            group_key = title if channel else ch + ": " + title
        elif topic and topic != title:
            group_key = topic if channel else ch + ": " + topic
        else:
            group_key = title if channel else ch + ": " + title

        _log("URL [%s] HD=%s SD=%s" % (ch, url_hd if url_hd else "-", url_sd if url_sd else "-"))
        try:
            ts = int(timestamp)
        except Exception:
            ts = 0

        items.append({
            "title":         _s(title),
            "group":         _s(group_key),
            "channel":       _s(ch),
            "stream_url_hd": _s(url_hd),
            "stream_url_sd": _s(url_sd),
            "description":   _s(desc),
            "duration":      _s(duration_str),
            "timestamp":     ts,
            "url_website":   _s(entry.get("url_website") or ""),
        })

    items = _dedupe_by_url(items)

    _log("MVW %d Sendungen verarbeitet" % len(items))
    return items, total_results, raw_count


# ------------------------------------------------------------------
# Topics-Endpunkt
# ------------------------------------------------------------------
def get_topics(channel=None):
    """
    Gibt alle Topics (Sendungsnamen) zurueck.
    Optional gefiltert nach Sender (channel="ARD" etc.).
    Liefert eine alphabetisch sortierte Liste von Unicode-Strings.
    """
    url = "https://mediathekviewweb.de/api/topics"
    if channel:
        url += "?channel=" + channel
    try:
        req = Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urlopen(req, timeout=15, context=_ssl_context) if _ssl_context else urlopen(req, timeout=15)
        data = json.loads(resp.read())
        topics = data.get("topics", [])
        _log("get_topics channel=%s -> %d Topics" % (channel, len(topics)))
        return topics
    except Exception as e:
        _log("get_topics Fehler: " + str(e))
        return []


# ------------------------------------------------------------------
# Sender-spezifische Funktionen
# ------------------------------------------------------------------
def get_ard_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ARD", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_zdf_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ZDF", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_arte_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ARTE", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_3sat_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("3Sat", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_ndr_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("NDR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_wdr_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("WDR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_br_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("BR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_mdr_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("MDR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_hr_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("HR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_swr_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("SWR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_rbb_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("RBB", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_sr_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("SR", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_zdfinfo_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ZDFinfo", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_zdfneo_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ZDFneo", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_kika_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("KiKA", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_phoenix_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("PHOENIX", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_radio_bremen_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("Radio Bremen TV", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_funk_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("Funk.net", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_ard_alpha_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ARD-alpha", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_one_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ONE", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_tagesschau24_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("tagesschau24", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_dw_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("DW", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_orf_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("ORF", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_srf_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query("SRF", size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)

def get_all_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query(channel=None, size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)


# ------------------------------------------------------------------
# Favoritenverwaltung
# Gespeichert als JSON: Liste von {"group": "...", "channel": "..."}
# ------------------------------------------------------------------
_SV_SN_NAMES = {">> Sendung verpasst?", ">> Demn\u00e4chst"}

def _load_favorites_raw():
    try:
        if os.path.exists(FAVORITES_FILE):
            with io.open(FAVORITES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Sondereintraege (SV/SN) bereinigen falls versehentlich gespeichert
                    cleaned = [e for e in data if e.get("group") not in _SV_SN_NAMES]
                    if len(cleaned) != len(data):
                        save_favorites(cleaned)
                    return cleaned
    except Exception:
        pass
    return []


def save_favorites(favorites_raw):
    """favorites_raw: Liste von {"group": unicode-str, "channel": unicode-str}"""
    try:
        with open(FAVORITES_FILE, "wb") as f:
            f.write(json.dumps(favorites_raw, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        _log("Favoriten speichern Fehler: " + str(e))


def reorder_favorites(group_bytes_list):
    """Speichert Favoriten in neuer Reihenfolge. group_bytes_list: Liste von group-Bytes."""
    favs_raw = _load_favorites_raw()
    name_to_fav = {}
    for f in favs_raw:
        name_to_fav[f.get("group", "")] = f
    reordered = []
    for gb in group_bytes_list:
        try:
            g = gb.decode("utf-8", "replace") if isinstance(gb, bytes) else gb
        except Exception:
            g = str(gb)
        if g in name_to_fav:
            reordered.append(name_to_fav[g])
    save_favorites(reordered)


def add_favorite(group_bytes, channel_bytes):
    """Fuegt eine Gruppe zu den Favoriten hinzu (Duplikate werden ignoriert)."""
    try:
        group   = group_bytes.decode("utf-8", "replace") if isinstance(group_bytes, bytes) else group_bytes
        channel = channel_bytes.decode("utf-8", "replace") if isinstance(channel_bytes, bytes) else channel_bytes
    except Exception:
        group   = str(group_bytes)
        channel = str(channel_bytes)

    favs = _load_favorites_raw()
    for f in favs:
        if f.get("group") == group:
            return  # bereits vorhanden
    favs.append({"group": group, "channel": channel})
    save_favorites(favs)
    _log("Favorit hinzugefuegt: " + group)


def remove_favorite(group_bytes):
    """Entfernt eine Gruppe aus den Favoriten."""
    try:
        group = group_bytes.decode("utf-8", "replace") if isinstance(group_bytes, bytes) else group_bytes
    except Exception:
        group = str(group_bytes)

    favs = _load_favorites_raw()
    favs = [f for f in favs if f.get("group") != group]
    save_favorites(favs)
    _log("Favorit entfernt: " + group)


def is_favorite(group_bytes):
    try:
        group = group_bytes.decode("utf-8", "replace") if isinstance(group_bytes, bytes) else group_bytes
    except Exception:
        group = str(group_bytes)
    return any(f.get("group") == group for f in _load_favorites_raw())


def _load_watched():
    try:
        if os.path.exists(WATCHED_FILE):
            with io.open(WATCHED_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
    except Exception:
        pass
    return set()


def _save_watched(watched_set):
    try:
        with open(WATCHED_FILE, "wb") as f:
            f.write(json.dumps(list(watched_set), ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        _log("Watched speichern Fehler: " + str(e))


def is_watched(url_bytes):
    try:
        url = url_bytes.decode("utf-8", "replace") if isinstance(url_bytes, bytes) else url_bytes
    except Exception:
        url = str(url_bytes)
    return url in _load_watched()


def toggle_watched(url_bytes):
    try:
        url = url_bytes.decode("utf-8", "replace") if isinstance(url_bytes, bytes) else url_bytes
    except Exception:
        url = str(url_bytes)
    watched = _load_watched()
    if url in watched:
        watched.discard(url)
        _log("Watched entfernt: " + url)
    else:
        watched.add(url)
        _log("Watched markiert: " + url)
    _save_watched(watched)


_episode_favorites_cache = None


def _load_episode_favorites():
    global _episode_favorites_cache
    if _episode_favorites_cache is not None:
        return _episode_favorites_cache
    try:
        if os.path.exists(EPISODE_FAVORITES_FILE):
            with io.open(EPISODE_FAVORITES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    _episode_favorites_cache = data
                    return _episode_favorites_cache
    except Exception:
        pass
    _episode_favorites_cache = []
    return _episode_favorites_cache


def _save_episode_favorites(items):
    global _episode_favorites_cache
    _episode_favorites_cache = items
    try:
        with open(EPISODE_FAVORITES_FILE, "wb") as f:
            f.write(json.dumps(items, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        _log("Episode-Favoriten speichern Fehler: " + str(e))


def _item_to_unicode(item):
    """Konvertiert alle Bytes-Werte eines Item-Dicts zu Unicode-Strings fuer JSON."""
    result = {}
    for k, v in item.items():
        if isinstance(v, bytes):
            result[k] = v.decode("utf-8", "replace")
        else:
            result[k] = v
    return result


def _item_to_bytes(item):
    """Konvertiert alle String-Werte eines Item-Dicts zurueck zu Bytes fuer Enigma2."""
    _STR_FIELDS = {"title", "group", "channel", "description", "duration",
                   "stream_url_hd", "stream_url_sd"}
    result = {}
    for k, v in item.items():
        if k in _STR_FIELDS and isinstance(v, str):
            try:
                result[k] = v.encode("utf-8")
            except Exception:
                result[k] = v
        else:
            result[k] = v
    return result


def is_episode_favorite(url_bytes):
    try:
        url = url_bytes.decode("utf-8", "replace") if isinstance(url_bytes, bytes) else url_bytes
    except Exception:
        url = str(url_bytes)
    return any(e.get("stream_url_hd") == url or e.get("stream_url_sd") == url
               for e in _load_episode_favorites())


def add_episode_favorite(item):
    """item: dict mit Bytes-Werten (wie aus der API)."""
    item_u = _item_to_unicode(item)
    url = item_u.get("stream_url_hd") or item_u.get("stream_url_sd") or ""
    if not url:
        return
    favs = _load_episode_favorites()
    if any(e.get("stream_url_hd") == url or e.get("stream_url_sd") == url for e in favs):
        return
    favs.insert(0, item_u)
    _save_episode_favorites(favs)
    _log("Episode-Favorit hinzugefuegt: " + url)


def remove_episode_favorite(url_bytes):
    try:
        url = url_bytes.decode("utf-8", "replace") if isinstance(url_bytes, bytes) else url_bytes
    except Exception:
        url = str(url_bytes)
    favs = _load_episode_favorites()
    favs = [e for e in favs if e.get("stream_url_hd") != url and e.get("stream_url_sd") != url]
    _save_episode_favorites(favs)
    _log("Episode-Favorit entfernt: " + url)


def get_episode_favorites():
    """Gibt alle Episode-Favoriten als Liste von Bytes-Item-Dicts zurueck."""
    return [_item_to_bytes(e) for e in _load_episode_favorites()]


def get_favorites(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    """
    Laedt alle Favoriten-Gruppen frisch aus der API.
    Nutzt die serverseitige Suche, um alle Folgen einer Serie zuverlaessig zu finden.
    """
    favs = _load_favorites_raw()
    if not favs:
        return [], 0, 0

    results = [None] * len(favs)
    _sem = threading.Semaphore(3)

    def _fetch_one(idx, fav):
        channel = fav.get("channel") or None
        group   = fav.get("group", "")

        # Den reinen Sendungsnamen extrahieren fuer die API Suche.
        # Nur entfernen wenn der Teil vor ": " wirklich ein bekannter Sendername ist —
        # sonst wuerde z.B. "Tatort: Borowski..." faelschlich zu "Borowski..." gekuerzt.
        pure_topic = group
        if channel and group.startswith(channel + ": "):
            pure_topic = group[len(channel)+2:]
        elif ": " in group:
            prefix = group.split(": ", 1)[0]
            if prefix in _KNOWN_CHANNELS:
                pure_topic = group.split(": ", 1)[1]

        # ZDF UHD Topics: Statische Liste verwenden statt MVW
        if channel == "ZDF UHD":
            items, _, _ = get_zdf_uhd_static_episodes(pure_topic)
            results[idx] = items
            return

        matched = []
        with _sem:
            try:
                # Hole gezielt bis zu 100 Folgen genau dieser Serie.
                # ["title", "topic"] statt nur ["topic"], damit Film-Favoriten gefunden
                # werden: bei Film-Containern (topic="Spielfilm" o.ae.) ist der
                # group_key der Filmtitel, der nur im title-Feld der API steht.
                # Das lokale Exakt-Filter (item_group_str == group) verhindert Fehlzuordnungen.
                items, _, _rc = _mvw_query(
                    channel=channel,
                    size=100,
                    offset=0,
                    search_term=pure_topic,
                    min_duration=min_duration,
                    sort_by=sort_by,
                    search_fields=["title", "topic"],
                )
                # Lokal auf die exakte Gruppe filtern, um unscharfen Beifang auszublenden.
                # Vergleich normalisiert: "BR: Schnittgut" gespeichert von "Alle" passt auch
                # auf group_key "Schnittgut" aus der channel-spezifischen Abfrage.
                for item in items:
                    item_group = item.get("group", b"")
                    try:
                        item_group_str = item_group.decode("utf-8", "replace")
                    except Exception:
                        item_group_str = str(item_group)
                    # Direkte Übereinstimmung
                    if item_group_str == group:
                        matched.append(item)
                        continue
                    # Fallback: gespeicherte Gruppe hat Sender-Prefix, item_group nicht
                    # z.B. group="BR: Schnittgut", item_group_str="Schnittgut"
                    if ": " in group:
                        group_suffix = group.split(": ", 1)[1]
                        if item_group_str == group_suffix:
                            # group-Feld auf den gespeicherten Namen normalisieren,
                            # damit is_favorite(gname) in der Favoriten-Ansicht korrekt matcht
                            item = dict(item)
                            item["group"] = group if isinstance(group, bytes) else group.encode("utf-8")
                            matched.append(item)
            except Exception as e:
                _log("Favorit laden Fehler (%s): %s" % (group, str(e)))
        results[idx] = matched

    threads = [threading.Thread(target=_fetch_one, args=(i, fav)) for i, fav in enumerate(favs)]
    for t in threads:
        t.daemon = True
        t.start()
    deadline = time.time() + 60
    for t in threads:
        remaining = deadline - time.time()
        if remaining > 0:
            t.join(timeout=remaining)

    all_items = []
    for r in results:
        if r:
            all_items.extend(r)

    # Kein Paging ueber alle Favoriten-Items — jede Gruppe hat bereits max. 100 Eintraege,
    # und die Gesamtzahl bleibt ueberschaubar. offset/size gelten nur fuer den API-Abruf
    # pro Gruppe (dort unveraendert), nicht fuer die zusammengefuehrte Ergebnisliste.
    return all_items, len(all_items), len(all_items)


# ------------------------------------------------------------------
# Suchverlauf
# Gespeichert als JSON: Liste von Unicode-Strings, neueste zuerst
# ------------------------------------------------------------------
def load_search_history():
    """Gibt die gespeicherte Suchliste zurueck (neueste zuerst), max. SEARCH_HISTORY_MAX Eintraege."""
    try:
        if os.path.exists(SEARCH_HISTORY_FILE):
            with io.open(SEARCH_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # In Python 2 liefert json.load unicode-Objekte — basestring deckt str+unicode ab
                    try:
                        str_types = (str, bytes, unicode)
                    except NameError:
                        str_types = (str, bytes)
                    return [e for e in data if isinstance(e, str_types) and e]
    except Exception:
        pass
    return []


def save_search_history(term):
    """Fuegt einen Suchbegriff vorne ein, entfernt Duplikate, kuerzt auf SEARCH_HISTORY_MAX."""
    try:
        if isinstance(term, bytes):
            term = term.decode("utf-8", "replace")
        term = term.strip()
        if not term:
            return
        history = load_search_history()
        history = [e for e in history if e != term]
        history.insert(0, term)
        history = history[:SEARCH_HISTORY_MAX]
        with open(SEARCH_HISTORY_FILE, "wb") as f:
            f.write(json.dumps(history, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        _log("Suchverlauf speichern Fehler: " + str(e))

# ---------------------------------------------------------------------------
# ZDF UHD – Sendungsliste via ZDF GraphQL-API + URL-Auflösung
# ---------------------------------------------------------------------------

def get_zdf_uhd_shows():
    """Liefert die aktuelle UHD-Sendungsliste vom ZDF (GraphQL-API)."""
    token_url = "https://zdf-prod-futura.zdf.de/mediathekV2/token"
    req = Request(token_url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urlopen(req, timeout=10, context=_ssl_context) if _ssl_context else urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    token = data["type"] + " " + data["token"]

    graphql_url = "https://api.zdf.de/graphql"
    query = ('{ metaCollectionContent(collectionId: "streaming_option-uhd"'
             ' input: { appId: "ffw-mt-web-32276a07" pagination: { first: 50 }'
             ' user: { abGroup: "gruppe-b", userSegment: "" } })'
             ' { smartCollections { title id canonical } } }')
    body = json.dumps({"query": query})
    if isinstance(body, str):
        body = body.encode("utf-8")
    req = Request(graphql_url, data=body, headers={
        "User-Agent":             "Mozilla/5.0",
        "Content-Type":           "application/json",
        "Api-Auth":               token,
        "Apollo-Require-Preflight": "True",
    })
    resp = urlopen(req, timeout=10, context=_ssl_context) if _ssl_context else urlopen(req, timeout=10)
    result = json.loads(resp.read().decode("utf-8"))
    return result.get("data", {}).get("metaCollectionContent", {}).get("smartCollections", [])


# ZDF HDR-Suffixe: 4K UHD (2160p) zuerst, dann Full-HD HDR (1080p)
_ZDF_HDR_SUFFIXES = ["_4692k_p72v16.mp4", "_2892k_p71v16.mp4"]


def uhd_url_candidate(url):
    """Gibt den 4K-Kandidaten zurück (nur Regex, kein Netzwerkzugriff). Für Icon-Checks."""
    if "akamaihd.net" not in url or "/zdf/" not in url:
        return url
    return _re.sub(r"_\d+k_p\d+v\d+\.mp4$", "_4692k_p72v16.mp4", url)


def uhd_url_candidates(url):
    """Gibt alle HDR-Kandidaten zurück (4K + 1080p HDR), ohne Netzwerkzugriff."""
    if "akamaihd.net" not in url or "/zdf/" not in url:
        return [url]
    return [_re.sub(r"_\d+k_p\d+v\d+\.mp4$", s, url) for s in _ZDF_HDR_SUFFIXES]


def resolve_uhd_url(url):
    """Prüft per HEAD-Request: 4K UHD zuerst, dann 1080p HDR; Fallback auf Original."""
    if "akamaihd.net" not in url or "/zdf/" not in url:
        return url
    for suffix in _ZDF_HDR_SUFFIXES:
        candidate = _re.sub(r"_\d+k_p\d+v\d+\.mp4$", suffix, url)
        if candidate == url:
            continue
        try:
            req = Request(candidate)
            req.get_method = lambda: "HEAD"
            resp = urlopen(req, timeout=2, context=_ssl_context) if _ssl_context else urlopen(req, timeout=2)
            if resp.getcode() == 200:
                return candidate
        except Exception:
            pass
    return url


def _find_uhd_streams_in(obj):
    """Sucht rekursiv nach MP4-Streams mit _p72v (4K UHD) oder _p71v (1080p HDR)."""
    result = []
    if isinstance(obj, dict):
        url_val = obj.get("url")
        if obj.get("mimeType") == "video/mp4" and url_val and isinstance(url_val, (bytes, str, type(u""))):
            result.append(url_val)
        for v in obj.values():
            result.extend(_find_uhd_streams_in(v))
    elif isinstance(obj, list):
        for item in obj:
            result.extend(_find_uhd_streams_in(item))
    return result


def resolve_uhd_url_via_document_api(url_website, with_meta=False):
    """ZDF Document API → exakte _p72v (4K) / _p71v (1080p HDR) URL. Gibt None bei Fehler.
    Mit with_meta=True wird stattdessen (url, season, episode) zurueckgegeben -
    season/episode sind None, falls ZDF sie fuer diese Sendung nicht pflegt
    (nur bei manchen Serien wie "Die Bergretter" vorhanden, siehe [[project_zdf_uhd]])."""
    empty = (None, None, None) if with_meta else None
    if not url_website:
        return empty
    ws = url_website if isinstance(url_website, str) else url_website.decode("utf-8", "replace")
    canonical = ws.rstrip("/").split("/")[-1]
    if not canonical:
        return empty
    api_url = "https://zdf-prod-futura.zdf.de/mediathekV2/document/" + canonical
    try:
        req = Request(api_url)
        resp = urlopen(req, timeout=5, context=_ssl_context) if _ssl_context else urlopen(req, timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
        streams = _find_uhd_streams_in(data)
        uhd = [u for u in streams if "_p72v" in u or "_p71v" in u]
        if not uhd:
            return empty
        main = [u for u in uhd if "_a1a2_" in u]
        url = main[0] if main else uhd[0]
        if not with_meta:
            return url
        doc = data.get("document", {}) if isinstance(data, dict) else {}
        season = episode = None
        try:
            if doc.get("seasonNumber") is not None:
                season = int(doc.get("seasonNumber"))
            if doc.get("episodeNumber") is not None:
                episode = int(doc.get("episodeNumber"))
        except (TypeError, ValueError):
            season = episode = None
        return url, season, episode
    except Exception:
        return empty


def get_zdf_uhd_topic_episodes(topic, offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    """Episoden einer bestimmten ZDF-UHD-Sendung (topic-gefiltert).
    Fallback auf Titelsuche für Einzelfilme (topic='Filme' in mediathekviewweb)."""
    sf = ["title"] if search_term else None
    items, total, raw = _mvw_query("ZDF", size, offset, search_term, min_duration, sort_by,
                                   search_fields=sf, topic_filter=topic)
    if not items and not search_term:
        items, total, raw = _mvw_query("ZDF", size, offset, topic, min_duration, sort_by,
                                       search_fields=["title"])
    return items, total, raw


# ---------------------------------------------------------------------------
# ZDF UHD – statische, verifizierte Episodenliste
# ---------------------------------------------------------------------------

_UHD_STATIC_DATA = None
_UHD_STATIC_PATH = os.path.join(os.path.dirname(__file__), "zdf_uhd_static.json")
_UHD_EXTRA_THEMEN = ["Die Bergretter"]


def _load_uhd_static():
    global _UHD_STATIC_DATA
    if _UHD_STATIC_DATA is None:
        try:
            with io.open(_UHD_STATIC_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            # Neues Format: {"episodes": [...], "no_hdr_topics": [...]}
            if isinstance(raw, dict):
                _UHD_STATIC_DATA = raw
            else:
                _UHD_STATIC_DATA = {"episodes": raw, "no_hdr_topics": []}
        except Exception:
            _UHD_STATIC_DATA = {"episodes": [], "no_hdr_topics": []}
    return _UHD_STATIC_DATA


def get_zdf_uhd_static_topics():
    """Gibt die eindeutigen Serientitel aus der statischen UHD-Liste zurück (geordnet nach erstem Auftreten)."""
    seen = []
    for entry in _load_uhd_static()["episodes"]:
        t = entry.get("topic", "")
        if t and t not in seen:
            seen.append(t)
    return seen


def get_zdf_uhd_no_hdr_topics():
    """Gibt Topics zurück, die geprüft wurden und kein HDR-Stream haben — zum Filtern der GraphQL-Liste."""
    return _load_uhd_static().get("no_hdr_topics", [])


def _match_static_topic(topic):
    """Findet den exakten Topic-Namen in der statischen Liste - direkt, oder
    per Prefix-Fallback wenn der GraphQL-Titel kürzer ist als der statische
    Topic-Name (z.B. GraphQL "Schatzinseln im Pazifik" vs. statisch
    "Schatzinseln im Pazifik – Leben mit dem Ozean"). None wenn das Topic
    (noch) nicht in der statischen Liste steht."""
    all_topics = get_zdf_uhd_static_topics()
    if topic in all_topics:
        return topic
    t_lower = topic.lower()
    for st in all_topics:
        if st.lower().startswith(t_lower + " ") or st.lower().startswith(t_lower + u" –") or st.lower().startswith(t_lower + u" -"):
            return st
    return None


def get_zdf_uhd_topic_quality(topic):
    """Gibt '4K HDR'/'1080p HDR' für ein Topic aus der statischen Liste
    zurück, rein lokal ohne MVW-Nachladung (im Unterschied zu
    get_zdf_uhd_static_episodes(), das zusätzlich Beschreibung/Laufzeit
    per MVW nachlädt - für die reine Qualitätsanzeige in der Sendungsliste
    unnötig und zu langsam bei vielen Sendungen). Laut Stichprobe hat jede
    Sendung durchgehend nur eine der beiden Qualitäten, nie gemischt -
    trotzdem gewinnt defensiv der erste Treffer. Leerer String wenn das
    Topic noch nicht in der statischen Liste steht."""
    matched_topic = _match_static_topic(topic)
    if not matched_topic:
        return ""
    for entry in _load_uhd_static()["episodes"]:
        if entry.get("topic", "") != matched_topic:
            continue
        url = entry.get("uhd_url", "")
        if "_p72v" in url:
            return "4K HDR"
        if "_p71v" in url:
            return "1080p HDR"
    return ""


def get_zdf_uhd_static_episodes(topic, search_term=None):
    """Gibt Episoden für ein Topic aus der statischen Liste im mediathek.py-internen Format zurück."""
    results = []
    all_episodes = _load_uhd_static()["episodes"]

    matched_topic = _match_static_topic(topic) or topic
    exact_entries = [e for e in all_episodes if e.get("topic", "") == matched_topic]

    for entry in exact_entries:
        if entry.get("topic", "") != matched_topic:
            continue
        title = entry.get("title", "")
        if search_term:
            st = search_term if isinstance(search_term, str) else search_term.decode("utf-8", "replace")
            if st.lower() not in title.lower():
                continue
        uhd_url = entry.get("uhd_url", "")
        ts = entry.get("timestamp", 0)
        try:
            ts = int(ts)
        except Exception:
            ts = 0
        quality = "4K HDR" if "_p72v" in uhd_url else "1080p HDR" if "_p71v" in uhd_url else ""
        results.append({
            "title":         _s(title),
            "group":         _s(topic),
            "channel":       _s("ZDF UHD"),
            "stream_url_hd": _s(uhd_url),
            "stream_url_sd": _s(""),
            "description":   _s(""),
            "duration":      _s(""),
            "quality":       _s(quality),
            "timestamp":     ts,
            "url_website":   _s(entry.get("web_url") or ""),
            "season":        entry.get("season"),
            "episode":       entry.get("episode"),
        })
    results.sort(key=lambda x: x["timestamp"], reverse=True)

    # Beschreibung und Laufzeit aus MediathekViewWeb nachladen
    if results:
        try:
            mvw_items, _, _ = _mvw_query(channel="ZDF", size=100, topic_filter=topic)
            if not mvw_items:
                mvw_items, _, _ = _mvw_query("ZDF", 100, 0, topic, 0, "timestamp",
                                             search_fields=["title"])
            if not mvw_items:
                # Langer Topic-Name wie "Schatzinseln im Pazifik – Leben mit dem Ozean"
                # → nur Präfix bis zum ersten " – " / " - " verwenden
                for sep in (u" – ", u" - "):
                    if sep in topic:
                        short = topic.split(sep)[0].strip()
                        mvw_items, _, _ = _mvw_query("ZDF", 100, 0, short, 0, "timestamp",
                                                     search_fields=["title"])
                        if mvw_items:
                            break
            mvw_titles = []
            for item in mvw_items:
                t = item.get("title", b"")
                if isinstance(t, bytes):
                    t = t.decode("utf-8", "replace")
                mvw_titles.append((t.lower(), item))
            for ep in results:
                ep_title = ep["title"]
                if isinstance(ep_title, bytes):
                    ep_title = ep_title.decode("utf-8", "replace")
                ep_lower = ep_title.strip().lower()
                mvw = None
                for mvw_lower, item in mvw_titles:
                    if mvw_lower == ep_lower or mvw_lower.startswith(ep_lower + " ") or ep_lower.startswith(mvw_lower + " "):
                        mvw = item
                        break
                # Kein Folgen-Match: ersten MVW-Eintrag als Topic-Beschreibung verwenden
                # Laufzeit dabei NICHT übernehmen (kann Teaser-Länge sein)
                if mvw is None and mvw_titles:
                    desc = mvw_titles[0][1].get("description", b"")
                    if desc:
                        ep["description"] = desc
                elif mvw:
                    desc = mvw.get("description", b"")
                    if desc:
                        ep["description"] = desc
                    dur = mvw.get("duration", b"")
                    if dur:
                        ep["duration"] = dur
        except Exception:
            pass

    return results, len(results), len(results)


def refresh_uhd_static():
    """Aktualisiert zdf_uhd_static.json per ZDF GraphQL + Document API + HEAD-Check.
    Gibt (anzahl_episoden, fehler_string_oder_None) zurück."""
    import io as _io2
    import threading as _thr

    old_count = len(_load_uhd_static().get("episodes", []))

    try:
        # Token
        token_url = "https://zdf-prod-futura.zdf.de/mediathekV2/token"
        req = Request(token_url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urlopen(req, timeout=10, context=_ssl_context) if _ssl_context else urlopen(req, timeout=10)
        tok_data = json.loads(resp.read().decode("utf-8"))
        token = tok_data["type"] + " " + tok_data["token"]
        _log("refresh_uhd_static: Token erhalten")

        # GraphQL: UHD-Kollektion mit Canonicals
        graphql_url = "https://api.zdf.de/graphql"
        gql = ('{ metaCollectionContent(collectionId: "streaming_option-uhd"'
               ' input: { appId: "ffw-mt-web-32276a07" pagination: { first: 50 }'
               ' user: { abGroup: "gruppe-b", userSegment: "" } })'
               ' { smartCollections { title __typename'
               ' ... on ISeriesSmartCollection { episodes { nodes { title canonical } } }'
               ' ... on MovieSmartCollection { video { title canonical } } } } }')
        body = json.dumps({"query": gql})
        if isinstance(body, str):
            body = body.encode("utf-8")
        req = Request(graphql_url, data=body, headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Api-Auth": token,
            "Apollo-Require-Preflight": "True",
        })
        resp = urlopen(req, timeout=10, context=_ssl_context) if _ssl_context else urlopen(req, timeout=10)
        raw_graphql = resp.read().decode("utf-8")
        cols = json.loads(raw_graphql).get("data", {}).get("metaCollectionContent", {}).get("smartCollections", [])
        _log("refresh_uhd_static: GraphQL lieferte %d Collections" % len(cols))

        episodes = []
        graphql_topics = set()
        for sc in cols:
            topic = sc.get("title", "")
            graphql_topics.add(topic)
            if sc.get("__typename") == "MovieSmartCollection":
                v = sc.get("video") or {}
                if v.get("canonical"):
                    episodes.append({"topic": topic, "title": v.get("title") or topic, "canonical": v["canonical"]})
            else:
                for node in sc.get("episodes", {}).get("nodes", []):
                    if node.get("canonical") and "audiodeskription" not in node.get("title", "").lower():
                        episodes.append({"topic": topic, "title": node.get("title", ""), "canonical": node["canonical"]})
        _log("refresh_uhd_static: %d Episoden aus GraphQL (%d Topics), frage Document API ab" % (len(episodes), len(graphql_topics)))

        # Document API parallel per Threads
        verified = [None] * len(episodes)
        _doc_api_fail_count = [0]
        def _resolve(args):
            i, ep = args
            url, season, episode = resolve_uhd_url_via_document_api(ep["canonical"], with_meta=True)
            if url:
                verified[i] = {"topic": ep["topic"], "title": ep["title"],
                               "web_url": "", "uhd_url": url, "timestamp": 0,
                               "season": season, "episode": episode}
            else:
                _doc_api_fail_count[0] += 1
        threads = []
        for i, ep in enumerate(episodes):
            t = _thr.Thread(target=_resolve, args=((i, ep),))
            t.daemon = True
            threads.append(t)
        for batch_start in range(0, len(threads), 20):
            batch = threads[batch_start:batch_start + 20]
            for t in batch:
                t.start()
            for t in batch:
                t.join()
        verified = [v for v in verified if v]
        _log("refresh_uhd_static: Document API: %d verifiziert, %d ohne UHD/HDR-Stream oder Fehler" %
             (len(verified), _doc_api_fail_count[0]))

        # Phase 2: EXTRA_THEMEN per MVW + HEAD-Check
        _HDR_SUFFIXES = ["_4692k_p72v16.mp4", "_2892k_p71v16.mp4"]
        extra = [t for t in _UHD_EXTRA_THEMEN if t not in graphql_topics]
        for topic in extra:
            mvw_items, _, _ = _mvw_query(channel="ZDF", topic_filter=topic, size=100)
            extra_candidates = []
            seen_urls = set()
            for item in mvw_items:
                url_hd = item.get("stream_url_hd", b"")
                if isinstance(url_hd, bytes):
                    url_hd = url_hd.decode("utf-8", "replace")
                if not url_hd or "akamaihd.net" not in url_hd or url_hd in seen_urls:
                    continue
                seen_urls.add(url_hd)
                title_b = item.get("title", b"")
                title_s = title_b.decode("utf-8", "replace") if isinstance(title_b, bytes) else title_b
                if "audiodeskription" in title_s.lower():
                    continue
                extra_candidates.append((topic, title_s, url_hd, item.get("timestamp", 0)))

            def _head_check(args):
                topic2, title2, url2, ts2 = args
                for suffix in _HDR_SUFFIXES:
                    candidate = _re.sub(r"_\d+k_p\d+v\d+\.mp4$", suffix, url2)
                    if candidate == url2:
                        continue
                    try:
                        req2 = Request(candidate)
                        req2.get_method = lambda: "HEAD"
                        r2 = urlopen(req2, timeout=2, context=_ssl_context) if _ssl_context else urlopen(req2, timeout=2)
                        if r2.getcode() == 200:
                            return {"topic": topic2, "title": title2, "web_url": "", "uhd_url": candidate, "timestamp": ts2,
                                    "season": None, "episode": None}
                    except Exception:
                        pass
                return None

            hc_results = [None] * len(extra_candidates)
            def _hc_worker(args):
                idx2, cand = args
                hc_results[idx2] = _head_check(cand)
            hc_threads = []
            for i, cand in enumerate(extra_candidates):
                t = _thr.Thread(target=_hc_worker, args=((i, cand),))
                t.daemon = True
                hc_threads.append(t)
            for batch_start in range(0, len(hc_threads), 15):
                batch = hc_threads[batch_start:batch_start + 15]
                for t in batch:
                    t.start()
                for t in batch:
                    t.join()
            verified.extend(v for v in hc_results if v)
        _log("refresh_uhd_static: nach Phase 2 (%d Extra-Themen): %d Episoden gesamt" % (len(extra), len(verified)))

        # no_hdr_topics + speichern
        topics_found = set(item["topic"] for item in verified)
        no_hdr_topics = sorted(graphql_topics - topics_found)
        verified.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

        # Schutz vor stillem Datenverlust: wenn z.B. die Document-API-Aufrufe
        # aus irgendeinem Grund auf der Box durchgehend fehlschlagen (jeder
        # einzelne Fehler wird oben in resolve_uhd_url_via_document_api()
        # lautlos verschluckt), liefert Phase 1 nichts und es bleiben nur die
        # Phase-2-Fallback-Themen (_UHD_EXTRA_THEMEN, z.B. "Die Bergretter")
        # uebrig - eine stark geschrumpfte Liste wuerde dann klaglos die gute
        # alte Datei ueberschreiben. Bei drastischem Rueckgang stattdessen
        # abbrechen, ohne zu speichern.
        if old_count >= 10 and len(verified) < old_count * 0.5:
            _log("refresh_uhd_static: ABBRUCH - nur %d Episoden (vorher %d), Datei NICHT ueberschrieben" %
                 (len(verified), old_count))
            return 0, "Nur %d von vorher %d Episoden gefunden - ZDF-API vermutlich gestoert" % (len(verified), old_count)

        # Python 2: alle Strings zu unicode normalisieren, sonst schlägt json.dump fehl
        def _to_u(obj):
            if isinstance(obj, bytes):
                return obj.decode("utf-8", "replace")
            elif isinstance(obj, dict):
                return {_to_u(k): _to_u(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_to_u(i) for i in obj]
            return obj

        output = _to_u({"episodes": verified, "no_hdr_topics": no_hdr_topics})
        json_bytes = json.dumps(output, indent=2, ensure_ascii=False)
        if not isinstance(json_bytes, bytes):
            json_bytes = json_bytes.encode("utf-8")
        with open(_UHD_STATIC_PATH, "wb") as f:
            f.write(json_bytes)

        global _UHD_STATIC_DATA
        _UHD_STATIC_DATA = None
        _log("refresh_uhd_static: %d Episoden gespeichert" % len(verified))
        return len(verified), None
    except Exception as e:
        _log("refresh_uhd_static: Fehler - " + str(e))
        return 0, str(e)
