from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from src.app_store.aur.errors import AurInvalidResponse, AurNetworkError, AurRpcError
from src.app_store.aur.rpc import AurRpcClient


class Response:
    def __init__(self, payload, *, content_type="application/json"):
        self.data = json.dumps(payload).encode()
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(self.data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.data[:limit]


def package_payload(name="demo-git"):
    return {
        "Name": name,
        "PackageBase": name,
        "Version": "2.0-1",
        "Description": "Demo package",
        "URL": "https://example.test/demo",
        "NumVotes": 7,
        "Popularity": 1.25,
        "OutOfDate": None,
        "Maintainer": "maintainer",
        "FirstSubmitted": 100,
        "LastModified": 200,
        "Depends": ["glibc"],
        "License": ["MIT"],
        "Keywords": ["demo"],
    }


def test_search_builds_v5_path_and_validates_package(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.headers, timeout))
        return Response({"version": 5, "type": "search", "resultcount": 1, "results": [package_payload()]})

    import src.app_store.aur.rpc as rpc_module
    monkeypatch.setattr(rpc_module, "urlopen", fake_urlopen)

    result = AurRpcClient(timeout=3).search("demo git")

    assert result[0].name == "demo-git"
    assert result[0].depends == ("glibc",)
    assert result[0].licenses == ("MIT",)
    assert result[0].aur_url.endswith("/packages/demo-git")
    assert "/rpc/v5/search/demo%20git?by=name-desc" in calls[0][0]
    assert calls[0][2] == 3


def test_search_rejects_short_query_without_network():
    with pytest.raises(ValueError, match="at least 2"):
        AurRpcClient().search("x")


def test_info_is_batched_and_preserves_names(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        names = []
        if "one" in request.full_url:
            names.append("one")
        if "two" in request.full_url:
            names.append("two")
        return Response({
            "version": 5,
            "type": "multiinfo",
            "resultcount": len(names),
            "results": [package_payload(name) for name in names],
        })

    import src.app_store.aur.rpc as rpc_module
    monkeypatch.setattr(rpc_module, "urlopen", fake_urlopen)

    result = AurRpcClient(info_chunk_size=1).info(["one", "two"])

    assert [package.name for package in result] == ["one", "two"]
    assert len(calls) == 2
    assert all("arg%5B%5D=" in url for url in calls)


def test_rpc_maps_network_failure(monkeypatch):
    def fake_urlopen(request, timeout):
        raise URLError("offline")

    import src.app_store.aur.rpc as rpc_module
    monkeypatch.setattr(rpc_module, "urlopen", fake_urlopen)

    with pytest.raises(AurNetworkError, match="offline"):
        AurRpcClient().search("demo")


def test_rpc_maps_server_error_payload(monkeypatch):
    def fake_urlopen(request, timeout):
        return Response({"version": 5, "type": "error", "resultcount": 0, "results": [], "error": "bad query"})

    import src.app_store.aur.rpc as rpc_module
    monkeypatch.setattr(rpc_module, "urlopen", fake_urlopen)

    with pytest.raises(AurRpcError, match="bad query"):
        AurRpcClient().search("demo")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 4, "type": "search", "resultcount": 0, "results": []},
        {"version": 5, "type": "multiinfo", "resultcount": 0, "results": []},
        {"version": 5, "type": "search", "resultcount": 2, "results": []},
        {"version": 5, "type": "search", "resultcount": 1, "results": [{}]},
    ],
)
def test_rpc_rejects_invalid_payloads(monkeypatch, payload):
    def fake_urlopen(request, timeout):
        return Response(payload)

    import src.app_store.aur.rpc as rpc_module
    monkeypatch.setattr(rpc_module, "urlopen", fake_urlopen)

    with pytest.raises(AurInvalidResponse):
        AurRpcClient().search("demo")


def test_info_rejects_malformed_package_name_without_network():
    with pytest.raises(ValueError, match="invalid AUR package name"):
        AurRpcClient().info(["--bad"])
