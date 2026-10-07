#!/usr/bin/env python3
"""Offline byte-comparison, failure-path and generator-count regression tests."""
from __future__ import annotations

import contextlib
import copy
import email.message
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_surge_modules as build
import check_remote_resources as check


class Response:
    def __init__(self, url: str, body: bytes, status: int = 200, content_type: str = "text/plain",
                 length: str | None = None) -> None:
        self.url, self.body, self.status = url, body, status
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        if length is not None:
            self.headers["Content-Length"] = length

    def read(self, limit: int) -> bytes:
        return self.body[:limit]

    def geturl(self) -> str:
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_complete_download() -> None:
    resource = check.Resource("https://scripts.example/fixed.js", "script", "surge/fixture")
    body = b"// fixture\n" + b"a" * 6000
    def opened(request, timeout):
        assert request.get_header("Range") is None
        return Response(request.full_url, body, length=str(len(body)))
    with patch.object(check.urllib.request, "urlopen", side_effect=opened):
        result = check.fetch(resource, "full", 1, 0, complete=True)
        assert result.ok and result.body == body and result.bytes_read > check.LIGHT_BYTES
    negatives = [
        Response(resource.url, b""), Response(resource.url, body, 206),
        Response(resource.url, b"<html>error</html>", content_type="text/html"),
        Response(resource.url, b"\xff"), Response(resource.url + "?version=master", body),
        Response(resource.url, body, length=str(len(body) + 1)),
        Response(resource.url, body, length="invalid"),
    ]
    for response in negatives:
        with patch.object(check.urllib.request, "urlopen", return_value=response):
            assert not check.fetch(resource, "full", 1, 0, complete=True).ok, response.body[:20]
    with patch.object(check.urllib.request, "urlopen", side_effect=TimeoutError("fixture timeout")):
        assert not check.fetch(resource, "full", 1, 0, complete=True).ok
    with patch.object(check, "FULL_LIMIT", 8), \
         patch.object(check.urllib.request, "urlopen", return_value=Response(resource.url, body)):
        assert not check.fetch(resource, "full", 1, 0, complete=True).ok


def test_targets_and_pin_counts() -> None:
    targets = check.script_update_targets()
    assert len(targets) == 3 and len({t.pinned for t in targets}) == 3
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / "qx.conf"
        config.write_text("resource_parser_url = " + targets[0].pinned.replace("38a6fe02eb7cc67efd26a8f3c618bd031f1885b4", "a" * 40) + "\n")
        source = copy.deepcopy(next(s for s in build.SOURCES if s["file"] == "youtube"))
        source["pins"] = {old: new.replace("65075cdb388fc5e3094afd7e7314c67b243f3525", "b" * 40)
                          for old, new in source["pins"].items()}
        with patch.object(check, "QX_FILES", (config,)), patch.object(build, "SOURCES", (source,)):
            derived = check.script_update_targets()
            assert "a" * 40 in derived[0].pinned and all("b" * 40 in t.pinned for t in derived[1:])
            assert [t.candidate for t in derived] == [t.candidate for t in targets]
            config.write_text("resource_parser_url = https://scripts.example/master.js\n")
            try:
                check.script_update_targets()
            except ValueError:
                pass
            else:
                raise AssertionError("mutable parser accepted as pinned")
    source = copy.deepcopy(next(s for s in build.SOURCES if s["file"] == "youtube"))
    request, response = list(source["pins"])
    def run(request_count):
        lines = [f"request{i} = script-path={request}" for i in range(request_count)]
        body = ("[Script]\n" + "\n".join(lines) + f"\nresponse = script-path={response}\n").encode()
        with patch.object(build, "SOURCES", (source,)), patch.object(build, "load", return_value=("fixture", body)):
            return build.load_sources()[0][2]["Script"]
    assert len(run(2)) == 3
    for count in [0, 1, 3]:
        try:
            run(count)
        except build.SourceError:
            pass
        else:
            raise AssertionError(f"request occurrence drift accepted: {count}")
    source["pin_counts"] = {}
    assert len(run(1)) == 2
    for counts in [{request: 0}, {request: True}, {"unknown": 2}]:
        source["pin_counts"] = counts
        try:
            run(1)
        except build.SourceError:
            pass
        else:
            raise AssertionError(f"invalid pin_counts accepted: {counts}")


def test_main_comparisons() -> None:
    targets = check.script_update_targets()
    resources = [check.Resource(t.pinned, "script", t.source) for t in targets]
    pinned_body = b"// unchanged file despite unrelated upstream commits\n" + b"a" * 6000
    bodies = {t.candidate: pinned_body for t in targets}
    calls = []
    def fetched(resource, mode, timeout, retries, complete=False):
        calls.append((resource.url, complete))
        body = bodies.get(resource.url, pinned_body)
        return check.Result(resource, bool(body), 200, "text/plain", resource.url, len(body),
                            "ok" if body else "fixture download failed", body if complete else b"")
    with tempfile.TemporaryDirectory() as temp, \
         patch.object(check, "extract_resources", return_value=resources), \
         patch.object(check, "fetch", side_effect=fetched), \
         patch.object(check, "mihomo_content_report", return_value=([], [], [])), \
         patch.object(check, "reject_overlap_report", return_value=([], [])):
        summary = Path(temp) / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(sys, "argv", ["check", "--mode", "light"]):
                assert check.main() == 0 and len(calls) == 3 and not any(c for _, c in calls)
                assert not summary.exists()
            calls.clear()
            with patch.object(sys, "argv", ["check", "--mode", "full"]):
                assert check.main() == 0 and len(calls) == 6 and all(c for _, c in calls)
                assert len({url for url, _ in calls}) == 6  # pinned bodies reused, no double fetch
                assert summary.read_text().count("| unchanged |") == 3
                assert hashlib.sha256(pinned_body).hexdigest() in summary.read_text()
                bodies[targets[1].candidate] = pinned_body[:-1] + b"b"  # past light-check prefix
                assert check.main() == 0 and "[changed]" in output.getvalue()
                assert f"first differing byte {len(pinned_body) - 1}" in output.getvalue()
                bodies[targets[2].candidate] = b""
                assert check.main() == 1 and "not compared" in output.getvalue()
                del bodies[targets[2].candidate]
                bodies[targets[0].pinned] = b""
                assert check.main() == 1
                del bodies[targets[0].pinned]
                with patch.object(check, "script_update_targets", side_effect=ValueError("fixture bad pin")):
                    assert check.main() == 1 and "fixture bad pin" in summary.read_text()


def main() -> None:
    test_complete_download()
    test_targets_and_pin_counts()
    test_main_comparisons()
    print("Script update offline tests passed: complete bytes, full/light, failed comparisons and exact pin counts.")


if __name__ == "__main__":
    main()
