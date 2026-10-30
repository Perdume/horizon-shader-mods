"""Write a copy of a shader pack zip with some options of lib/settings.glsl changed.

    python -I tools/variant_pack.py SRC.zip DST.zip [--off NAME ...] [--set NAME VALUE] [--after MATCH TEXT]

--off NAME      comment out the line `#define NAME` (exact name; a trailing `*` matches a prefix)
--set NAME VALUE
                give `#define NAME <value> //[choices]` another value (the comment stays); NAME must be
                defined with a value exactly once. e.g. --set WATER_A 0.30
--after MATCH TEXT
                add the line TEXT (same indent) after the first line containing MATCH
                e.g. --after 'const float shadowDistance' 'const float shadowIntervalSize = 0.0;'

Nothing is extracted; entries are copied byte for byte except that one file.
"""

import argparse
import re
import zipfile

SETTINGS = "lib/settings.glsl"
DEFINE = re.compile(r"^(\s*)#define\s+(\w+)")


def matches(name: str, wanted: list[str]) -> bool:
    return any(
        name.startswith(w[:-1]) if w.endswith("*") else name == w for w in wanted
    )


def edit(text: str, off: list[str], set_: list[tuple[str, str]],
         after: list[tuple[str, str]]) -> tuple[str, list[str]]:
    lines = text.split("\n")
    done = []
    for i, line in enumerate(lines):
        m = DEFINE.match(line)
        if m and matches(m.group(2), off):
            lines[i] = "//" + line.lstrip()
            done.append(f"off {m.group(2)}")
    for name, value in set_:
        valued = re.compile(rf"^(\s*#define\s+{re.escape(name)}\s+)(\S+)(.*)$")
        found = [i for i, line in enumerate(lines) if valued.match(line)]
        if len(found) != 1:
            done.append(f"NOT SET: {name} ({len(found)} lines define it with a value)")
            continue
        old = valued.match(lines[found[0]]).group(2)
        lines[found[0]] = valued.sub(lambda m: m.group(1) + value + m.group(3), lines[found[0]])
        done.append(f"set {name}: {old} -> {value}")
    for match, added in after:
        at = next((i for i, line in enumerate(lines) if match in line), None)
        if at is None:
            done.append(f"NOT FOUND: {match}")
            continue
        indent = lines[at][: len(lines[at]) - len(lines[at].lstrip())]
        lines.insert(at + 1, indent + added)
        done.append(f"added {added}")
    return "\n".join(lines), done


def variant(src: str, dst: str, off: list[str], set_: list[tuple[str, str]],
            after: list[tuple[str, str]]) -> list[str]:
    done: list[str] = []
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith(SETTINGS):
                text, done = edit(data.decode("utf-8"), off, set_, after)
                data = text.encode("utf-8")
            zout.writestr(info.filename, data)
    return done


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--off", nargs="+", default=[], metavar="NAME")
    p.add_argument("--set", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"), dest="set_")
    p.add_argument("--after", nargs=2, action="append", default=[], metavar=("MATCH", "TEXT"))
    args = p.parse_args()
    changes = variant(args.src, args.dst, args.off, [tuple(a) for a in args.set_],
                      [tuple(a) for a in args.after])
    for line in changes or ["(nothing changed)"]:
        print(line)
