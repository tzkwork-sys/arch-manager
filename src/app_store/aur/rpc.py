from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .errors import AurInvalidResponse, AurNetworkError, AurRpcError
from .local_state import valid_package_name
from .models import AurPackage


AUR_BASE_URL = "https://aur.archlinux.org"
AUR_RPC_VERSION = 5
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_INFO_CHUNK_SIZE = 100
USER_AGENT = "ArchManager-AppStore-AUR/1.0"


def _string(value: Any, *, required: bool = False, field: str = "value") -> str:
    if isinstance(value, str):
        text = value.strip()
        if text or not required:
            return text
    if required:
        raise AurInvalidResponse(f"missing or invalid {field}")
    return ""


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise AurInvalidResponse(f"invalid {field}")
    if isinstance(value, int):
        return value
    raise AurInvalidResponse(f"invalid {field}")


def _number(value: Any, *, field: str, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AurInvalidResponse(f"invalid {field}")
    return float(value)


def _integer(value: Any, *, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise AurInvalidResponse(f"invalid {field}")
    return value


def _tuple_strings(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AurInvalidResponse(f"invalid {field}")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise AurInvalidResponse(f"invalid {field} item")
        text = item.strip()
        if text:
            result.append(text)
    return tuple(result)


def package_from_rpc(item: Any) -> AurPackage:
    if not isinstance(item, dict):
        raise AurInvalidResponse("AUR package entry is not an object")
    name = _string(item.get("Name"), required=True, field="Name")
    package_base = _string(item.get("PackageBase"), required=True, field="PackageBase")
    version = _string(item.get("Version"), required=True, field="Version")
    if not valid_package_name(name) or not valid_package_name(package_base):
        raise AurInvalidResponse("invalid AUR package name")
    return AurPackage(
        name=name,
        package_base=package_base,
        version=version,
        description=_string(item.get("Description")),
        upstream_url=_optional_string(item.get("URL")),
        aur_url=f"{AUR_BASE_URL}/packages/{quote(name, safe='@._+:-')}",
        maintainer=_optional_string(item.get("Maintainer")),
        votes=_integer(item.get("NumVotes"), field="NumVotes"),
        popularity=_number(item.get("Popularity"), field="Popularity"),
        out_of_date=_optional_int(item.get("OutOfDate"), field="OutOfDate"),
        first_submitted=_optional_int(item.get("FirstSubmitted"), field="FirstSubmitted"),
        last_modified=_optional_int(item.get("LastModified"), field="LastModified"),
        depends=_tuple_strings(item.get("Depends"), field="Depends"),
        make_depends=_tuple_strings(item.get("MakeDepends"), field="MakeDepends"),
        opt_depends=_tuple_strings(item.get("OptDepends"), field="OptDepends"),
        check_depends=_tuple_strings(item.get("CheckDepends"), field="CheckDepends"),
        conflicts=_tuple_strings(item.get("Conflicts"), field="Conflicts"),
        provides=_tuple_strings(item.get("Provides"), field="Provides"),
        replaces=_tuple_strings(item.get("Replaces"), field="Replaces"),
        licenses=_tuple_strings(item.get("License"), field="License"),
        keywords=_tuple_strings(item.get("Keywords"), field="Keywords"),
    )


class AurRpcClient:
    """Small validated aurweb RPC v5 client. It performs read-only HTTP GETs only."""

    def __init__(
        self,
        *,
        base_url: str = AUR_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        info_chunk_size: int = DEFAULT_INFO_CHUNK_SIZE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = max(0.5, float(timeout))
        self.max_response_bytes = max(64 * 1024, int(max_response_bytes))
        self.info_chunk_size = max(1, min(150, int(info_chunk_size)))

    def search(self, query: str, *, by: str = "name-desc") -> tuple[AurPackage, ...]:
        term = (query or "").strip()
        if len(term) < 2:
            raise ValueError("AUR search query must contain at least 2 characters")
        if by not in {
            "name", "name-desc", "maintainer", "comaintainers", "depends",
            "makedepends", "optdepends", "checkdepends", "provides",
            "conflicts", "replaces", "groups", "submitter",
        }:
            raise ValueError("unsupported AUR search field")
        encoded_term = quote(term, safe="")
        query_string = urlencode({"by": by})
        payload = self._get_json(f"{self.base_url}/rpc/v5/search/{encoded_term}?{query_string}")
        return self._parse_payload(payload, expected_type="search")

    def info(self, package_names: Iterable[str]) -> tuple[AurPackage, ...]:
        names = tuple(dict.fromkeys(name.strip() for name in package_names if name and name.strip()))
        if not names:
            return ()
        invalid = [name for name in names if not valid_package_name(name)]
        if invalid:
            raise ValueError(f"invalid AUR package name: {invalid[0]}")
        packages: list[AurPackage] = []
        for chunk in self._info_chunks(names):
            query_string = urlencode([("arg[]", name) for name in chunk])
            payload = self._get_json(f"{self.base_url}/rpc/v5/info?{query_string}")
            packages.extend(self._parse_payload(payload, expected_type="multiinfo"))
        return tuple(packages)

    def _info_chunks(self, names: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        # aurweb/nginx has a URI length limit below the generic HTTP maximum.
        # Keep a conservative ceiling while also honoring the count cap.
        prefix = f"{self.base_url}/rpc/v5/info?"
        max_url_length = 4000
        chunks: list[tuple[str, ...]] = []
        current: list[str] = []
        for name in names:
            candidate = [*current, name]
            query = urlencode([("arg[]", item) for item in candidate])
            if current and (len(candidate) > self.info_chunk_size or len(prefix) + len(query) > max_url_length):
                chunks.append(tuple(current))
                current = [name]
            else:
                current = candidate
        if current:
            chunks.append(tuple(current))
        return tuple(chunks)

    def _get_json(self, url: str) -> Any:
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type and content_type not in {"application/json", "text/json"}:
                    raise AurInvalidResponse(f"unexpected AUR response type: {content_type}")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > self.max_response_bytes:
                    raise AurInvalidResponse("AUR response is too large")
                data = response.read(self.max_response_bytes + 1)
        except HTTPError as exc:
            raise AurNetworkError(f"AUR HTTP error {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AurNetworkError(str(exc)) from exc
        if len(data) > self.max_response_bytes:
            raise AurInvalidResponse("AUR response is too large")
        if not data:
            raise AurInvalidResponse("empty AUR response")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AurInvalidResponse("AUR response is not valid JSON") from exc

    @staticmethod
    def _parse_payload(payload: Any, *, expected_type: str) -> tuple[AurPackage, ...]:
        if not isinstance(payload, dict):
            raise AurInvalidResponse("AUR RPC payload is not an object")
        if payload.get("version") != AUR_RPC_VERSION:
            raise AurInvalidResponse("unsupported AUR RPC version")
        response_type = payload.get("type")
        if response_type == "error":
            message = payload.get("error")
            raise AurRpcError(message if isinstance(message, str) and message else "AUR RPC error")
        if response_type != expected_type:
            raise AurInvalidResponse(f"unexpected AUR RPC type: {response_type!r}")
        results = payload.get("results")
        resultcount = payload.get("resultcount")
        if not isinstance(results, list) or isinstance(resultcount, bool) or not isinstance(resultcount, int):
            raise AurInvalidResponse("invalid AUR RPC result container")
        if resultcount != len(results):
            raise AurInvalidResponse("AUR RPC result count mismatch")
        return tuple(package_from_rpc(item) for item in results)
