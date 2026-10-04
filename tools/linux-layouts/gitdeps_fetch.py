"""Fetches only the chosen files listed in an Unreal Commit.gitdeps.xml (what Setup.sh downloads).

Setup.sh pulls every binary dependency of the engine (tens of GB); building UnrealBuildTool and
running UnrealHeaderTool need a handful of them. The index maps each file to a blob, and each
blob to an offset inside a gzipped pack on Epic's CDN.

Usage: gitdeps_fetch.py <engine root> <regex> [<regex>...]
"""
import gzip
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET


def main(argv: list[str]) -> int:
    root, patterns = argv[0], [re.compile(p) for p in argv[1:]]
    tree = ET.parse(os.path.join(root, "Engine", "Build", "Commit.gitdeps.xml")).getroot()
    base = tree.get("BaseUrl").rstrip("/")
    files = {f.get("Name"): f.get("Hash") for f in tree.iter("File")
             if any(p.search(f.get("Name")) for p in patterns)}
    blobs = {b.get("Hash"): b for b in tree.iter("Blob")}
    packs = {p.get("Hash"): p for p in tree.iter("Pack")}
    by_pack: dict[str, list[tuple[str, ET.Element]]] = {}
    for name, blob_hash in files.items():
        blob = blobs[blob_hash]
        by_pack.setdefault(blob.get("PackHash"), []).append((name, blob))
    print(f"{len(files)} files in {len(by_pack)} packs", file=sys.stderr)
    for pack_hash, members in by_pack.items():
        pack = packs[pack_hash]
        url = f"{base}/{pack.get('RemotePath')}/{pack_hash}"
        with urllib.request.urlopen(url, timeout=120) as response:
            data = gzip.decompress(response.read())
        for name, blob in members:
            offset, size = int(blob.get("PackOffset")), int(blob.get("Size"))
            target = os.path.join(root, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as out:
                out.write(data[offset:offset + size])
            if name.endswith((".sh", "Exe")) or "/Linux/" in name and "." not in os.path.basename(name):
                os.chmod(target, 0o755)
            print(name, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
