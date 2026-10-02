#!/usr/bin/env python3
"""Build surge/modules/rewrite.sgmodule from pinned Surge modules.

Surge has no profile section that lists modules (unlike Loon's [Plugin]); each module is
installed and enabled per device. This merges the modules enabled in the Loon setup into one
repo module, so the phone installs a single URL. Sources are either upstream Surge modules
pinned to a commit or release tag, or frozen conversions kept in surge/modules/converted/ (the
ddgksf2013 QX rewrites that have no Surge version, converted like loon/plugins/). Module
arguments are applied here; the header records each source and its sha256. Scripts referenced
inside a module still follow that module's own URLs (kokoryh's point at its master branch, as
on Loon).

  python3 scripts/build_surge_modules.py          # fetch the pinned sources, write the module
  python3 scripts/build_surge_modules.py --check  # regenerate and compare, no write

Exit codes: 0 ok / up to date, 1 out of date, 2 a source could not be fetched or parsed.
To update, change SOURCES (or a converted file), run the script, and review the diff.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "surge" / "modules" / "rewrite.sgmodule"

# Order follows the Loon [Plugin] list. Surge runs only the first matching http-response script
# and the first matching header-mode URL Rewrite, so keep app-specific modules before collections.
SOURCES = (
    {
        "name": "kokoryh 哔哩哔哩增强",
        "url": "https://raw.githubusercontent.com/kokoryh/Sparkle/"
        "1bc5b545a544d7d59b1cf821785daddc3868e4a5/release/surge/module/bilibili.sgmodule",
        # "#" is the author's off switch: it turns the sponsor-block script line into a comment
        # and passes sponsorBlock "#" to the protobuf script (same as the Loon pilot advice).
        "arguments": {"空降助手": "#"},
    },
    {
        # Loon uses Kelee's YouTube plugin, which is built on this module.
        "name": "Maasea YouTube (Music) Enhance",
        "url": "https://raw.githubusercontent.com/Maasea/sgmodule/"
        "65075cdb388fc5e3094afd7e7314c67b243f3525/YouTube.Enhance.sgmodule",
        "arguments": {},
    },
    {
        "name": "blackmatrix7 Advertising",
        "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/"
        "5fe08949edb51935c67e0766dc4bc94c0dad0509/rewrite/Surge/Advertising/Advertising.sgmodule",
        "arguments": {},
    },
    {
        "name": "NSRingo iRingo Siri",
        "url": "https://github.com/NSRingo/Siri/releases/download/v4.2.7/iRingo.Siri.sgmodule",
        "arguments": {},
    },
    {
        "name": "blackmatrix7 SafeRedirect",
        "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/"
        "5fe08949edb51935c67e0766dc4bc94c0dad0509/rewrite/Surge/SafeRedirect/SafeRedirect.sgmodule",
        "arguments": {},
    },
    {
        "name": "app2smile Spotify",
        "url": "https://raw.githubusercontent.com/app2smile/rules/"
        "df6366a7024e0b3f0aa3510c5b791eea6f3cba89/module/spotify.module",
        "arguments": {},
    },
    {"name": "ddgksf2013 WeiboAds", "path": "surge/modules/converted/WeiboAds.sgmodule", "arguments": {}},
    {"name": "ddgksf2013 GoofishAds", "path": "surge/modules/converted/GoofishAds.sgmodule", "arguments": {}},
    {"name": "ddgksf2013 Douban", "path": "surge/modules/converted/Douban.sgmodule", "arguments": {}},
    {"name": "ddgksf2013 Q-Search", "path": "surge/modules/converted/Q-Search.sgmodule", "arguments": {}},
    {"name": "ddgksf2013 General", "path": "surge/modules/converted/General.sgmodule", "arguments": {}},
)
# Sections a module may carry here. [General] and [MITM] are key = value sections whose
# %APPEND% values are merged; the others are line lists kept in source order.
KEYED_SECTIONS = ("General", "MITM")
LIST_SECTIONS = ("Rule", "URL Rewrite", "Header Rewrite", "Map Local", "Body Rewrite", "Script")
USER_AGENT = "proxy-config-surge-modules/1.0"


class SourceError(Exception):
    """A source could not be fetched, read or parsed."""


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise SourceError(f"{url}: HTTP {response.status}")
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"{url}: {exc}") from exc


def load(source: dict) -> tuple[str, bytes]:
    if "path" in source:
        try:
            return source["path"], (ROOT / source["path"]).read_bytes()
        except OSError as exc:
            raise SourceError(f"{source['path']}: {exc}") from exc
    return source["url"], fetch(source["url"])


def parse_arguments(text: str) -> dict[str, str]:
    for line in text.splitlines():
        match = re.match(r"^#!arguments\s*=\s*(.*)$", line)
        if match:
            pairs = {}
            for item in match.group(1).split(","):
                key, sep, value = item.partition(":")
                if not sep:
                    raise SourceError(f"cannot parse #!arguments item {item!r}")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] == '"':
                    value = value[1:-1]  # the module body already quotes these where needed
                pairs[key.strip()] = value
            return pairs
    return {}


def apply_arguments(text: str, name: str, overrides: dict[str, str]) -> str:
    values = parse_arguments(text)
    unknown = sorted(set(overrides) - set(values))
    if unknown:
        raise SourceError(f"{name}: unknown module arguments {unknown}")
    values.update(overrides)
    for key, value in values.items():
        text = text.replace("{{{" + key + "}}}", value)
    if "{{{" in text:
        raise SourceError(f"{name}: unreplaced placeholder {re.search(r'{{{[^}]*}}}', text).group(0)}")
    return text


def split_sections(text: str, name: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            if current not in KEYED_SECTIONS + LIST_SECTIONS:
                raise SourceError(f"{name}: unsupported section [{current}]")
            sections.setdefault(current, [])
        elif current is not None and stripped:
            sections[current].append(line.rstrip())
    return sections


def merge_keyed(sources: list[tuple[str, dict[str, list[str]]]], section: str) -> list[str]:
    merged: dict[str, list[str]] = {}
    for name, sections in sources:
        for line in sections.get(section, []):
            if line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition("=")
            value = value.strip()
            if not sep or not value.startswith("%APPEND%"):
                raise SourceError(f"{name}: [{section}] only takes 'key = %APPEND% ...' lines, got {line!r}")
            items = [v.strip() for v in value[len("%APPEND%"):].split(",") if v.strip()]
            bucket = merged.setdefault(key.strip(), [])
            bucket.extend(v for v in items if v not in bucket)
    return [f"{key} = %APPEND% {', '.join(values)}" for key, values in merged.items()]


def render() -> str:
    parsed: list[tuple[str, dict[str, list[str]]]] = []
    header = [
        "#!name=去广告与增强合集",
        "#!desc=把 Loon 中启用的插件合并为一个模块：哔哩哔哩（空降助手已关闭）、YouTube、blackmatrix7 去广告与安全重定向、"
        "Siri、Spotify，以及墨鱼的微博、闲鱼、豆瓣、Safari 超级搜索、神机重定向。需要 MitM。由仓库生成，不要再单独安装其中的模块。",
        "#!category=proxy-config",
        "",
        "# Generated by scripts/build_surge_modules.py. Do not edit by hand; change SOURCES there and rerun.",
    ]
    for source in SOURCES:
        where, body = load(source)
        header.append(f"# Source: {source['name']} {where}")
        header.append(f"#   sha256 {hashlib.sha256(body).hexdigest()}; arguments: "
                      + (", ".join(f"{k}={v}" for k, v in source["arguments"].items()) or "defaults"))
        text = apply_arguments(body.decode("utf-8"), source["name"], source["arguments"])
        parsed.append((source["name"], split_sections(text, source["name"])))

    names = [line.split("=", 1)[0].strip() for _, sections in parsed for line in sections.get("Script", [])
             if "=" in line and not line.lstrip().startswith("#")]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise SourceError(f"duplicate [Script] names across sources: {duplicates}")

    lines = header
    for section in KEYED_SECTIONS[:1] + LIST_SECTIONS + KEYED_SECTIONS[1:]:
        if section in KEYED_SECTIONS:
            body = merge_keyed(parsed, section)
        else:
            body = []
            for name, sections in parsed:
                if sections.get(section):
                    body.append(f"# --- {name}")
                    body.extend(sections[section])
        if body:
            lines += ["", f"[{section}]", *body]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed module (needs network)")
    args = parser.parse_args()
    try:
        content = render()
    except SourceError as exc:
        print(f"SOURCE ERROR: {exc}", file=sys.stderr)
        return 2
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else None
    if args.check:
        if current != content:
            print(f"{OUTPUT.relative_to(ROOT)} is out of date", file=sys.stderr)
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} is up to date")
        return 0
    if current == content:
        print(f"{OUTPUT.relative_to(ROOT)} is already up to date")
    else:
        OUTPUT.write_text(content, encoding="utf-8")
        print(f"Updated {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
