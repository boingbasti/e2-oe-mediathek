# Generiert die Python-3-Variante des Plugins (fuer OpenATV u.ae.) aus der
# echten (Python-2/VTi-)Quelle unter OeMediathek/, komplett per Patches auf
# eine Kopie angewendet. Schreibt nur nach py3_build/ (gitignored, wird bei
# jedem Lauf frisch erzeugt), ruehrt OeMediathek/ selbst nicht an. Wird vom
# --py3-Flag in build_ipk.py sowie der GitHub Action bei Release-Erstellung
# genutzt.

import os
import re
import shutil

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "OeMediathek")
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py3_build", "OeMediathek")

_B_OLD = '''def _b(val):
    """Gibt val als Byte-String zurück (Python 2 / Enigma2)."""
    if isinstance(val, bytes):
        return val
    try:
        return val.encode("utf-8")
    except Exception:
        return str(val)'''

_B_NEW = '''def _b(val):
    """Gibt val als nativen Text-String zurück (Python 3)."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    return str(val)'''

# download_manager.py hat eine EIGENE, von plugin.py._b() unabhaengige
# Kopie dieses Helpers (andere Signatur: s statt val, kein Docstring) - leicht
# zu uebersehen: Download-Manager-Screen bleibt sonst komplett leer ohne
# jede Fehlermeldung im Log, weil Label(_b("...")) dort bytes an
# eLabel.setText() gibt (Python-3-SWIG-Binding erwartet str, siehe _B_NEW),
# was lautlos zu keinem gerenderten Text fuehrt statt einer Exception.
_DM_B_OLD = '''def _b(s):
    if isinstance(s, bytes):
        return s
    return s.encode("utf-8", "replace")'''

_DM_B_NEW = '''def _b(s):
    if s is None:
        return ""
    if isinstance(s, bytes):
        return s.decode("utf-8", "replace")
    return str(s)'''

_S_OLD = '''def _s(val):
    """Unicode-String zu UTF-8 Byte-String für Python 2 / Enigma2."""
    if isinstance(val, bytes):
        return val
    try:
        return val.encode('utf-8')
    except Exception:
        return str(val)'''

_S_NEW = '''def _s(val):
    """Gibt val als nativen Text-String zurück (Python 3)."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    return str(val)'''


