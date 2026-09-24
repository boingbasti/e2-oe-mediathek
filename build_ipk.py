#!/usr/bin/env python3
# build_ipk.py - Baut ein Enigma2-IPK-Paket fuer OeMediathek

import argparse
import os
import tarfile
import struct
import io

PLUGIN_NAME = "enigma2-plugin-extensions-oemediathek"
VERSION = "1.9.5"
ARCHITECTURE = "all"
MAINTAINER = "saufsoldat"
HOMEPAGE = "https://github.com/boingbasti/e2-oe-mediathek"
DESCRIPTION = "Enigma2-Plugin zum Streamen der öffentlich-rechtlichen Mediatheken"
INSTALL_PATH = "/usr/lib/enigma2/python/Plugins/Extensions/OeMediathek"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --py3: baut aus py3_build/OeMediathek (siehe py3_port.py) statt der echten
# Python-2/VTi-Quelle unter OeMediathek/, eigener Output-Dateiname damit
# beide IPKs nebeneinander bestehen koennen (z.B. als GitHub-Release-Assets).
_args = argparse.ArgumentParser(description=__doc__)
_args.add_argument("--py3", action="store_true",
                    help="Python-3-Variante aus py3_build/OeMediathek bauen (siehe py3_port.py)")
_args = _args.parse_args()

if _args.py3:
    PLUGIN_SRC = os.path.join(SCRIPT_DIR, "py3_build", "OeMediathek")
    OUTPUT_FILE = os.path.join(SCRIPT_DIR, f"{PLUGIN_NAME}_py3_{VERSION}_{ARCHITECTURE}.ipk")
else:
    PLUGIN_SRC = os.path.join(SCRIPT_DIR, "OeMediathek")
    OUTPUT_FILE = os.path.join(SCRIPT_DIR, f"{PLUGIN_NAME}_{VERSION}_{ARCHITECTURE}.ipk")

# Erwartete Anzahl Logo-PNGs (ohne defaults/) zur Build-Zeit ermitteln - postinst
# prueft damit nach der Installation, ob wirklich alle Dateien angekommen sind.
# Direkter Verzeichnis-Count statt fester Zahl, damit neue Sender-Logos nie
# vergessen werden nachzuziehen.
_LOGO_DIR_SRC = os.path.join(PLUGIN_SRC, "logos")
_LOGO_COUNT = len([f for f in os.listdir(_LOGO_DIR_SRC) if f.endswith(".png")]) if os.path.isdir(_LOGO_DIR_SRC) else 0


PREINST_SCRIPT = f"""#!/bin/sh
echo "OeMediathek preinst gestartet"
LOGO_DIR="{INSTALL_PATH}/logos"
BACKUP_DIR="/tmp/oemediathek_logo_backup"
if [ -d "$LOGO_DIR" ]; then
    echo "OeMediathek: Logo-Ordner gefunden, sichere alle Logos"
    rm -rf "$BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    for f in "$LOGO_DIR"/*.png; do
        [ -f "$f" ] || continue
        fname=$(basename "$f")
        echo "OeMediathek: sichere $fname"
        cp "$f" "$BACKUP_DIR/$fname"
    done
else
    echo "OeMediathek: Logo-Ordner nicht gefunden, Erstinstallation"
fi
echo "OeMediathek preinst fertig"
"""

POSTINST_SCRIPT = f"""#!/bin/sh
echo "OeMediathek postinst gestartet"
rm -f {INSTALL_PATH}/*.pyo {INSTALL_PATH}/*.pyc
LOGO_DIR="{INSTALL_PATH}/logos"
BACKUP_DIR="/tmp/oemediathek_logo_backup"
DEFAULTS_DIR="$LOGO_DIR/defaults"
if [ -d "$BACKUP_DIR" ]; then
    echo "OeMediathek: stelle benutzerdefinierte Logos wieder her"
    for f in "$BACKUP_DIR"/*.png; do
        [ -f "$f" ] || continue
        fname=$(basename "$f")
        default="$DEFAULTS_DIR/$fname"
        if [ -f "$default" ] && ! cmp -s "$f" "$default"; then
            echo "OeMediathek: stelle $fname wieder her (benutzerdefiniert)"
            cp "$f" "$LOGO_DIR/$fname"
        fi
    done
    rm -rf "$BACKUP_DIR"
else
    echo "OeMediathek: kein Backup gefunden, Erstinstallation"
fi
if [ -d "$DEFAULTS_DIR" ]; then
    echo "OeMediathek: loesche defaults-Ordner"
    rm -rf "$DEFAULTS_DIR"
fi
# Nachtraeglich pruefen, ob wirklich alle Logos angekommen sind - eine
# abgebrochene/beschaedigte Uebertragung der IPK laesst je nach Abbruchpunkt
# im tar-Archiv genau diesen hinteren, groessten Teil des Pakets fehlen,
# waehrend die vorne liegenden Python-Dateien schon geschrieben sind und das
# Plugin dadurch ohne jede Fehlermeldung startet, nur eben ohne Logos.
if [ -d "$LOGO_DIR" ]; then
    ACTUAL_LOGOS=$(ls "$LOGO_DIR"/*.png 2>/dev/null | wc -l)
else
    ACTUAL_LOGOS=0
fi
if [ "$ACTUAL_LOGOS" -lt {_LOGO_COUNT} ]; then
    echo "OeMediathek WARNUNG: Logo-Ordner unvollstaendig ($ACTUAL_LOGOS von {_LOGO_COUNT} Dateien) - die IPK-Uebertragung war vermutlich unvollstaendig (z.B. WLAN-Aussetzer oder voller Speicher). Bitte die IPK neu installieren."
fi
echo "OeMediathek postinst fertig"
"""


