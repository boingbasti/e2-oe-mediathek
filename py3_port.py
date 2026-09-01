# Generiert die Python-3-Variante des Plugins (fuer OpenATV u.ae.) aus der
# echten (Python-2/VTi-)Quelle unter OeMediathek/, komplett per Patches auf
# eine Kopie angewendet. Schreibt nur nach py3_build/ (gitignored, wird bei
# jedem Lauf frisch erzeugt), ruehrt OeMediathek/ selbst nicht an. Wird vom
# --py3-Flag in build_ipk.py sowie der GitHub Action bei Release-Erstellung
# genutzt, siehe CLAUDE.md.

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
# Kopie dieses Helpers (andere Signatur: s statt val, kein Docstring) - wurde
# live auf .13 uebersehen: Download-Manager-Screen blieb komplett leer ohne
# jede Fehlermeldung im Log, weil Label(_b("...")) dort bytes an
# eLabel.setText() gab (Python-3-SWIG-Binding erwartet str, siehe _B_NEW),
# was lautlos zu keinem gerenderten Text fuehrte statt einer Exception.
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
# Python 3 (OpenATV) schlaegt das mit "No module named 'mediathek'" fehl -
# live auf .13 verifiziert. oe-alliance-plugins loest das exakt so
# (bestaetigt per Diff-Vergleich ihrer plugin.py).
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
# als UTF-8-Bytes -> Mojibake ("Ã–R Mediathek"), live auf .13 verifiziert.
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
# verschluckt den kompletten Docstring-Koerper als ein einziges Fake-Literal -
# live reproduziert: dadurch wurde ein echtes b"..."-Byte-Literal direkt nach
# einem Docstring in player.py nicht mehr als eigenstaendiger Treffer erkannt
# und blieb faelschlich unveraendert (siehe _strip_byte_prefix).
#
# Die eigene comment-Gruppe ist ebenso zwingend: ein einzelnes Apostroph in
# einem #-Kommentar (z.B. "# ...thread's results...", player.py Zeile 94)
# wird sonst als oeffnendes '...'-Literal gelesen und verschluckt als
# Fake-Match ALLES bis zum naechsten zufaelligen Anfuehrungszeichen -
# live reproduziert: das hat mehrere hundert Zeilen Code inkl. eines
# b"(Offline)"-Literals unsichtbar gemacht, weit ueber die Kommentarzeile
# hinaus. Kommentare muessen daher als eigene, unangetastete Alternative VOR
# den String-Alternativen erkannt werden.
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
# -> TypeError: can only concatenate str (not "bytes") to str, live auf .13
# reproduziert. Stichprobe aller verbleibenden b"..."-Literale (83x) zeigt
# ausschliesslich Text (Wochentage, Labels, Trennzeichen) - keine echten
# Binaerdaten. Fix: b-Prefix bei allen einfachen (nicht triple-quoted)
# Byte-Literalen entfernen. \b verhindert Treffer in rb"..."/br"...".
def _strip_byte_prefix(path):
    """Nutzt bewusst dieselbe _STRING_LITERAL-Tokenisierung wie
    _fix_byte_escapes() statt eines eigenen b"..."-Regex: ein naives, blind
    ueber den Rohtext scannendes Muster (\\bb["']...) traf faelschlich auch
    auf ein 'b' am Wortende direkt vor einem FREMDEN Anfuehrungszeichen
    innerhalb eines anderen String-Literals - live reproduziert in
    mediathek.py: 'abGroup: "gruppe-b", userSegment: ""' (Teil eines
    einfach gequoteten GraphQL-Query-Strings) wurde zu "gruppe-",
    das 'b' lautlos verschluckt. Ursache erst gefunden, nachdem die ZDF-
    GraphQL-Abfrage auf dem py3-Testgeraet .13 deterministisch 0 statt 26
    Collections lieferte - der veraenderte abGroup-Wert landete in einer
    nicht existenten ZDF-A/B-Testgruppe. _STRING_LITERAL matcht dagegen
    immer das VOLLSTAENDIGE aeussere Literal zuerst (Alternierung auf '...'
    vs "..." ab der öffnenden Anfuehrung), respektiert also echte
    Literalgrenzen statt beliebiger b"-Fundstellen irgendwo im Text.

    Entfernt das b-Praefix NIE ohne die \\xNN-Escapes im Inhalt zu dekodieren:
    _fix_byte_escapes() laesst echte Byte-Literale bewusst unangetastet (dort
    bedeutet \\xNN roh-Byte), sobald hier aber nur das 'b' gestrichen wird,
    ist es ein GANZ NORMALES String-Literal, in dem \\xNN unter Python 3 als
    einzelner Unicode-Codepoint gelesen wird, nicht als UTF-8-Byte -> Mojibake
    ("gruppe-b" waere hier kein Beispiel, aber b">> Demn\\xc3\\xa4chst" in
    plugin.py wurde so zu "DemnÃ¤chst", live auf .13 in der ARD-Themenliste
    reproduziert. Deshalb hier dieselbe _hex_repl-Dekodierung wie in
    _fix_byte_escapes() anwenden, nicht nur das 'b' wegschneiden."""
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


