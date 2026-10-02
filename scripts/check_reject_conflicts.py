#!/usr/bin/env python3
"""Report repo-routed rules that Surge's pre-matching REJECT lists would override.

On QX and Loon the repo lists come before the ad lists; on Surge the REJECT lists in
surge/modules/home-direct.sgmodule run first (pre-matching). This compares a REJECT list with
the domain and IP rules in rules/*.yaml and reports where the list would win.

  python3 scripts/check_reject_conflicts.py [--self-test] FILE...

FILE is a Surge rule list, or a .sgmodule (its [Rule] lines with a REJECT policy are read;
logical rules and {{{argument}}} rules are listed as not analysed).

Relations:
  definite    every host the repo rule covers is rejected (proven, never inferred)
  partial     some concrete host the repo rule covers is rejected (one subdomain, or only the
              root name of a repo suffix)
  possible    cannot be decided statically (a wildcard against an open suffix space)
  unanalysed  rule types not modelled here; listed, never dropped silently
Not covered: reject IP rules against repo domain rules (needs DNS), keywords against the open
subdomain space of a repo suffix, URL/port/process rejects. A clean report is not a proof that
nothing is over-blocked. Reports describe the current snapshot only.

Exit codes: 0 analysed, no definite/partial; 1 analysed and definite/partial found; 2 input
error (missing file, malformed line, bad CIDR, no input); 4 unexpected error (traceback shown).
"""
from __future__ import annotations

import contextlib
import fnmatch
import io
import ipaddress
import re
import sys
import tempfile
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = ROOT / "rules"
MANIFEST_NAME = "local_rules.yaml"

# Rule-set line grammar, from the Surge manual (rules/overview, rules/ruleset, rules/logical,
# rules/source-and-port). The current manual says Surge skips an invalid line with a warning; the
# repo is stricter on purpose and treats any line it cannot validate as an input error, so a bad
# upstream line fails the remote check instead of quietly dropping rules on the device.
DOMAIN_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD"}
IP_TYPES = {"IP-CIDR", "IP-CIDR6", "GEOIP", "IP-ASN"}
PORT_TYPES = {"DEST-PORT", "SRC-PORT", "IN-PORT"}
SET_TYPES = {"RULE-SET", "DOMAIN-SET"}
LOGICAL_TYPES = {"AND", "OR", "NOT"}
SURGE_RULE_TYPES = DOMAIN_TYPES | IP_TYPES | PORT_TYPES | SET_TYPES | LOGICAL_TYPES | {
    "SRC-IP", "USER-AGENT", "URL-REGEX", "PROCESS-NAME", "PROTOCOL", "HOSTNAME-TYPE", "SUBNET",
    "DEVICE-NAME", "MAC-ADDRESS", "CELLULAR-RADIO", "CELLULAR-CARRIER", "SCRIPT",
}
FLAG_TYPES = {
    "no-resolve": IP_TYPES | SET_TYPES,
    "extended-matching": DOMAIN_TYPES | SET_TYPES | {"URL-REGEX"},
}
KEY_PARAMS = {"notification-text", "notification-interval", "update-interval"}
IP_FAMILY = {"IP-CIDR": 4, "IP-CIDR6": 6}
COMMENT_PREFIXES = ("#", "//", ";")
INLINE_COMMENT = re.compile(r"\s+(?://|#|;).*$")
PORT_RE = re.compile(r"^(?:(\d+)|(\d+)-(\d+)|(?:>=|<=|>|<)(\d+))$")
MAX_LOGICAL_DEPTH = 10


class InputError(Exception):
    """An expected problem with an input file: unreadable, malformed or invalid."""


def parse_network(value: str, rule_type: str, where: str):
    try:
        net = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise InputError(f"{where}: invalid {rule_type} value {value!r}") from exc
    if net.version != IP_FAMILY[rule_type]:
        raise InputError(f"{where}: {rule_type} holds an IPv{net.version} network {value!r}")
    return net