def build_control_tar():
    control_content = f"""Package: {PLUGIN_NAME}
Version: {VERSION}
Architecture: {ARCHITECTURE}
Maintainer: {MAINTAINER}
Homepage: {HOMEPAGE}
Section: misc
Priority: optional
License: GPL-2.0
Description: {DESCRIPTION}
""".encode("utf-8")

    preinst_content  = PREINST_SCRIPT.encode("utf-8")
    postinst_content = POSTINST_SCRIPT.encode("utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        info = tarfile.TarInfo(name="./control")
        info.size = len(control_content)
        tar.addfile(info, io.BytesIO(control_content))

        info = tarfile.TarInfo(name="./preinst")
        info.size = len(preinst_content)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(preinst_content))

        info = tarfile.TarInfo(name="./postinst")
        info.size = len(postinst_content)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(postinst_content))

    return buf.getvalue()


def build_data_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for root, dirs, files in os.walk(PLUGIN_SRC):
            # __pycache__ überspringen
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            # Verzeichnis-Eintrag hinzufügen
            rel_dir = os.path.relpath(root, PLUGIN_SRC)
            if rel_dir == ".":
                arcdir = "./" + INSTALL_PATH.lstrip("/")
            else:
                arcdir = "./" + INSTALL_PATH.lstrip("/") + "/" + rel_dir.replace("\\", "/")
            dir_info = tarfile.TarInfo(name=arcdir)
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            tar.addfile(dir_info)
            for fname in files:
                if fname.endswith(".pyc") or fname.endswith(".pyo"):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, PLUGIN_SRC)
                arcname = "./" + INSTALL_PATH.lstrip("/") + "/" + rel.replace("\\", "/")
                tar.add(fpath, arcname=arcname)
    return buf.getvalue()


def write_ar(path, members):
    """Schreibt ein ar-Archiv. members = [(name, data), ...]"""
    with open(path, "wb") as f:
        f.write(b"!<arch>\n")
        for name, data in members:
            # ar-Header: name(16) mtime(12) uid(6) gid(6) mode(8) size(10) magic(2)
            name_b = name.encode("utf-8").ljust(16)[:16]
            mtime_b = b"0".ljust(12)
            uid_b = b"0".ljust(6)
            gid_b = b"0".ljust(6)
            mode_b = b"100644".ljust(8)
            size_b = str(len(data)).encode("utf-8").ljust(10)
            magic_b = b"\x60\x0a"
            f.write(name_b + mtime_b + uid_b + gid_b + mode_b + size_b + magic_b)
            f.write(data)
            if len(data) % 2 != 0:
                f.write(b"\n")  # Padding auf gerade Byte-Grenze


def main():
    print("Baue control.tar.gz ...")
    control_tar = build_control_tar()

    print("Baue data.tar.gz ...")
    data_tar = build_data_tar()

    debian_binary = b"2.0\n"

    print(f"Schreibe {OUTPUT_FILE} ...")
    write_ar(OUTPUT_FILE, [
        ("debian-binary", debian_binary),
        ("control.tar.gz", control_tar),
        ("data.tar.gz", data_tar),
    ])

    size_kb = os.path.getsize(OUTPUT_FILE) // 1024
    print(f"Fertig: {os.path.basename(OUTPUT_FILE)} ({size_kb} KB)")


if __name__ == "__main__":
    main()
