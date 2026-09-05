"""HTTP method handling and disconnected clients."""

import json
import urllib.error
import urllib.request

import pytest

from agenthub import webapp


@pytest.mark.parametrize(
    "method,path,same_origin,status",
    [
        ("GET", "/api/run", True, 405),
        ("GET", "/api/state/", True, 404),
        ("POST", "/api/state", True, 405),
        ("POST", "/api/unknown", True, 404),
        ("POST", "/api/unknown", False, 401),
        ("POST", "/", True, 405),
    ],
)
def test_route_errors(
    server: str, method: str, path: str, same_origin: bool, status: int
) -> None:
    request = urllib.request.Request(
        f"{server}{path}",
        method=method,
        headers={"Sec-Fetch-Site": "same-origin"} if same_origin else {},
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == status
    assert isinstance(json.loads(error.value.read())["error"], str)
    if status == 401:
        assert error.value.headers["Connection"] == "close"


@pytest.mark.parametrize("path", ["/", "/api/usage/settings"])
def test_head_returns_get_headers_without_body(server: str, path: str) -> None:
    with urllib.request.urlopen(f"{server}{path}", timeout=5) as response:
        body = response.read()
        content_type = response.headers["Content-Type"]
    request = urllib.request.Request(f"{server}{path}", method="HEAD")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
        assert int(response.headers["Content-Length"]) == len(body)
        assert response.headers["Content-Type"] == content_type
        assert response.read() == b""


def test_broken_pipe_does_not_attempt_an_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = object.__new__(webapp.Handler)

    def disconnected(method: str) -> None:
        raise BrokenPipeError("client disconnected")

    def unexpected_error_response(status: int, message: str) -> None:
        pytest.fail("cannot send an error response to a disconnected client")

    monkeypatch.setattr(handler, "handle_route", disconnected)
    monkeypatch.setattr(handler, "send_error_json", unexpected_error_response)
    with pytest.raises(BrokenPipeError, match="client disconnected"):
        handler.dispatch("GET")