def split_top(text: str, where: str) -> list[str]:
    """Split on commas outside parentheses and double quotes."""
    parts, depth, quoted, start = [], 0, False, 0
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise InputError(f"{where}: unbalanced parentheses")
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    if depth or quoted:
        raise InputError(f"{where}: unbalanced parentheses or quotes")
    parts.append(text[start:].strip())
    return parts


def wrapped(token: str, where: str) -> str:
    if not (token.startswith("(") and token.endswith(")")) or len(split_top(token[1:-1], where)) < 1:
        raise InputError(f"{where}: expected a parenthesised group, got {token!r}")
    return token[1:-1]


def check_value(rule_type: str, value: str, where: str):
    """Validate a rule value; return the parsed network for IP-CIDR/IP-CIDR6."""
    if not value:
        raise InputError(f"{where}: {rule_type} has an empty value")
    if rule_type in IP_FAMILY:
        return parse_network(value, rule_type, where)
    if rule_type in DOMAIN_TYPES and (any(c.isspace() for c in value) or "/" in value):
        raise InputError(f"{where}: invalid {rule_type} value {value!r}")
    if rule_type == "IP-ASN" and not value.isdigit():
        raise InputError(f"{where}: IP-ASN takes a number, got {value!r}")
    if rule_type == "GEOIP" and not re.fullmatch(r"[A-Za-z]{2}", value):
        raise InputError(f"{where}: GEOIP takes a two-letter country code, got {value!r}")
    if rule_type in PORT_TYPES:
        match = PORT_RE.match(value)
        ports = [int(g) for g in match.groups() if g] if match else []
        if not ports or any(p > 65535 for p in ports) or (match.group(2) and ports[0] > ports[1]):
            raise InputError(f"{where}: invalid port expression {value!r}")
    return None


def check_params(rule_type: str, params: list[str], where: str) -> None:
    for param in params:
        key, sep, _ = param.partition("=")
        if sep:
            if key not in KEY_PARAMS or (key == "update-interval" and rule_type not in SET_TYPES):
                raise InputError(f"{where}: parameter {key!r} is not allowed on {rule_type} here")
        elif param == "pre-matching":
            raise InputError(f"{where}: pre-matching is not allowed inside a rule set or sub-rule")
        elif rule_type not in FLAG_TYPES.get(param, ()):
            raise InputError(f"{where}: flag {param!r} is not valid on {rule_type}")


def validate_rule(text: str, where: str, depth: int = 0):
    """Validate one rule-set line or sub-rule (no policy). Return (type, value, network)."""
    tokens = split_top(text, where)
    rule_type = tokens[0]
    if rule_type == "FINAL":
        raise InputError(f"{where}: FINAL is not allowed inside a rule set or logical rule")
    if rule_type not in SURGE_RULE_TYPES:
        raise InputError(f"{where}: {rule_type!r} is not a Surge rule type")
    if len(tokens) < 2:
        raise InputError(f"{where}: malformed rule line {text!r}")
    if rule_type in LOGICAL_TYPES:
        if depth >= MAX_LOGICAL_DEPTH:
            raise InputError(f"{where}: logical rules nest deeper than {MAX_LOGICAL_DEPTH}")
        subs = split_top(wrapped(tokens[1], where), where)
        if rule_type == "NOT" and len(subs) != 1:
            raise InputError(f"{where}: NOT takes exactly one sub-rule")
        for sub in subs:
            validate_rule(wrapped(sub, where), where, depth + 1)
        check_params(rule_type, tokens[2:], where)
        return rule_type, text, None
    value = tokens[1]
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    net = check_value(rule_type, value, where)
    check_params(rule_type, tokens[2:], where)
    return rule_type, value, net


