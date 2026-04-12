# -*- coding: utf-8 -*-
# mediathek.py
# Holt Sendungslisten über MediathekViewWeb-API (aggregiert alle ÖR-Sender)

import json
import os

LOG_FILE       = "/tmp/oemediathek.log"
FAVORITES_FILE = "/etc/enigma2/oemediathek_favorites.json"
DEBUG          = False

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
    if not DEBUG:
        return
    line = "[OeMediathek] " + str(msg)
    print(line)
    try:
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


# ------------------------------------------------------------------
# MediathekViewWeb-API
# POST https://mediathekviewweb.de/api/query
# ------------------------------------------------------------------
def _mvw_query(channel=None, size=100, offset=0, search_term=None, min_duration=0, sort_by="timestamp", search_fields=None):
    """
    Fragt die MediathekViewWeb-API ab.
    search_fields: Liste der Felder fuer die Suche, Standard ["title", "topic"]
    sort_by: "timestamp" | "duration" (API-seitig); "az" wird clientseitig behandelt
    """
    url = "https://mediathekviewweb.de/api/query"

    queries = []
    if channel:
        queries.append({"fields": ["channel"], "query": channel})

    if search_term:
        fields = search_fields if search_fields else ["title", "topic"]
        queries.append({"fields": fields, "query": search_term})

    api_sort = sort_by if sort_by in ("timestamp", "duration") else "timestamp"

    body_dict = {
        "queries":   queries,
        "sortBy":    api_sort,
        "sortOrder": "desc",
        "future":    False,
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
    _log("MVW %d Ergebnisse (gesamt: %d)" % (len(results_raw), total_results))

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
        
        # HD und SD getrennt auslesen
        url_hd     = entry.get("url_video_hd") or ""
        url_sd     = entry.get("url_video") or ""
        
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

        # Audiodeskriptions-Fassungen ausblenden
        if title.endswith("(Audiodeskription)"):
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
        })

    _log("MVW %d Sendungen verarbeitet" % len(items))
    return items, total_results


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

def get_all_highlights(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    return _mvw_query(channel=None, size=size, offset=offset, search_term=search_term, min_duration=min_duration, sort_by=sort_by)


# ------------------------------------------------------------------
# Favoritenverwaltung
# Gespeichert als JSON: Liste von {"group": "...", "channel": "..."}
# ------------------------------------------------------------------
def _load_favorites_raw():
    try:
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


def save_favorites(favorites_raw):
    """favorites_raw: Liste von {"group": unicode-str, "channel": unicode-str}"""
    try:
        with open(FAVORITES_FILE, "w") as f:
            json.dump(favorites_raw, f, ensure_ascii=False)
    except Exception as e:
        _log("Favoriten speichern Fehler: " + str(e))


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


def get_favorites(offset=0, size=100, search_term=None, min_duration=0, sort_by="timestamp"):
    """
    Laedt alle Favoriten-Gruppen frisch aus der API.
    Nutzt die serverseitige Suche, um alle Folgen einer Serie zuverlaessig zu finden.
    """
    favs = _load_favorites_raw()
    if not favs:
        return [], 0

    all_items = []
    for fav in favs:
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

        try:
            # Hole gezielt bis zu 100 Folgen genau dieser Serie
            items, _ = _mvw_query(
                channel=channel,
                size=100,
                offset=0,
                search_term=pure_topic,
                min_duration=min_duration,
                sort_by=sort_by,
            )
            # Lokal auf die exakte Gruppe filtern, um unscharfen Beifang auszublenden
            for item in items:
                item_group = item.get("group", b"")
                try:
                    item_group_str = item_group.decode("utf-8", "replace")
                except Exception:
                    item_group_str = str(item_group)
                if item_group_str == group:
                    all_items.append(item)
        except Exception as e:
            _log("Favorit laden Fehler (%s): %s" % (group, str(e)))

    # Wichtig: Paging (offset und size) fuer die gesammelte Gesamtliste anwenden
    total_count = len(all_items)
    sliced_items = all_items[offset : offset + size]

    return sliced_items, total_count