def _patch(path, old, new):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if old not in content:
        raise SystemExit("Erwarteter Original-Block nicht gefunden in %s - Quelle hat sich geaendert, Skript pruefen." % path)
    content = content.replace(old, new, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# Implizite Top-Level-Imports der Geschwister-Module -> explizite relative
# Imports. Unter Python 2/VTi funktionierte "from mediathek import X", weil
# Enigma2s Plugin-Loader den Plugin-Ordner selbst in sys.path haengt. Unter
# Python 3 (OpenATV) schlaegt das mit "No module named 'mediathek'" fehl.
# oe-alliance-plugins loest das exakt so (bestaetigt per Diff-Vergleich ihrer
# plugin.py).
_RELATIVE_IMPORT_FIXES = {
    "download_manager.py": [
        ("from downloader import format_size", "from .downloader import format_size"),
    ],
    "mediathek.py": [
        ("from downloader import get_debug_logging as _get_debug_logging",
         "from .downloader import get_debug_logging as _get_debug_logging"),
    ],
    "player.py": [
        ("from downloader import get_debug_logging, get_force_exteplayer, get_live_tv_background, load_settings, save_settings",
         "from .downloader import get_debug_logging, get_force_exteplayer, get_live_tv_background, load_settings, save_settings"),
    ],
    "plugin.py": [
        ("from mediathek import (", "from .mediathek import ("),
        ("from player import play_stream_async, black_background_ref, _self_heal_all_serviceapp_backups",
         "from .player import play_stream_async, black_background_ref, _self_heal_all_serviceapp_backups"),
        ("from downloader import Downloader, get_save_dir, set_save_dir, get_content_length, format_size, get_auto_convert, set_auto_convert, convert_mp4_to_ts, get_tile_wrap_lr, set_tile_wrap_lr, get_serviceapp_autoconfigure, set_serviceapp_autoconfigure, get_debug_logging, set_debug_logging, get_force_exteplayer, set_force_exteplayer, get_download_quality, set_download_quality, get_download_quality_label, get_stream_quality, set_stream_quality, get_stream_quality_label, get_download_extra_info, set_download_extra_info, get_download_extra_info_label, get_live_tv_background, set_live_tv_background",
         "from .downloader import Downloader, get_save_dir, set_save_dir, get_content_length, format_size, get_auto_convert, set_auto_convert, convert_mp4_to_ts, get_tile_wrap_lr, set_tile_wrap_lr, get_serviceapp_autoconfigure, set_serviceapp_autoconfigure, get_debug_logging, set_debug_logging, get_force_exteplayer, set_force_exteplayer, get_download_quality, set_download_quality, get_download_quality_label, get_stream_quality, set_stream_quality, get_stream_quality_label, get_download_extra_info, set_download_extra_info, get_download_extra_info_label, get_live_tv_background, set_live_tv_background"),
        ("from download_manager import OeMediathekDownloadManagerScreen", "from .download_manager import OeMediathekDownloadManagerScreen"),
    ],
}


# Der Code nutzt an sehr vielen Stellen (218x ueber alle Module) \xNN\xNN-
# Byte-Escapes fuer Umlaute in normalen String-Literalen, z.B. "\xc3\x96R
# Mediathek" fuer "ÖR Mediathek". Unter Python 2 sind das rohe UTF-8-Bytes
# in einem Byte-String - korrekt. Unter Python 3 werden dieselben \xNN-
# Escapes stattdessen als einzelne Unicode-Codepoints interpretiert, nicht
# als UTF-8-Bytes -> Mojibake ("Ã–R Mediathek").
# Fix: jede Sequenz aufeinanderfolgender \xNN-Escapes als UTF-8-Bytes lesen
# und durch das echte Zeichen ersetzen. Nicht-UTF8-Sequenzen (kommen in
# diesem Code nicht vor, siehe Stichprobenpruefung) bleiben unangetastet.
_HEX_ESCAPE_RUN = re.compile(r"(?:\\x[0-9a-fA-F]{2})+")
# Ganze Single-/Double-Quote-String-Literale inkl. optionalem Prefix (b/B/u/U/r/R).
# Triple-quoted Docstrings nutzen in diesem Code durchgehend echte UTF-8-
# Zeichen statt \xNN-Escapes (Stichprobe bestaetigt das), werden hier bewusst
# nicht erfasst - kein Fixbedarf dort.
# Triple-Quote-Varianten MUESSEN vor den Single-Quote-Alternativen stehen und
# non-greedy (*?) bis zum naechsten schliessenden """/''' matchen. Ohne das
# haelt die Single-Quote-Alternative das oeffnende """ faelschlich fuer zwei
# leere Strings ("" + Rest-Quote startet einen neuen, unbegrenzten Match) und
# verschluckt den kompletten Docstring-Koerper als ein einziges Fake-Literal:
# dadurch wurde ein echtes b"..."-Byte-Literal direkt nach einem Docstring in
# player.py nicht mehr als eigenstaendiger Treffer erkannt und blieb
# faelschlich unveraendert (siehe _strip_byte_prefix).
#
# Die eigene comment-Gruppe ist ebenso zwingend: ein einzelnes Apostroph in
# einem #-Kommentar (z.B. "# ...thread's results...", player.py) wird sonst
# als oeffnendes '...'-Literal gelesen und verschluckt als Fake-Match ALLES
# bis zum naechsten zufaelligen Anfuehrungszeichen: das hat mehrere hundert
# Zeilen Code inkl. eines b"(Offline)"-Literals unsichtbar gemacht, weit
# ueber die Kommentarzeile hinaus. Kommentare muessen daher als eigene,
# unangetastete Alternative VOR den String-Alternativen erkannt werden.
_STRING_LITERAL = re.compile(
    r"(?P<comment>#[^\n]*)"
    r"|(?P<prefix>[a-zA-Z]?)(?P<str>'''.*?'''|\"\"\".*?\"\"\"|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")",
    re.DOTALL,
)


def _hex_repl(m):
    hex_bytes = re.findall(r"\\x([0-9a-fA-F]{2})", m.group(0))
    raw = bytes(int(h, 16) for h in hex_bytes)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return m.group(0)


