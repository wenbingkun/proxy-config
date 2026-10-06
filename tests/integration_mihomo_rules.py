#!/usr/bin/env python3
"""Real-core rule/DNS regression with local providers and an isolated redir-host DNS stub.

Run explicitly with --core/--mmdb; CI supplies its existing hash-verified dependencies.
Only the production rule skeleton is reused. This does not emulate device DNS modes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mihomo_rule_matching.yaml"
CONFIG = ROOT / "mihomo/verge/config.yaml"


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected mapping")
    return value


def build_fixture(source: dict, fixture: dict, directory: Path, socks: int, controller: int, dns: int) -> tuple[dict, dict]:
    if not isinstance(source.get("rules"), list) or not source["rules"] or not source["rules"][-1].startswith("MATCH,"):
        raise ValueError("fixture requires terminal MATCH to isolate every fallback")
    providers = source["rule-providers"]
    assigned = fixture["providers"]
    if not isinstance(providers, dict) or not isinstance(assigned, dict):
        raise ValueError("provider definitions must be mappings")
    if set(assigned) - set(providers):
        raise ValueError(f"missing tested provider definitions: {sorted(set(assigned) - set(providers))}")
    local, counts = {}, {}
    (directory / "providers").mkdir()
    for index, (name, spec) in enumerate(providers.items()):
        behavior, fmt = spec.get("behavior"), spec.get("format", "yaml")
        if behavior not in {"domain", "ipcidr", "classical"} or fmt not in {"yaml", "text"}:
            raise ValueError(f"{name}: unsupported fixture behavior/format")
        content = assigned.get(name)
        if content is not None and content["behavior"] != behavior:
            raise ValueError(f"{name}: tested provider behavior changed")
        payload = content["payload"] if content is not None else []
        if not isinstance(payload, list) or not all(isinstance(entry, str) for entry in payload):
            raise ValueError(f"{name}: fixture payload must be strings")
        path = f"providers/{index}.{'yaml' if fmt == 'yaml' else 'txt'}"
        body = yaml.safe_dump({"payload": payload}) if fmt == "yaml" else "".join(entry + "\n" for entry in payload)
        (directory / path).write_text(body, encoding="utf-8")
        local[name] = {"type": "file", "behavior": behavior, "format": fmt, "path": "./" + path}
        counts[name] = len(payload)
    groups, rules = {}, []
    for rule in source["rules"]:
        parts = rule.split(",")
        kind = parts[0]
        if kind == "RULE-SET":
            if len(parts) not in {3, 4} or parts[1] not in providers or (len(parts) == 4 and parts[3] != "no-resolve"):
                raise ValueError(f"undefined provider or unsupported RULE-SET: {rule}")
        elif kind in {"PROCESS-NAME", "GEOIP"}:
            if len(parts) != 3:
                raise ValueError(f"unsupported fixture rule: {rule}")
        elif kind != "MATCH" or len(parts) != 2:
            raise ValueError(f"unsupported fixture rule: {rule}")
        policy_index = 1 if kind == "MATCH" else 2
        group = "fixture:" + parts[policy_index]
        groups[group] = {"name": group, "type": "select", "proxies": ["REJECT"]}
        parts[policy_index] = group
        rules.append(",".join(parts))
    return {
        "socks-port": socks, "external-controller": f"127.0.0.1:{controller}",
        "bind-address": "127.0.0.1", "allow-lan": False, "mode": "rule", "log-level": "info",
        "ipv6": False, "find-process-mode": "off", "sniffer": {"enable": False}, "tun": {"enable": False},
        "geodata-mode": False, "geo-auto-update": False,
        "geox-url": {key: "http://127.0.0.1:9/disabled" for key in ("mmdb", "asn", "geoip", "geosite")},
        "dns": {"enable": True, "enhanced-mode": "redir-host", "ipv6": False,
                "use-hosts": False, "use-system-hosts": False, "respect-rules": False,
                "nameserver": [f"udp://127.0.0.1:{dns}"], "default-nameserver": [f"127.0.0.1:{dns}"]},
        "rule-providers": local, "proxy-groups": list(groups.values()), "rules": rules,
    }, counts


class CountingDNS:
    def __init__(self, cases: list[dict]):
        self.answers = {case["host"]: case["answer"] for case in cases if "answer" in case}
        self.questions: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        while not self.stop.is_set():
            try:
                packet, address = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                if len(packet) < 12 or struct.unpack("!H", packet[4:6])[0] != 1:
                    raise ValueError("expected one DNS question")
                labels, pos = [], 12
                while packet[pos]:
                    length = packet[pos]
                    if length > 63:
                        raise ValueError("compressed/malformed DNS question")
                    labels.append(packet[pos + 1:pos + 1 + length].decode("ascii"))
                    pos += length + 1
                pos += 1
                qtype, qclass = struct.unpack("!HH", packet[pos:pos + 4])
                end = pos + 4
                name = ".".join(labels).lower()
                with self.lock:
                    self.questions.append((name, qtype))
                if name not in self.answers or qclass != 1:
                    raise ValueError(f"unexpected DNS question: {name}/{qtype}/{qclass}")
                answer = b""
                if qtype == 1:
                    answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(self.answers[name])
                reply = packet[:2] + struct.pack("!HHHHH", 0x8180, 1, bool(answer), 0, 0) + packet[12:end] + answer
                self.sock.sendto(reply, address)
            except (ValueError, IndexError, UnicodeError, struct.error, OSError) as exc:
                with self.lock:
                    self.errors.append(str(exc))
                return

    def snapshot(self) -> tuple[list[tuple[str, int]], list[str]]:
        with self.lock:
            return list(self.questions), list(self.errors)

    def close(self) -> None:
        self.stop.set()
        self.thread.join(2)
        self.sock.close()
        if self.thread.is_alive():
            raise RuntimeError("DNS stub did not stop")


def ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def recv_exact(connection: socket.socket, length: int) -> bytes:
    data = b""
    while len(data) < length:
        part = connection.recv(length - len(data))
        if not part:
            raise RuntimeError("unexpected SOCKS EOF")
        data += part
    return data


def socks_request(port: int, host: str) -> None:
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        encoded = host.encode("ascii")
        destination = b"\x03" + bytes([len(encoded)]) + encoded
    else:
        destination = b"\x01" + address.packed
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(5)
        connection.sendall(b"\x05\x01\x00")
        if recv_exact(connection, 2) != b"\x05\x00":
            raise RuntimeError("SOCKS negotiation failed")
        connection.sendall(b"\x05\x01\x00" + destination + struct.pack("!H", 443))
        response = recv_exact(connection, 4)
        if response[0] != 5 or response[2] != 0 or response[1] > 8:
            raise RuntimeError(f"invalid SOCKS reply: {response.hex()}")
        if response[3] == 1:
            recv_exact(connection, 6)
        elif response[3] == 4:
            recv_exact(connection, 18)
        else:
            raise RuntimeError(f"unexpected SOCKS reply address type: {response[3]}")
        # Mihomo acknowledges CONNECT before outbound matching. Send data to exercise
        # the tunnel; REJECT must then close it, and the real match log is required below.
        if response[1] == 0:
            try:
                connection.sendall(b"fixture")
                if connection.recv(1):
                    raise RuntimeError("unexpected data from a REJECT-only fixture")
            except (ConnectionResetError, BrokenPipeError):
                # A reset is a valid REJECT close, not evidence of the expected rule.
                pass


def find_match(log: str, host: str) -> tuple[str, str] | None:
    for line in log.splitlines():
        quoted = re.search(r'msg=("(?:\\.|[^"\\])*")', line)
        if quoted is None:
            continue
        message = json.loads(quoted[1])
        match = re.search(r"--> " + re.escape(host) + r":443 match (.+?) using (.+?)\[REJECT\]$", message)
        if match:
            return match[1], match[2]
    return None


# TEST-NET-3 literal outside every fixture provider: only the terminal MATCH covers it, without DNS.
WARMUP_HOST = "203.0.113.254"


def wait_for_tunnel(process: subprocess.Popen, socks: int, log_path: Path) -> None:
    """Provider counts can be ready before the tunnel handles traffic.

    Until then Mihomo still acknowledges SOCKS but drops the connection without a match log,
    so retry a warm-up request until the core actually logs a rule decision.
    """
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("core exited before tunnel readiness")
        try:
            socks_request(socks, WARMUP_HOST)
        except (OSError, RuntimeError):
            pass
        probe = time.monotonic() + 0.5
        while time.monotonic() < probe:
            match = find_match(log_path.read_text(encoding="utf-8"), WARMUP_HOST)
            if match is not None:
                if match[0] != "Match":
                    raise RuntimeError(f"warm-up literal matched {match}, expected terminal Match")
                return
            time.sleep(0.02)
    raise RuntimeError("core tunnel readiness timed out")


def run_core(core: Path, mmdb: Path, source: dict, fixture: dict, cases: list[dict]) -> list[dict]:
    dns = CountingDNS(fixture["cases"])
    try:
        with tempfile.TemporaryDirectory(prefix="mihomo-rule-regression-") as temp:
            directory = Path(temp)
            socks, controller = ports()
            config, counts = build_fixture(source, fixture, directory, socks, controller, dns.port)
            shutil.copyfile(mmdb, directory / "Country.mmdb")
            config_path = directory / "config.yaml"
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
            log_path = directory / "core.log"
            with log_path.open("w") as log:
                process = subprocess.Popen([str(core), "-d", str(directory), "-f", str(config_path)], stdout=log, stderr=subprocess.STDOUT)
                try:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise RuntimeError("core exited before readiness")
                        try:
                            with opener.open(f"http://127.0.0.1:{controller}/providers/rules", timeout=1) as response:
                                loaded = json.load(response)["providers"]
                            # The controller can open before file providers initialize.
                            if loaded is None:
                                loaded = {}
                            if not isinstance(loaded, dict):
                                raise ValueError("invalid providers API response")
                            if all(name in loaded and loaded[name].get("ruleCount") == count for name, count in counts.items()):
                                break
                        except (urllib.error.URLError, TimeoutError, OSError):
                            # Only transport readiness errors may retry; bad API/JSON fails explicitly.
                            pass
                        time.sleep(0.02)
                    else:
                        raise RuntimeError("core/provider readiness timed out")
                    wait_for_tunnel(process, socks, log_path)
                    observations = []
                    for case in cases:
                        before, errors = dns.snapshot()
                        if errors:
                            raise RuntimeError(f"DNS stub failed: {errors}")
                        socks_request(socks, case["host"])
                        deadline = time.monotonic() + 3
                        match = None
                        while time.monotonic() < deadline:
                            match = find_match(log_path.read_text(encoding="utf-8"), case["host"])
                            if match is not None:
                                break
                            if process.poll() is not None:
                                raise RuntimeError("core exited during request")
                            time.sleep(0.01)
                        if match is None:
                            raise RuntimeError(f"no real-core match log for {case['host']}")
                        after, errors = dns.snapshot()
                        if errors:
                            raise RuntimeError(f"DNS stub failed: {errors}")
                        observations.append({"host": case["host"], "rule": match[0], "policy": match[1],
                                             "questions": after[len(before):]})
                    return observations
                except Exception as exc:
                    raise RuntimeError(f"{exc}\nCore log:\n{log_path.read_text(encoding='utf-8')[-5000:]}") from exc
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                            raise RuntimeError("core did not stop on SIGTERM")
    finally:
        dns.close()


def mismatches(case: dict, observation: dict) -> list[str]:
    expected = {"rule": case["rule"], "policy": "fixture:" + case["policy"],
                "questions": [(case["host"], 1)] * case["dns"]}
    return [f"{key}: expected {value!r}, got {observation[key]!r}" for key, value in expected.items()
            if observation[key] != value]


def mutated(source: dict, variant: str) -> tuple[dict, str]:
    candidate = copy.deepcopy(source)
    rules = candidate["rules"]
    if variant == "missing-lan-fallback":
        rules.remove("RULE-SET,Lan,DIRECT")
        host = "unlisted-lan.test"
    elif variant == "early-resolution":
        index = rules.index("RULE-SET,LocalNetwork,DIRECT,no-resolve")
        rules[index] = "RULE-SET,LocalNetwork,DIRECT"
        host = "known-ai.test"
    elif variant == "priority-swap":
        first = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,GitHub,") and rule.endswith(",no-resolve"))
        second = next(i for i, rule in enumerate(rules) if rule.startswith("RULE-SET,Developer,") and rule.endswith(",no-resolve"))
        rules[first], rules[second] = rules[second], rules[first]
        host = "priority.test"
    else:
        raise ValueError(f"unknown mutation {variant}")
    return candidate, host


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--mmdb", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    core, mmdb = args.core.resolve(), args.mmdb.resolve()
    if not core.is_file() or not mmdb.is_file():
        parser.error("core and mmdb must be existing files")
    source, fixture = load_yaml(args.config), load_yaml(FIXTURE)
    cases = fixture["cases"]
    report = {"core_sha256": hashlib.sha256(core.read_bytes()).hexdigest(),
              "mmdb_sha256": hashlib.sha256(mmdb.read_bytes()).hexdigest(),
              "production_config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
              "runtime": "isolated redir-host, no sniffer/TUN, file providers, REJECT-only groups",
              "baseline": run_core(core, mmdb, source, fixture, cases), "mutations": {}}
    for case, observation in zip(cases, report["baseline"], strict=True):
        errors = mismatches(case, observation)
        if errors:
            raise AssertionError(f"{case['host']}: {'; '.join(errors)}")
    for variant in ("missing-lan-fallback", "early-resolution", "priority-swap"):
        candidate, host = mutated(source, variant)
        case = next(case for case in cases if case["host"] == host)
        observation = run_core(core, mmdb, candidate, fixture, [case])[0]
        errors = mismatches(case, observation)
        if not errors:
            raise AssertionError(f"mutation was not detected: {variant}")
        report["mutations"][variant] = {"observation": observation, "detected_by": errors}
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Real-core rule/DNS regression passed: {len(cases)} cases, 3 detected semantic mutations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
