#!/usr/bin/env python3
"""Build the mirrored fmz200 Loon plugins and keep every hosted Loon plugin icon small.

fmz200's per-app split plugins (fmz200/wool_scripts Loon/plugin/split/) are used as they are, except
for #!icon and XiaoYuZhou's feature-preserving corrections in xiaoyuzhou_rewrites.py. Upstream points
all of them at one 560 KB animated GIF, which Loon kept downloading on the phone. This writes
loon/plugins/fmz200-<App>.plugin from the pinned commit with those reviewed changes, and sets #!icon
of the other plugins hosted in loon/plugins/ the same way. Icons are the
apps' App Store icons (100x100 PNG, a few KB, served by Apple's CDN); GIF icons are refused.

  python3 scripts/build_loon_plugins.py          # fetch the pinned sources, write the plugins
  python3 scripts/build_loon_plugins.py --check  # regenerate and compare, no write

Exit codes: 0 ok / up to date, 1 out of date, 2 a source could not be fetched or parsed.
To update, change FMZ200_COMMIT, MIRRORS or ICONS, run the script, and review the diff.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from xiaoyuzhou_rewrites import preserve_features

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "loon" / "plugins"
FMZ200_COMMIT = "5d5f63fcf98bc69d5f8f1b1bae6f86a01ee4bb97"
FMZ200 = f"https://raw.githubusercontent.com/fmz200/wool_scripts/{FMZ200_COMMIT}/Loon/plugin/split"
# fmz200 split plugin -> part directory. All script-free (tests/test_loon_config.py checks).
MIRRORS = {
    "WeChatOfficialAccount": "partW",
    "Meituan-MeituanWaimai": "partM",
    "Hupu": "partH",
    "Mijia": "partM",
    "MaoYan": "partM",
    "LeKe": "partB",
    "Douban": "partD",
    "ChinaMobile": "partZ",
    "XiaoYuZhou": "partX",
}
MZ = "https://is1-ssl.mzstatic.com/image/thumb/"
# Plugin file in loon/plugins/ -> the matching app's App Store icon (iTunes lookup by the bundle ID
# installed on the phone, artworkUrl100 as PNG). Q-Search uses DuckDuckGo (it rewrites DuckDuckGo
# searches); General uses Google (it redirects google.cn).
ICONS = {
    "AlibabaAmdc.plugin": MZ + "Purple221/v4/9d/81/f8/9d81f836-fcaf-f7fc-6150-04f2761764fe/AppIcon-0-0-1x_U007emarketing-0-10-0-85-220.png/100x100bb.png",
    "DaMai.plugin": MZ + "Purple211/v4/c5/23/4c/c5234cd0-5980-c184-e14a-41ca193e83f0/AppIcon-0-0-1x_U007emarketing-0-6-0-85-220.png/100x100bb.png",
    "NeteaseMail.plugin": MZ + "Purple211/v4/ac/2d/4f/ac2d4ffc-f481-f3aa-cbff-65a7ebc9c04c/AppIconStore-0-0-1x_U007emarketing-0-8-0-85-220.png/100x100bb.png",
    "QSearch.plugin": MZ + "Purple211/v4/40/fa/0b/40fa0b23-e17b-977e-740f-2b5fa9962d2c/AppIcon-0-0-1x_U007epad-0-0-0-1-0-0-sRGB-0-85-220.png/100x100bb.png",
    "Weibo.plugin": MZ + "Purple211/v4/a8/52/27/a85227af-35ed-aea1-c7ac-51ede3a7a45e/WeiboAppIcon-0-0-1x_U007epad-0-1-0-85-220.png/100x100bb.png",
    "XianYu.plugin": MZ + "Purple211/v4/a7/7d/97/a77d970b-9da7-9154-56ab-46f114ea7736/AppIcon-0-0-1x_U007epad-0-1-0-sRGB-85-220.png/100x100bb.png",
    "DaMaiAds.plugin": MZ + "Purple211/v4/c5/23/4c/c5234cd0-5980-c184-e14a-41ca193e83f0/AppIcon-0-0-1x_U007emarketing-0-6-0-85-220.png/100x100bb.png",
    "Douban.plugin": MZ + "Purple211/v4/79/54/c5/7954c588-8484-b8cb-747b-9f94e897ac22/AppIcon-0-0-1x_U007emarketing-0-7-0-85-220.png/100x100bb.png",
    "General.plugin": MZ + "Purple211/v4/4f/d3/5b/4fd35b53-e6d3-ee49-342a-8a7ace6fe960/SuperG_ios26-0-0-1x_U007epad-0-0-0-1-0-0-sRGB-0-0-0-85-220.png/100x100bb.png",
    "GoofishAds.plugin": MZ + "Purple211/v4/a7/7d/97/a77d970b-9da7-9154-56ab-46f114ea7736/AppIcon-0-0-1x_U007epad-0-1-0-sRGB-85-220.png/100x100bb.png",
    "NeteaseMailAds.plugin": MZ + "Purple211/v4/ac/2d/4f/ac2d4ffc-f481-f3aa-cbff-65a7ebc9c04c/AppIconStore-0-0-1x_U007emarketing-0-8-0-85-220.png/100x100bb.png",
    "Q-Search.plugin": MZ + "Purple211/v4/40/fa/0b/40fa0b23-e17b-977e-740f-2b5fa9962d2c/AppIcon-0-0-1x_U007epad-0-0-0-1-0-0-sRGB-0-85-220.png/100x100bb.png",
    "UmetripAds.plugin": MZ + "Purple221/v4/f3/24/9f/f3249f91-f692-a1a4-ea79-df11a012566f/AppIcon-0-0-1x_U007emarketing-0-7-0-85-220.png/100x100bb.png",
    "Umetrip.plugin": MZ + "Purple221/v4/f3/24/9f/f3249f91-f692-a1a4-ea79-df11a012566f/AppIcon-0-0-1x_U007emarketing-0-7-0-85-220.png/100x100bb.png",
    "WeRead.plugin": MZ + "Purple211/v4/35/92/e6/3592e6f4-0b0b-f2e4-7968-7033b281e1f5/AppIcon-0-1x_U007epad-0-11-0-85-220-0.png/100x100bb.png",
    "WeiboAds.plugin": MZ + "Purple211/v4/a8/52/27/a85227af-35ed-aea1-c7ac-51ede3a7a45e/WeiboAppIcon-0-0-1x_U007epad-0-1-0-85-220.png/100x100bb.png",
    "Xiaohongshu.plugin": MZ + "Purple221/v4/ae/f7/20/aef720a0-32ec-e719-2ea0-5b98afdaba74/AppIcon-0-0-1x_U007epad-0-11-0-85-220.png/100x100bb.png",
    "XiaoYuZhouAds.plugin": MZ + "Purple211/v4/01/79/78/01797835-05d5-f693-5b59-0937a2786021/AppIcon-0-0-1x_U007epad-0-1-85-220.png/100x100bb.png",
    "fmz200-WeChatOfficialAccount.plugin": MZ + "Purple221/v4/61/70/2e/61702e05-0531-165f-5cd3-012c8b5b3b20/AppIcon-0-0-1x_U007epad-0-1-0-sRGB-0-85-220.png/100x100bb.png",
    "fmz200-Meituan-MeituanWaimai.plugin": MZ + "Purple211/v4/f9/7c/56/f97c56ea-ddcb-7fe0-66aa-97d5f481ef62/AppIcon-0-0-1x_U007emarketing-0-0-0-7-0-0-sRGB-85-220.png/100x100bb.png",
    "fmz200-Hupu.plugin": MZ + "Purple211/v4/09/ef/56/09ef5673-0da7-0e9f-a2a8-fc2bc6ca4ec1/AppIcon-0-0-1x_U007emarketing-0-8-0-sRGB-85-220.png/100x100bb.png",
    "fmz200-Mijia.plugin": MZ + "Purple211/v4/e3/28/c4/e328c4ce-6fb1-2cf9-39b9-3e93b61997b3/AppIcon-0-0-1x_U007emarketing-0-8-0-sRGB-0-85-220.png/100x100bb.png",
    "fmz200-MaoYan.plugin": MZ + "Purple211/v4/18/93/5d/18935dff-1aad-50c3-8bfa-c2feba69c28e/AppIcon-0-0-1x_U007epad-0-1-0-85-220.png/100x100bb.png",
    "fmz200-LeKe.plugin": MZ + "Purple221/v4/cd/a6/aa/cda6aaa6-81bd-affe-524a-8f99a03c3a1c/AppIcon-0-0-1x_U007epad-0-1-0-85-220.png/100x100bb.png",
    "fmz200-Douban.plugin": MZ + "Purple211/v4/79/54/c5/7954c588-8484-b8cb-747b-9f94e897ac22/AppIcon-0-0-1x_U007emarketing-0-7-0-85-220.png/100x100bb.png",
    "fmz200-XiaoYuZhou.plugin": MZ + "Purple211/v4/01/79/78/01797835-05d5-f693-5b59-0937a2786021/AppIcon-0-0-1x_U007epad-0-1-85-220.png/100x100bb.png",
    "fmz200-ChinaMobile.plugin": MZ + "Purple221/v4/31/4c/ba/314cba49-6e99-7cff-7054-c5bb99c15b8c/AppIcon-0-0-1x_U007epad-0-1-0-sRGB-85-220.png/100x100bb.png",
}
USER_AGENT = "proxy-config-loon-plugins/1.0"


class SourceError(Exception):
    """A source could not be fetched or parsed."""


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise SourceError(f"{url}: HTTP {response.status}")
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"{url}: {exc}") from exc


def set_icon(text: str, name: str) -> str:
    icon = ICONS[name]
    if not icon.endswith(".png"):
        raise SourceError(f"{name}: icons must be small PNGs, got {icon}")
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if re.match(r"^#!icon\s*=", line)]
    if len(hits) != 1:
        raise SourceError(f"{name}: expected exactly one #!icon line, found {len(hits)}")
    lines[hits[0]] = f"#!icon={icon}"
    return "\n".join(lines)


def mirror(app: str) -> str:
    url = f"{FMZ200}/{MIRRORS[app]}/{app}.lpx"
    body = fetch(url)
    text = body.decode("utf-8").replace("\r\n", "\n")
    name = f"fmz200-{app}.plugin"
    text = set_icon(text, name)
    if app == "XiaoYuZhou":
        text = preserve_features(text)
    lines = text.split("\n")
    header_end = max(i for i, line in enumerate(lines) if line.startswith("#!")) + 1
    lines[header_end:header_end] = [
        "# Mirror of fmz200's Loon split plugin; generated by scripts/build_loon_plugins.py, do not edit by hand.",
        f"# Source: {url}",
        f"# Source SHA-256: {hashlib.sha256(body).hexdigest()}",
        ("# Changes: small PNG icon; preserve AI summaries, normal search, categories and recommendations."
         if app == "XiaoYuZhou" else
         "# Only change: #!icon (upstream uses a 560 KB animated GIF) is the app's App Store icon."),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def render() -> dict[Path, str]:
    outputs = {PLUGIN_DIR / f"fmz200-{app}.plugin": mirror(app) for app in MIRRORS}
    for name in ICONS:
        path = PLUGIN_DIR / name
        if path in outputs:
            continue
        if not path.exists():
            raise SourceError(f"{name}: listed in ICONS but not in loon/plugins/")
        outputs[path] = set_icon(path.read_text(encoding="utf-8"), name)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare with the committed plugins (needs network)")
    args = parser.parse_args()
    try:
        outputs = render()
    except SourceError as exc:
        print(f"SOURCE ERROR: {exc}", file=sys.stderr)
        return 2
    unlisted = sorted(p.name for p in PLUGIN_DIR.glob("*.plugin") if p.name not in ICONS)
    stale = sorted(p for p in PLUGIN_DIR.glob("fmz200-*.plugin") if p not in outputs)
    if args.check:
        changed = [p for p, c in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        for path in changed + stale:
            print(f"{path.relative_to(ROOT)} is out of date", file=sys.stderr)
        for name in unlisted:
            print(f"loon/plugins/{name} has no entry in ICONS", file=sys.stderr)
        if changed or stale or unlisted:
            return 1
        print(f"{len(outputs)} Loon plugins are up to date")
        return 0
    for path, content in outputs.items():
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.write_text(content, encoding="utf-8")
        print(f"Updated {path.relative_to(ROOT)}")
    for path in stale:
        path.unlink()
        print(f"Removed {path.relative_to(ROOT)}")
    for name in unlisted:
        print(f"WARNING: loon/plugins/{name} has no entry in ICONS", file=sys.stderr)
    print(f"{len(outputs)} Loon plugins are up to date")
    return 1 if unlisted else 0


if __name__ == "__main__":
    raise SystemExit(main())
