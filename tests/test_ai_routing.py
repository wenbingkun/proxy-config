#!/usr/bin/env python3
"""Offline domain-routing contracts using generated lists and production rule order.

External lists are independent overlap fixtures, not a client or DNS emulator.
The real-core runner can reuse AI_CASES with actual AIExtra payloads.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AI = "🤖 人工智能"
MOBILE_AI = AI + " · 自动"

# Core, authentication and content examples are independent of the rule-source list.
AI_CASES = (
    "preview.livepreview.claude.app",
    "widget.claudemcpcontent.com",
    "bridge.claudemcpclient.com",
    "ab.oaistatsig.com",
    "cdn.openaimerge.com",
    "sora.com",
    "aistudio.google.com",
    "notebooklm.google.com",
    "notebooklm-pa.googleapis.com",
    "geminiweb-pa.clients6.google.com",
    "appsgenaiserver-pa.googleapis.com",
    "webchannel-alkalimakersuite-pa.clients6.google.com",
    "cloudaicompanion.googleapis.com",
    "api.githubcopilot.com",
    "api.individual.githubcopilot.com",
    "copilot-proxy.githubusercontent.com",
    "origin-tracker.githubusercontent.com",
    "production-copilot-host-centralus-02.webpubsub.azure.com",
    "production-copilot-host-westus3.webpubsub.azure.com",
    "copilotprodattachments.blob.core.windows.net",
    "copilot.microsoft.com",
    "copilot.cloud.microsoft",
    "sydney.bing.com",
    "psc2.cf2.poecdn.net",
    "perplexity.com",
    "api.pplx.ai",
    "pplx-res.cloudinary.com",
    "ppl-ai-file-upload.s3.amazonaws.com",
    "marketplace.cursorapi.com",
    "downloads.cursor-cdn.com",
    "workspace.region.cursorvm.com",
    "agent-stores.s3.us-east-1.amazonaws.com",
    "cursor.blob.core.windows.net",
    "server.codeiumdata.com",
    "app.windsurf.build",
    "api.devin.ai",
    "auth.grokusercontent.com",
    "auth.grokipedia.com",
    "accounts.spacexsi.com",
    "accounts.spacex.ai",
    "muse.ai",
    "hatch.metaaivm.com",
)
SHARED_CONTROLS = (
    "storage.googleapis.com",
    "accounts.google.com",
    "www.googleapis.com",
    "login.live.com",
    "api.github.com",
    "graph.facebook.com",
    "other.s3.amazonaws.com",
    "other.s3.us-east-1.amazonaws.com",
    "other.blob.core.windows.net",
    "other.cloudinary.com",
    "other.webpubsub.azure.com",
    "cloudflare-ech.com",
    "widget.claudemcpcontent.com.example.net",
    "evil-claude.app",
)
EXACT_CONTROLS = ("child.cdn.openaimerge.com", "child.accounts.spacexsi.com")
# Deliberately broad overlaps expose a moved/missing AIExtra reference.
OVERLAPS = {
    "Google": ["DOMAIN-SUFFIX,google.com", "DOMAIN-SUFFIX,googleapis.com"],
    "Microsoft": ["DOMAIN-SUFFIX,microsoft.com", "DOMAIN-SUFFIX,cloud.microsoft",
                  "DOMAIN-SUFFIX,blob.core.windows.net", "DOMAIN-SUFFIX,webpubsub.azure.com"],
    "GitHub": ["DOMAIN-SUFFIX,githubusercontent.com", "DOMAIN-SUFFIX,github.com"],
    "WhatsApp": ["DOMAIN,graph.facebook.com"],
    "Amazon": ["DOMAIN-SUFFIX,amazonaws.com"],
    "ProxyLite": ["DOMAIN-SUFFIX,claude.app", "DOMAIN-SUFFIX,poecdn.net",
                  "DOMAIN-SUFFIX,cursorapi.com", "DOMAIN-SUFFIX,codeiumdata.com"],
}


def read_rules(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".yaml":
        return yaml.safe_load(text)["payload"]
    return [s.strip() for s in text.splitlines() if s.strip() and not s.startswith(("#", ";"))]


def match(rule: str, host: str) -> bool:
    fields = [s.strip() for s in rule.split(",")]
    if len(fields) < 2:
        return False
    kind, value = fields[:2]
    if kind.upper() in {"DOMAIN", "HOST"}:
        return host == value
    if kind.upper() in {"DOMAIN-SUFFIX", "HOST-SUFFIX"}:
        return host == value or host.endswith("." + value)
    if kind.upper() in {"DOMAIN-KEYWORD", "HOST-KEYWORD"}:
        return value in host
    return False


def first_policy(records: list[tuple[str, str]], host: str) -> str | None:
    return next((policy for rule, policy in records if match(rule, host)), None)


def repo_or_overlap(url: str, client: str) -> list[str]:
    local = re.search(rf"/{client}/rules/([a-z_]+\.list)$", url)
    if local:
        return read_rules(ROOT / client / "rules" / local[1])
    service = re.search(r"/rule/(?:Loon|Surge|QuantumultX)/([^/]+)/", url)
    return OVERLAPS.get(service[1], []) if service else []


def section(path: Path, name: str) -> list[str]:
    active = False
    result = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("["):
            active = s == name
        elif active and s and not s.startswith(("#", ";")):
            result.append(s)
    return result


def mihomo_records(path: Path) -> list[tuple[str, str]]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    records = []
    for line in config["rules"]:
        fields = line.split(",")
        if fields[0] == "RULE-SET":
            name, policy = fields[1:3]
            spec = config["rule-providers"][name]
            local = re.search(r"/mihomo/rules/([a-z_]+\.yaml)$", spec["url"])
            entries = read_rules(ROOT / "mihomo/rules" / local[1]) if local else OVERLAPS.get(name, [])
            records.extend((entry, policy) for entry in entries)
        elif len(fields) > 2:
            records.append((line, fields[2]))
    return records


def mobile_records(client: str) -> list[tuple[str, str]]:
    records = []
    if client == "qx":
        path = ROOT / "quantumultx/bootstrap.example.conf"
        for line in section(path, "[filter_local]"):
            fields = line.split(",")
            if len(fields) > 2:
                records.append((line, fields[2].strip()))
        for line in section(path, "[filter_remote]"):
            url, *args = [s.strip() for s in line.split(",")]
            params = dict(arg.split("=", 1) for arg in args if "=" in arg)
            if params.get("enabled", "true") == "false":
                continue
            if url.endswith("/quantumultx/filter/repo.snippet"):
                for rule in read_rules(ROOT / "quantumultx/filter/repo.snippet"):
                    records.append((rule, rule.rsplit(",", 1)[1].strip()))
            else:
                records.extend((rule, params["force-policy"]) for rule in repo_or_overlap(url, "quantumultx"))
        return records
    if client == "loon":
        for line in section(ROOT / "loon/bootstrap.example.conf", "[Rule]"):
            fields = line.split(",")
            if len(fields) > 2:
                records.append((line, fields[2].strip()))
        for line in section(ROOT / "loon/bootstrap.example.conf", "[Remote Rule]"):
            url, *args = [s.strip() for s in line.split(",")]
            params = dict(arg.split("=", 1) for arg in args if "=" in arg)
            if params.get("enabled", "true") != "false":
                records.extend((rule, params["policy"]) for rule in repo_or_overlap(url, "loon"))
        return records
    for line in section(ROOT / "surge/proxy-config.conf", "[Rule]"):
        fields = line.split(",")
        if fields[0] == "RULE-SET":
            records.extend((rule, fields[2]) for rule in repo_or_overlap(fields[1], "surge"))
        elif len(fields) > 2:
            records.append((line, fields[2]))
    return records


def main() -> None:
    configs = ("mihomo/verge/config.yaml", "mihomo/verge/config-single.yaml",
               "mihomo/shellcrash/config-router.template.yaml",
               "mihomo/shellcrash/config-router-single.template.yaml")
    for name in (*configs, "qx", "loon", "surge"):
        records = mihomo_records(ROOT / name) if name in configs else mobile_records(name)
        expected = MOBILE_AI if name in {"qx", "loon"} else AI
        for host in AI_CASES:
            assert first_policy(records, host) == expected, (name, host, first_policy(records, host))
        for host in SHARED_CONTROLS:
            assert first_policy(records, host) not in {AI, MOBILE_AI}, (name, host)
        # A broad Google rule moved ahead of the real records must expose the original misroute.
        moved = [(entry, "wrong-google") for entry in OVERLAPS["Google"]] + records
        assert first_policy(moved, "appsgenaiserver-pa.googleapis.com") != expected
    # Existing upstream keyword rules may be broader; exact-host scope is a contract
    # of the shared supplement, not a promise to override those upstream lists.
    for path in ("mihomo/rules/ai_extra.yaml", "loon/rules/ai_extra.list",
                 "surge/rules/ai_extra.list", "quantumultx/filter/repo.snippet"):
        entries = read_rules(ROOT / path)
        for host in EXACT_CONTROLS:
            assert not any(match(rule, host) for rule in entries), (path, host)
    for name in ("loon/bootstrap.example.conf", "quantumultx/bootstrap.example.conf"):
        lines = (ROOT / name).read_text().splitlines()
        copilot = [s for s in lines if "/Copilot/Copilot.list," in s]
        assert len(copilot) == 1 and "tag=Microsoft Copilot," in copilot[0], name
    print(f"AI routing contracts passed: {len(AI_CASES)} positives, {len(SHARED_CONTROLS)} "
          f"shared/spoof and {len(EXACT_CONTROLS)} exact-host controls, "
          "seven production configs and priority mutations.")


if __name__ == "__main__":
    main()