def _fix_byte_escapes(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    def literal_repl(m):
        if m.group("comment") is not None:
            return m.group(0)
        s = m.group("str")
        if s.startswith(("'''", '"""')):
            # Docstring: nutzt bereits echte UTF-8-Zeichen, kein Fixbedarf,
            # nicht anfassen (siehe Kommentar an _STRING_LITERAL).
            return m.group(0)
        prefix = m.group("prefix")
        if prefix.lower() == "b":
            # Byte-Literal: \xNN bedeutet in Py2 UND Py3 dasselbe (roh-Byte),
            # nicht anfassen.
            return m.group(0)
        return prefix + _HEX_ESCAPE_RUN.sub(_hex_repl, s)

    content = _STRING_LITERAL.sub(literal_repl, content)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# Nach dem _b()/_s()-Fix liefern beide Helfer durchgehend str statt bytes.
# Reine b"..."/b'...'-Literale (ohne \xNN, sonst schon durch die Mojibake-
# Korrektur oben erfasst) werden dadurch inkonsistent, sobald sie mit einem
# str per + verkettet werden - z.B. "self.source_name + b\" | \" + group_str"
# -> TypeError: can only concatenate str (not "bytes") to str. Stichprobe
# aller verbleibenden b"..."-Literale (83x) zeigt
# ausschliesslich Text (Wochentage, Labels, Trennzeichen) - keine echten
# Binaerdaten. Fix: b-Prefix bei allen einfachen (nicht triple-quoted)
# Byte-Literalen entfernen. \b verhindert Treffer in rb"..."/br"...".
def _strip_byte_prefix(path):
    """Nutzt bewusst dieselbe _STRING_LITERAL-Tokenisierung wie
    _fix_byte_escapes() statt eines eigenen b"..."-Regex: ein naives, blind
    ueber den Rohtext scannendes Muster (\\bb["']...) traf faelschlich auch
    auf ein 'b' am Wortende direkt vor einem FREMDEN Anfuehrungszeichen
    innerhalb eines anderen String-Literals - konkret in mediathek.py:
    'abGroup: "gruppe-b", userSegment: ""' (Teil eines einfach gequoteten
    GraphQL-Query-Strings) wurde zu "gruppe-", das 'b' lautlos verschluckt.
    Ursache erst gefunden, nachdem die ZDF-GraphQL-Abfrage unter Python 3
    deterministisch 0 statt 26 Collections lieferte - der veraenderte
    abGroup-Wert landete in einer nicht existenten ZDF-A/B-Testgruppe.
    _STRING_LITERAL matcht dagegen
    immer das VOLLSTAENDIGE aeussere Literal zuerst (Alternierung auf '...'
    vs "..." ab der öffnenden Anfuehrung), respektiert also echte
    Literalgrenzen statt beliebiger b"-Fundstellen irgendwo im Text.

    Entfernt das b-Praefix NIE ohne die \\xNN-Escapes im Inhalt zu dekodieren:
    _fix_byte_escapes() laesst echte Byte-Literale bewusst unangetastet (dort
    bedeutet \\xNN roh-Byte), sobald hier aber nur das 'b' gestrichen wird,
    ist es ein GANZ NORMALES String-Literal, in dem \\xNN unter Python 3 als
    einzelner Unicode-Codepoint gelesen wird, nicht als UTF-8-Byte -> Mojibake
    ("gruppe-b" waere hier kein Beispiel, aber b">> Demn\\xc3\\xa4chst" in
    plugin.py wurde so zu "DemnÃ¤chst" in der ARD-Themenliste. Deshalb hier
    dieselbe _hex_repl-Dekodierung wie in _fix_byte_escapes() anwenden,
    nicht nur das 'b' wegschneiden."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    def literal_repl(m):
        if m.group("comment") is not None:
            return m.group(0)
        s = m.group("str")
        if s.startswith(("'''", '"""')):
            return m.group(0)
        prefix = m.group("prefix")
        if prefix.lower() == "b":
            return _HEX_ESCAPE_RUN.sub(_hex_repl, s)
        return m.group(0)

    content = _STRING_LITERAL.sub(literal_repl, content)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# _apply() in play_stream_async() faengt Exceptions aus play_resolved_stream()
# nicht ab, die verschwinden dadurch im Twisted-Reactor ohne jede sichtbare
# Spur (weder Crash noch eigenes Log) - Symptom: Qualitaetsauswahl-Fenster
# schliesst sich, Player oeffnet nie, kein Fehler in oemediathek.log. Fuer den
# Python-3-Port bewusst mit Logging ergaenzt, damit ein Fehler an dieser
# Stelle sichtbar wird statt lautlos zu verschwinden.
_APPLY_OLD = '''            def _apply():
                global _active_play_thread_running
                _active_play_thread_running = False
                play_resolved_stream(session, stream_url_bytes, title_bytes, player_id, streams, stream_index, autoconfigure_serviceapp)'''

_APPLY_NEW = '''            def _apply():
                global _active_play_thread_running
                _active_play_thread_running = False
                try:
                    play_resolved_stream(session, stream_url_bytes, title_bytes, player_id, streams, stream_index, autoconfigure_serviceapp)
                except Exception:
                    _log("play_stream_async: Fehler bei play_resolved_stream (DIAGNOSE-PATCH): " + _fmt_exc())'''


# Der eigentliche Wiedergabe-Blocker: eServiceReference() ist ein SWIG-
# Binding, das unter Python 3 fuer den Pfad-Parameter (std::string const&)
# echten str erwartet (SWIG konvertiert das selbst per UTF-8 zu std::string)
# - NICHT bytes, anders als unter Python 2. _resolve_stream() erzeugt aber
# bewusst bytes (fuers VTi-Original so vorgesehen), was zu
# "TypeError: in method 'new_eServiceReference', argument 3 of type
# 'std::string const &'" fuehrt (Traceback erst durch den Logging-Patch oben
# sichtbar geworden, vorher lautlos verschluckt vom Twisted-Reactor).
_URL_ENCODE_OLD = '''    if isinstance(stream_url_str, bytes):
        stream_url_bytes = stream_url_str
    else:
        stream_url_bytes = stream_url_str.encode('utf-8')

    if isinstance(title, bytes):
        title_bytes = title
    else:
        title_bytes = title.encode('utf-8')'''

_URL_ENCODE_NEW = '''    if isinstance(stream_url_str, bytes):
        stream_url_bytes = stream_url_str.decode("utf-8", "replace")
    else:
        stream_url_bytes = stream_url_str

    if isinstance(title, bytes):
        title_bytes = title.decode("utf-8", "replace")
    else:
        title_bytes = title'''

# Gleiches Muster wie oben, zwei weitere Stellen die eServiceReference() mit
# per .encode("utf-8") erzeugten bytes fuettern.
_OFFLINE_REF_OLD = '''    path = _OFFLINE_VIDEO.encode("utf-8") if isinstance(_OFFLINE_VIDEO, str) else _OFFLINE_VIDEO
    ref = eServiceReference(4097, 0, path)
    if name:
        title = (name + " (Offline)").encode("utf-8") if isinstance(name, type(u"")) else (name + " (Offline)")
        ref.setName(title)'''

_OFFLINE_REF_NEW = '''    path = _OFFLINE_VIDEO
    ref = eServiceReference(4097, 0, path)
    if name:
        title = name + " (Offline)"
        ref.setName(title)'''

_BLACK_BG_OLD = '''    path = _BLACK_BACKGROUND_VIDEO.encode("utf-8") if isinstance(_BLACK_BACKGROUND_VIDEO, str) else _BLACK_BACKGROUND_VIDEO
    return eServiceReference(4097, 0, path)'''

_BLACK_BG_NEW = '''    path = _BLACK_BACKGROUND_VIDEO
    return eServiceReference(4097, 0, path)'''

# Live-Stream-Blocker (per strace bestaetigt): das native ServiceApp
# oeffnet vor dem eigentlichen exteplayer3-Start selbst kurz eine
# Probe-Verbindung zum lokalen Playlist-Proxy. Der
# alte Server bediente per handle_request() aber nur GENAU EINEN Request und
# schloss danach - die Probe-Verbindung verbrauchte den einzigen Slot, die
# echte Anfrage von exteplayer3 (Sekunden spaeter) lief ins Leere
# ("Connection refused", kein Fehler im eigenen Log). Fix: mehrere Requests
# bedienen, bis das Zeitfenster ausgeschoepft ist.
_HTTP_SERVER_OLD = '''        server.timeout = 20.0
        port = server.server_address[1]

        t = threading.Thread(target=lambda: (server.handle_request(), server.server_close()))
        t.daemon = True
        t.start()'''

_HTTP_SERVER_NEW = '''        server.timeout = 20.0
        port = server.server_address[1]

        def _serve():
            import time as _time
            deadline = _time.time() + server.timeout
            while _time.time() < deadline:
                try:
                    server.handle_request()
                except Exception:
                    break
            server.server_close()

        t = threading.Thread(target=_serve)
        t.daemon = True
        t.start()'''

# Downloader.filepath wird bewusst als bytes gebaut (fuer Python 2 korrekt,
# vermeidet Locale-Encoding-Probleme bei os-Funktionen). plugin.py's
# _bg_download_done() ruft darauf aber fp.lower().endswith(".mp4") auf - ein
# str-Literal-Vergleich, den Python 3 bei bytes mit "endswith first arg must
# be bytes or a tuple of bytes, not str" ablehnt. Symptom: Download selbst
# lief durch ("Fertig: ..." im Log), aber die anschliessende
# MP4->TS-Konvertierung wurde nie angestossen, weil genau diese Pruefung in
# _bg_download_done() abstuerzte (durch den except in Downloader._run()
# lautlos als "Fehler: ..." nach dem "Fertig" geloggt statt sichtbar zu
# crashen). os.path.exists()/open()/os.remove() funktionieren unter Python 3
# gleichermassen mit str wie mit bytes, daher hier einfach kein encode("utf-8")
# mehr - filepath bleibt str, konsistent mit save_dir/filename direkt darueber.
_FILEPATH_OLD = '''        candidate = os.path.join(save_dir, filename).encode("utf-8")
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(save_dir, u"%s_%d%s" % (base, counter, ext)).encode("utf-8")
            counter += 1'''

_FILEPATH_NEW = '''        candidate = os.path.join(save_dir, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(save_dir, u"%s_%d%s" % (base, counter, ext))
            counter += 1'''

# _inject_direct_hits() baut das "Direkte Treffer"-Gruppenlabel bewusst per
# .encode("utf-8") als bytes (fuer Python 2 korrekt, konsistent mit anderen
# Gruppennamen aus _build_groups()/mediathek.py, die dort ebenfalls bytes
# sind). Unter Python 3 sind nach dem _s()-Fix alle ANDEREN Gruppennamen
# jedoch bereits str - nur dieses eine hartcodierte Label bleibt bytes und
# ist damit unter Python 3 der einzige verbleibende Ausreisser. Symptom:
# Suche -> "Direkte Treffer" ausgewaehlt (mode war zusaetzlich
# durch den do_search()-mode-Bug verdeckt, siehe echte Quelle) ->
# _start_episode_fetch() crasht lautlos (vom eigenen on_ok-try/except
# verschluckt) bei "title_text = self.source_name + " | " + group_str" mit
# "can only concatenate str (not bytes) to str" - Bildschirm reagiert auf OK
# einfach nicht mehr. gname.decode(...).encode("utf-8") in
# _start_episode_fetch() reproduziert das bytes-Ergebnis sogar noch einmal
# fuer alle Folge-Aufrufe. Fix: hier kein encode() mehr, label bleibt str.
_DIRECT_HITS_OLD = '''    label = (">> Direkte Treffer (%d)" % len(direct)).encode("utf-8")'''

_DIRECT_HITS_NEW = '''    label = ">> Direkte Treffer (%d)" % len(direct)'''

# WICHTIG: do_search() nicht in der echten Quelle geaendert, siehe unten -
# das hier ist eine reine Python-3-Vorsichtsmassnahme fuer den Port, KEIN
# Fix eines bestaetigten VTi-Bugs. Unter Python 3 reproduziert: nach Suche
# aus einer Episodenliste heraus blieb self.mode faelschlich auf
# MODE_EPISODES, obwohl _show_groups() (aufgerufen von _on_fetch_done() nach
# erfolgreichem Fetch) self.mode als ALLERERSTE Anweisung auf MODE_GROUPS
# setzt - strukturell sollte das also nicht passieren koennen. Mit
# identischen Reproduktionsschritten auf der echten VTi-Quelle (mehrfach
# getestet, inkl. Logging von on_ok mode=...) tritt der Fehler NICHT auf -
# vermutlich eine Python-3/Timing-spezifische Race Condition (z.B. ein
# verzoegerter Poll-Timer der vorherigen Episodenliste, der self.mode nach
# _show_groups() erneut ueberschreibt), kein deterministischer Code-Fehler.
# Da die echte Ursache nicht gefunden wurde, hier nur als defensive
# Absicherung fuer den py3-Port belassen, nicht in der echten Quelle.
_DO_SEARCH_MODE_OLD = '''                    self.current_search = term
                    save_search_history(term)

                self.page = 0'''

_DO_SEARCH_MODE_NEW = '''                    self.current_search = term
                    save_search_history(term)

                self.mode = MODE_GROUPS
                self.page = 0'''

# _fetch_alpha_thread() (A-Z-Picker-Overlay) UND _fetch_thread() im "az"/
# "za"-Sortiermodus (A-Z direkt in der Gruppenliste) bauen ihre Platzhalter-
# Items von Hand statt ueber mediathek.py, mit demselben .encode("utf-8")-
# Muster wie der alte _DIRECT_HITS-Bug oben: unter Python 3 bleiben
# "group"/"title"/"channel"/"topic" dadurch bytes, waehrend mediathek.py._s()
# (siehe _S_NEW) fuer alle echten Items laengst str liefert.
# _start_episode_fetch() stolpert beim Oeffnen einer so gefundenen Sendung
# dann ueber dieselbe "can only concatenate str (not bytes) to str" bei
# "title_text = self.source_name + " | " + group_str" - vom on_ok-try/
# except lautlos verschluckt, Bildschirm reagiert auf OK einfach nicht
# (matcht GitHub-Issue #1: "Ordner koennen nicht geoeffnet werden", nur
# ueber A-Z reproduzierbar, Server-Suche war nicht betroffen weil deren
# Items durch mediathek.py._s() schon str sind). Fix: _b() (in plugin.py
# fuer py3 bereits auf str gepatcht, siehe _B_NEW) statt manuellem encode().
_ALPHA_FETCH_OLD = '''            ch_bytes = ch.encode("utf-8") if ch else ""
            self._fetch_alpha_result = [
                {"group": t.encode("utf-8"), "title": t.encode("utf-8"),
                 "channel": ch_bytes, "topic": t.encode("utf-8")}
                for t in filtered_topics
            ]'''

_ALPHA_FETCH_NEW = '''            ch_txt = _b(ch) if ch else ""
            self._fetch_alpha_result = [
                {"group": _b(t), "title": _b(t),
                 "channel": ch_txt, "topic": _b(t)}
                for t in filtered_topics
            ]'''

_AZ_SORT_FETCH_OLD = '''                ch_str = _AZ_CH_MAP.get(self.source_name)
                ch_bytes = ch_str.encode("utf-8") if ch_str else ""
                self._fetch_result = [
                    {"group": t.encode("utf-8"), "title": t.encode("utf-8"),
                     "channel": ch_bytes, "topic": t.encode("utf-8")}
                    for t in page_topics'''

_AZ_SORT_FETCH_NEW = '''                ch_str = _AZ_CH_MAP.get(self.source_name)
                ch_txt = _b(ch_str) if ch_str else ""
                self._fetch_result = [
                    {"group": _b(t), "title": _b(t),
                     "channel": ch_txt, "topic": _b(t)}
                    for t in page_topics'''

# write_info_txt() oeffnet die .txt-Begleitdatei bewusst im Text-Modus ("w")
# und schreibt trotzdem per .encode("utf-8") bytes hinein - unter Python 2
# ist das folgenlos (str IST bytes dort, "w" vs "wb" macht auf POSIX keinen
# Unterschied), auf VTi mit der echten v1.9.2 bestaetigt: .txt-Datei
# wird dort einwandfrei geschrieben, kein Bug. Unter Python 3 lehnt ein im
# Text-Modus geoeffnetes File-Objekt einen bytes-Schreibaufruf dagegen mit
# TypeError ab - dort aber vom eigenen except Exception: pass lautlos
# verschluckt, .txt-Datei wird nie geschrieben. Nur fuer den py3-Port
# relevant, daher hier und nicht in der echten Quelle.
_WRITE_INFO_TXT_OLD = '''        if lines:
            with open(txt_path, "w") as f:
                f.write(u"\\n\\n".join(lines).encode("utf-8"))'''

_WRITE_INFO_TXT_NEW = '''        if lines:
            with open(txt_path, "wb") as f:
                f.write(u"\\n\\n".join(lines).encode("utf-8"))'''

# _episode_label() lieferte unter Python 2 per label.encode("utf-8") bytes
# zurueck. Unter Python 3 landeten dadurch bytes in den UI-Listen und in
# _ep_fav_list_entries() crashte das Greifen einer Folge zum Sortieren
# (_b("» ") + label) mit "TypeError: can only concatenate str (not bytes) to str".
# Fix: Rueckgabe ueber _b(label) (liefert str unter Python 3).
_EPISODE_LABEL_OLD = '''    try:
        return label.encode("utf-8")
    except Exception:
        return str(label)'''

_EPISODE_LABEL_NEW = '''    return _b(label)'''

# _build_groups() sortierte per k.decode("utf-8", "replace").lower(). Unter
# Python 3 sind die Gruppennamen k bereits str -> k.decode() wirft AttributeError,
# der vom try/except lautlos verschluckt wurde -> Gruppen blieben unter Python 3
# bei Aufruf von _build_groups (Favoriten, "Alle Mediatheken") unsortiert.
_BUILD_GROUPS_SORT_OLD = '''    if sort_mode == "az":
        try:
            groups_order.sort(key=lambda k: k.decode("utf-8", "replace").lower())
        except Exception:
            pass
    elif sort_mode == "za":
        try:
            groups_order.sort(key=lambda k: k.decode("utf-8", "replace").lower(), reverse=True)
        except Exception:
            pass'''

_BUILD_GROUPS_SORT_NEW = '''    if sort_mode == "az":
        try:
            groups_order.sort(key=lambda k: _b(k).lower())
        except Exception:
            pass
    elif sort_mode == "za":
        try:
            groups_order.sort(key=lambda k: _b(k).lower(), reverse=True)
        except Exception:
            pass'''

# _item_to_bytes() konvertierte alle Stringfelder eines Item-Dicts per
# v.encode("utf-8") in bytes fuer Python 2. Unter Python 3 wurden dadurch alle
# Felder von Einzelfolgen-Favoriten zu bytes (im Widerspruch zum restlichen
# Plugin, wo _s() str liefert). Fuehrte u.a. bei on_download() mit Unterordnern
# zu TypeError bei show.startswith(">> "). Fix: _s(v) liefert str unter Py3.
_ITEM_TO_BYTES_OLD = '''def _item_to_bytes(item):
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
    return result'''

_ITEM_TO_BYTES_NEW = '''def _item_to_bytes(item):
    """Gibt alle String-Werte eines Item-Dicts als native Strings zurueck (Python 3)."""
    _STR_FIELDS = {"title", "group", "channel", "description", "duration",
                   "stream_url_hd", "stream_url_sd"}
    result = {}
    for k, v in item.items():
        if k in _STR_FIELDS:
            result[k] = _s(v)
        else:
            result[k] = v
    return result'''

# get_favorites() normalisierte Gruppen mit Sender-Praefix (z.B. "BR: Schnittgut")
# per group.encode("utf-8") zu bytes (unter Python 2 konsistent mit dem Rest).
# Unter Python 3 wurden diese Items als einzige zu bytes, was beim Oeffnen in
# _start_episode_fetch() exakt wie in GitHub-Issue #1 zu TypeError bei
# self.source_name + " | " + group_str fuehrte. Fix: _s(group) (str).
_FAV_PREFIX_MATCH_OLD = '''                            item = dict(item)
                            item["group"] = group if isinstance(group, bytes) else group.encode("utf-8")
                            matched.append(item)'''

_FAV_PREFIX_MATCH_NEW = '''                            item = dict(item)
                            item["group"] = _s(group)
                            matched.append(item)'''

# _start_episode_fetch() wandelte group_str defensiv per
# gname.decode().encode("utf-8") um - unter Python 3 machte das aus einem
# versehentlichen bytes-Objekt erneut bytes statt str. Mit _b(gname) ist
# group_str unter Python 3 garantiert immer ein nativer str.
_START_EP_FETCH_OLD = '''        try:
            group_str = gname.decode("utf-8", "replace").encode("utf-8")
        except Exception:
            group_str = gname'''

_START_EP_FETCH_NEW = '''        group_str = _b(gname)'''

# In _fav_list_entries() defensive Absicherung: _b(gname) stellt sicher,
# dass die Pfeil-Markierung auch bei unerwarteten bytes-Objekten nie crasht.
_FAV_LIST_ENTRIES_OLD = '''            if i == self._fav_grabbed:
                entries.append(_b("» ") + gname)'''

_FAV_LIST_ENTRIES_NEW = '''            if i == self._fav_grabbed:
                entries.append(_b("» ") + _b(gname))'''

# _browse() wandelte cur per cur.encode("utf-8") in bytes um (fuer Python 2
# vorgesehen). Unter Python 3 landeten dadurch bytes in OeMediathekDirBrowser,
# wodurch beim Anlegen eines neuen Ordners (Gelbe Taste, _create_folder)
# os.path.join(self._cur, name) wegen bytes+str mit "TypeError: Can't mix
# strings and bytes in path components" abstuerzte. Fix: _b(cur) (str).
_BROWSE_START_OLD = '''            cur = get_save_dir()
            start = cur if isinstance(cur, bytes) else cur.encode("utf-8")'''

_BROWSE_START_NEW = '''            cur = get_save_dir()
            start = _b(cur)'''


def main():
    if os.path.exists(DST):
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))

    # Generische Fixes zuerst - die gezielten Patches unten erwarten den
    # bereits normalisierten (str-only, kein \xNN-Mojibake) Zustand.
    for fname in os.listdir(DST):
        if fname.endswith(".py"):
            _fix_byte_escapes(os.path.join(DST, fname))
            _strip_byte_prefix(os.path.join(DST, fname))

    _patch(os.path.join(DST, "plugin.py"), _B_OLD, _B_NEW)
    _patch(os.path.join(DST, "mediathek.py"), _S_OLD, _S_NEW)
    _patch(os.path.join(DST, "download_manager.py"), _DM_B_OLD, _DM_B_NEW)

    for fname, pairs in _RELATIVE_IMPORT_FIXES.items():
        for old, new in pairs:
            _patch(os.path.join(DST, fname), old, new)

    _patch(os.path.join(DST, "player.py"), _APPLY_OLD, _APPLY_NEW)
    _patch(os.path.join(DST, "player.py"), _URL_ENCODE_OLD, _URL_ENCODE_NEW)
    _patch(os.path.join(DST, "player.py"), _OFFLINE_REF_OLD, _OFFLINE_REF_NEW)
    _patch(os.path.join(DST, "player.py"), _BLACK_BG_OLD, _BLACK_BG_NEW)
    _patch(os.path.join(DST, "player.py"), _HTTP_SERVER_OLD, _HTTP_SERVER_NEW)
    _patch(os.path.join(DST, "downloader.py"), _FILEPATH_OLD, _FILEPATH_NEW)
    _patch(os.path.join(DST, "plugin.py"), _DIRECT_HITS_OLD, _DIRECT_HITS_NEW)
    _patch(os.path.join(DST, "plugin.py"), _ALPHA_FETCH_OLD, _ALPHA_FETCH_NEW)
    _patch(os.path.join(DST, "plugin.py"), _AZ_SORT_FETCH_OLD, _AZ_SORT_FETCH_NEW)
    _patch(os.path.join(DST, "plugin.py"), _DO_SEARCH_MODE_OLD, _DO_SEARCH_MODE_NEW)
    _patch(os.path.join(DST, "downloader.py"), _WRITE_INFO_TXT_OLD, _WRITE_INFO_TXT_NEW)
    _patch(os.path.join(DST, "plugin.py"), _EPISODE_LABEL_OLD, _EPISODE_LABEL_NEW)
    _patch(os.path.join(DST, "plugin.py"), _BUILD_GROUPS_SORT_OLD, _BUILD_GROUPS_SORT_NEW)
    _patch(os.path.join(DST, "mediathek.py"), _ITEM_TO_BYTES_OLD, _ITEM_TO_BYTES_NEW)
    _patch(os.path.join(DST, "mediathek.py"), _FAV_PREFIX_MATCH_OLD, _FAV_PREFIX_MATCH_NEW)
    _patch(os.path.join(DST, "plugin.py"), _START_EP_FETCH_OLD, _START_EP_FETCH_NEW)
    _patch(os.path.join(DST, "plugin.py"), _FAV_LIST_ENTRIES_OLD, _FAV_LIST_ENTRIES_NEW)
    _patch(os.path.join(DST, "plugin.py"), _BROWSE_START_OLD, _BROWSE_START_NEW)

    print("py3-Variante erzeugt unter:", DST)


if __name__ == "__main__":
    main()