# Nur fuer die Diagnose auf .13: _apply() in play_stream_async() faengt
# Exceptions aus play_resolved_stream() nicht ab, die verschwinden dadurch
# im Twisted-Reactor ohne jede sichtbare Spur (weder Crash noch eigenes Log).
# Live reproduziert: Qualitaetsauswahl-Fenster schliesst sich, Player oeffnet
# nie, kein Fehler in oemediathek.log. Temporaerer Diagnose-Patch, NICHT Teil
# des eigentlichen Py2->3-Ports - nur um die tatsaechliche Fehlermeldung zu
# sehen.
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
# 'std::string const &'" fuehrt, live auf .13 reproduziert (Traceback erst
# durch den eigenen Diagnose-Patch unten sichtbar geworden, vorher lautlos
# verschluckt vom Twisted-Reactor).
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

# Live-Stream-Blocker (per strace auf .13 bestaetigt): das native ServiceApp
# auf diesem OpenATV-Build oeffnet vor dem eigentlichen exteplayer3-Start
# selbst kurz eine Probe-Verbindung zu unserem lokalen Playlist-Proxy. Der
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
# be bytes or a tuple of bytes, not str" ablehnt. Live auf .13 reproduziert:
# Download selbst lief durch ("Fertig: ..." im Log), aber die anschliessende
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
# ist damit unter Python 3 der einzige verbleibende Ausreisser. Live auf .13
# reproduziert: Suche -> "Direkte Treffer" ausgewaehlt (mode war zusaetzlich
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
# Fix eines bestaetigten VTi-Bugs. Live auf .13 (Python 3) reproduziert:
# nach Suche aus einer Episodenliste heraus blieb self.mode faelschlich auf
# MODE_EPISODES, obwohl _show_groups() (aufgerufen von _on_fetch_done() nach
# erfolgreichem Fetch) self.mode als ALLERERSTE Anweisung auf MODE_GROUPS
# setzt - strukturell sollte das also nicht passieren koennen. Auf der
# echten VTi-Box .11 mit identischen Reproduktionsschritten (mehrfach
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

# write_info_txt() oeffnet die .txt-Begleitdatei bewusst im Text-Modus ("w")
# und schreibt trotzdem per .encode("utf-8") bytes hinein - unter Python 2
# ist das folgenlos (str IST bytes dort, "w" vs "wb" macht auf POSIX keinen
# Unterschied), live auf .11 mit der echten v1.9.2 bestaetigt: .txt-Datei
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
    _patch(os.path.join(DST, "plugin.py"), _DO_SEARCH_MODE_OLD, _DO_SEARCH_MODE_NEW)
    _patch(os.path.join(DST, "downloader.py"), _WRITE_INFO_TXT_OLD, _WRITE_INFO_TXT_NEW)

    print("py3-Variante erzeugt unter:", DST)


if __name__ == "__main__":
    main()