def parse_rules(text: str, name: str, module: bool = False):
    """Return (rules, skipped). Each rule is (type, value, network-or-None).

    A rule list is fully validated (validate_rule); logical rules come back with the whole line
    as their value, so analyse() lists them as unanalysed. A .sgmodule is only scanned for its
    simple REJECT rules (its own syntax, with policies, is not validated here).
    """
    rules, skipped = [], []
    in_rule = not module
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if module and s.startswith("["):
            in_rule = s == "[Rule]"
            continue
        if not in_rule or not s or s.startswith(COMMENT_PREFIXES):
            continue
        where = f"{name}:{lineno}"
        if not module:
            rules.append(validate_rule(INLINE_COMMENT.sub("", s), where))
            continue
        if s.startswith(("AND", "OR", "NOT")) or s.startswith("{{{"):
            skipped.append(s)
            continue
        parts = [p.strip() for p in s.split(",")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise InputError(f"{where}: malformed rule line {s!r}")
        if len(parts) < 3 or not parts[2].startswith("REJECT"):
            continue
        rule_type, value = parts[0], parts[1]
        net = parse_network(value, rule_type, where) if rule_type in IP_FAMILY else None
        rules.append((rule_type, value, net))
    return rules, skipped


def validate_surge_ruleset(text: str, name: str) -> int:
    """Raise InputError unless every line passes validate_rule; return the rule count.

    check_remote_resources uses this for full resource checks and, through parse_rules, for the
    REJECT overlap report, so both paths reject the same lines.
    """
    rules, _ = parse_rules(text, name)
    return len(rules)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc


def load_reject(path: Path):
    return parse_rules(read_text(path), path.name, module=path.suffix == ".sgmodule")


def load_repo(rules_dir: Path = RULES_DIR) -> dict[str, list[tuple[str, object]]]:
    repo: dict[str, list[tuple[str, object]]] = {}
    files = sorted(f for f in rules_dir.glob("*.yaml") if f.name != MANIFEST_NAME)
    if not files:
        raise InputError(f"no rule sources in {rules_dir}")
    for f in files:
        try:
            data = yaml.safe_load(read_text(f)) or {}
        except yaml.YAMLError as exc:
            raise InputError(f"{f.name}: invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise InputError(f"{f.name}: top level must be a mapping")
        for key, values in data.items():
            for value in values or []:
                value = str(value)
                if key in ("ip_cidr", "ip_cidr6"):
                    rule_type = "IP-CIDR" if key == "ip_cidr" else "IP-CIDR6"
                    repo.setdefault(key, []).append((f.name, parse_network(value, rule_type, f.name)))
                else:
                    repo.setdefault(key, []).append((f.name, value))
    return repo


def under(child: str, parent: str) -> bool:
    return child == parent or child.endswith("." + parent)


def analyse(repo, rejects):
    out = {"definite": [], "partial": [], "possible": [], "unanalysed": []}
    suffixes = repo.get("domain_suffix", [])
    domains = repo.get("domain", [])
    keywords = repo.get("domain_keyword", [])
    cidrs = repo.get("ip_cidr", []) + repo.get("ip_cidr6", [])
    for rtype, rval, rnet in rejects:
        tag = f"{rtype},{rval}"
        if rtype in ("DOMAIN-SUFFIX", "DOMAIN"):
            for src, s in suffixes:
                if under(s, rval) and (rtype == "DOMAIN-SUFFIX" or s == rval):
                    out["definite" if rtype == "DOMAIN-SUFFIX" else "partial"].append((src, f"suffix {s}", tag))
                elif under(rval, s):
                    out["partial"].append((src, f"suffix {s}", tag))
            for src, d in domains:
                if (rtype == "DOMAIN-SUFFIX" and under(d, rval)) or d == rval:
                    out["definite"].append((src, f"domain {d}", tag))
            for src, k in keywords:
                if k in rval:
                    out["partial"].append((src, f"keyword {k}", tag))
        elif rtype == "DOMAIN-KEYWORD":
            for src, s in suffixes:
                if rval in s:
                    out["definite"].append((src, f"suffix {s}", tag))
            for src, d in domains:
                if rval in d:
                    out["definite"].append((src, f"domain {d}", tag))
            for src, k in keywords:
                if rval in k or k in rval:
                    out["possible"].append((src, f"keyword {k}", tag))
        elif rtype == "DOMAIN-WILDCARD":
            # Matching some host under a repo suffix never proves the pattern covers the whole
            # suffix space (root and every subdomain), so the strongest result is partial.
            rx = re.compile(fnmatch.translate(rval), re.IGNORECASE)
            literal_tail = rval.rsplit("*", 1)[-1].lstrip(".").replace("?", "")
            for src, s in suffixes:
                if rx.match(s) or rx.match("x." + s):
                    out["partial"].append((src, f"suffix {s}", tag))
                elif literal_tail and "." in literal_tail and (under(literal_tail, s) or under(s, literal_tail)):
                    out["possible"].append((src, f"suffix {s}", tag))
            for src, d in domains:
                if rx.match(d):
                    out["definite"].append((src, f"domain {d}", tag))
        elif rtype in IP_FAMILY:
            for src, other in cidrs:
                if rnet.version == other.version and rnet.overlaps(other):
                    kind = "definite" if other.subnet_of(rnet) else "partial"
                    out[kind].append((src, f"cidr {other}", tag))
        else:
            out["unanalysed"].append(("-", "-", tag))
    return out


def self_test() -> int:
    global analyse
    net = lambda v: ipaddress.ip_network(v)  # noqa: E731
    repo = {
        "domain_suffix": [("t", "example.com"), ("t", "safe.org")],
        "domain": [("t", "login.example.net")],
        "domain_keyword": [("t", "tracker")],
        "ip_cidr": [("t", net("10.1.0.0/16"))],
    }
    rejects = [
        ("DOMAIN-SUFFIX", "ads.example.com", None),   # partial: subdomain inside a repo suffix
        ("DOMAIN-SUFFIX", "example.net", None),        # definite: repo domain under it
        ("DOMAIN-KEYWORD", "ads", None),               # nothing literal; open suffix space not reported
        ("DOMAIN-KEYWORD", "safe", None),              # definite: repo suffix contains the keyword
        ("DOMAIN-WILDCARD", "*.example.com", None),    # partial
        ("DOMAIN-SUFFIX", "tracker.io", None),         # partial: shadows a repo keyword
        ("IP-CIDR", "10.1.2.0/24", net("10.1.2.0/24")),  # partial overlap
        ("IP-CIDR", "10.0.0.0/8", net("10.0.0.0/8")),    # definite: repo network inside
        ("DOMAIN-WILDCARD", "example.com", None),      # partial: root name only
        ("DOMAIN-WILDCARD", "s?fe.org", None),         # partial: root name only
        ("DOMAIN-SET", "https://x/list.txt", None),    # unanalysed
        ("URL-REGEX", "^http://ad", None),             # unanalysed
    ]
    got = {k: sorted(t for _, _, t in v) for k, v in analyse(repo, rejects).items()}
    want = {
        "definite": sorted(["DOMAIN-SUFFIX,example.net", "DOMAIN-KEYWORD,safe", "IP-CIDR,10.0.0.0/8"]),
        "partial": sorted(["DOMAIN-SUFFIX,ads.example.com", "DOMAIN-SUFFIX,tracker.io", "IP-CIDR,10.1.2.0/24",
                           "DOMAIN-WILDCARD,*.example.com", "DOMAIN-WILDCARD,example.com",
                           "DOMAIN-WILDCARD,s?fe.org"]),
        "possible": [],
        "unanalysed": sorted(["DOMAIN-SET,https://x/list.txt", "URL-REGEX,^http://ad"]),
    }
    failures = [] if got == want else [f"analyse: {got} != {want}"]
    case2 = analyse({"domain_suffix": [("t", "x.example.com")]}, [("DOMAIN-WILDCARD", "x.*.com", None)])
    if case2["definite"] or not case2["partial"]:
        failures.append(f"x.*.com against suffix x.example.com must be partial: {case2}")

    for text in ("DOMAIN-SUFFIX\n", "IP-CIDR,not-a-cidr\n", "IP-CIDR6,fe80::zz/10\n", "IP-CIDR,2001:db8::/32\n"):
        try:
            parse_rules(text, "sample.list")
        except InputError:
            continue
        failures.append(f"parse_rules accepted {text.strip()!r}")
    invalid = (
        "HOST-SUFFIX,example.com",                 # QX rule type
        "+.example.com",                           # Clash domain-set line
        "AND,((DOMAIN,example.com)",               # unbalanced (implementation review I2)
        "DEST-PORT,not-a-port",                    # bad port (I2)
        "DOMAIN,example.com,pre-matching",         # pre-matching inside a rule set (I2)
        "DEST-PORT,70000",
        "DEST-PORT,200-100",
        "FINAL,DIRECT",
        "NOT,((DOMAIN,a.com),(DOMAIN,b.com))",
        "AND,(DOMAIN,example.com)",
        "AND,((DOMAIN,example.com),(DEST-PORT,x))",
        "DOMAIN,example.com,no-resolve",           # flag not valid on the type
        "IP-ASN,AS13335",
        "GEOIP,CHN",
        "DOMAIN-SUFFIX,bad domain.com",
    )
    for line in invalid:
        try:
            validate_surge_ruleset(line + "\n", "bad.list")
            failures.append(f"validate_surge_ruleset accepted {line!r}")
        except InputError:
            pass
    valid = (
        "DOMAIN-SUFFIX, gateway.icloud.com\n"
        "IP-CIDR,1.2.3.0/24,no-resolve\n"
        "DOMAIN,cdn.example.org,extended-matching\n"
        "IP-ASN,13335\n"
        "GEOIP,CN\n"
        "DEST-PORT,>=50000\n"
        "SRC-PORT,10000-20000\n"
        "AND,((DOMAIN,example.com),(DEST-PORT,443))\n"
        "AND,((NOT,((SRC-IP,192.168.1.110))),(DOMAIN-SUFFIX,example.com))\n"
        "DOMAIN-KEYWORD,ads // trailing comment\n"
    )
    if validate_surge_ruleset(valid, "ok.list") != 10:
        failures.append("validate_surge_ruleset: valid list miscounted")

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "bad.yaml").write_text("ip_cidr:\n  - 10.0.0.0/33\n", encoding="utf-8")
        try:
            load_repo(Path(tmp))
            failures.append("load_repo accepted an invalid ip_cidr")
        except InputError:
            pass

    real = analyse
    def broken(*_args):
        raise RuntimeError("simulated defect")
    analyse = broken
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".list", delete=False, encoding="utf-8") as f:
            f.write("DOMAIN-SUFFIX,noconflict.invalid\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = main([f.name], quiet=True)
        if code != 4:
            failures.append(f"an unexpected exception must exit 4, got {code}")
    finally:
        analyse = real
        Path(f.name).unlink()

    if failures:
        print("SELF-TEST FAILED", *failures, sep="\n  ")
        return 1
    print("self-test ok")
    return 0


def run(argv: list[str]) -> int:
    if not argv:
        raise InputError("no input files")
    repo = load_repo()
    loaded = [(Path(arg), *load_reject(Path(arg))) for arg in argv]
    print("repo:", {k: len(v) for k, v in repo.items()})
    total = 0
    for path, rules, skipped in loaded:
        counts: dict[str, int] = {}
        for rule_type, _, _ in rules:
            counts[rule_type] = counts.get(rule_type, 0) + 1
        print(f"\n== {path.name}\n   rules: {counts}")
        for s in skipped:
            print("   not analysed:", s)
        out = analyse(repo, rules)
        for kind, hits in out.items():
            print(f"   {kind}: {len(hits)}")
            for hit in hits:
                print("     ", hit)
        total += len(out["definite"]) + len(out["partial"])
    print("\ndefinite+partial total:", total)
    return 1 if total else 0


def main(argv: list[str], quiet: bool = False) -> int:
    if argv[:1] == ["--self-test"]:
        return self_test()
    try:
        return run(argv)
    except InputError as exc:
        print(f"INPUT ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception:  # an unexpected defect: keep the traceback, never report it as a result
        if not quiet:
            traceback.print_exc()
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
