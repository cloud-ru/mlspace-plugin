from __future__ import annotations

import json

import httpx

from mlspace_mcp.errors import error_from_response


def test_422_detail_list_is_made_readable():
    resp = httpx.Response(
        422,
        json={"detail": [{"loc": ["body", "image", "type"], "msg": "Field required", "type": "missing"}]},
    )
    err = error_from_response(resp)
    assert err.status == 422
    assert "image.type" in err.message
    assert "Field required" in err.message


def test_400_reason_shape_is_surfaced():
    # the shape MLSpace returned for the x_api_key role error
    resp = httpx.Response(400, json={"reason": "role model access checking error", "status": "INVALID_ARGUMENT"})
    err = error_from_response(resp)
    assert err.status == 400
    assert "role model access checking error" in err.message


def test_422_nested_detail_reason_is_surfaced():
    resp = httpx.Response(422, json={"detail": {"reason": "bad thing happened"}})
    err = error_from_response(resp)
    assert "bad thing happened" in err.message


def test_404_falls_back_to_raw_body():
    resp = httpx.Response(404, text="not found plain text")
    err = error_from_response(resp)
    assert err.status == 404
    assert "not found plain text" in err.message


def test_request_credentials_never_reach_the_error_text():
    """Secrets the wrapper sent (token, x-api-key, a body password) stay out of errors.

    Upstream-echoed response bodies are a separate question; this pins the request side.
    """
    request = httpx.Request(
        "POST", "https://api.test.local/public/v2/data_transfer/v2/connectors",
        headers={"Authorization": "Bearer SYNTH-TOKEN-7f3a", "x-api-key": "SYNTH-KEY-91c2"},
        json={"name": "c", "password": "SYNTH-PASS-5d0e"},
    )
    for status in (400, 403, 422, 500):
        text = error_from_response(
            httpx.Response(status, json={"detail": "rejected"}, request=request)).to_text()
        for secret in ("SYNTH-TOKEN-7f3a", "SYNTH-KEY-91c2", "SYNTH-PASS-5d0e"):
            assert secret not in text, (status, secret)


# HTML error page normalisation ------------------------------------------------

# verbatim live dalle/inference 404 body: an HTML page wrapped in a JSON string
_HTML_404 = (
    "<!doctype html>\n<html lang=en>\n<title>404 Not Found</title>\n<h1>Not Found</h1>\n"
    "<p>The requested URL was not found on the server. If you entered the URL manually "
    "please check your spelling and try again.</p>\n"
)


def test_404_html_in_json_string_normalised():
    resp = httpx.Response(
        404, text=json.dumps(_HTML_404), headers={"content-type": "application/json"}
    )
    err = error_from_response(resp)
    assert err.status == 404
    assert "html" in err.message.lower()
    assert "does not distinguish" in err.message.lower()
    # the raw page is not dumped into the message
    assert "check your spelling" not in err.message.lower()
    assert "<!doctype" not in err.message
    assert err.message.startswith("Not found (404).")


def test_raw_text_html_normalised():
    resp = httpx.Response(404, text=_HTML_404, headers={"content-type": "text/html"})
    err = error_from_response(resp)
    assert "html" in err.message.lower()
    assert "<!doctype" not in err.message


def test_404_structured_detail_preserved():
    # regression: a structured JSON detail is preferred, HTML note does not fire
    resp = httpx.Response(404, json={"detail": "resource is not found"})
    err = error_from_response(resp)
    assert "is not found" in err.message
    assert "generic html" not in err.message.lower()


def test_445_waf_recognised():
    resp = httpx.Response(
        445, text="<html><head><title>Blocked</title></head>" + "x" * 7800,
        headers={"content-type": "text/html"},
    )
    err = error_from_response(resp)
    assert err.status == 445
    blob = (err.message + " " + (err.hint or "")).lower()
    assert "firewall" in blob or "security" in blob
    assert "do not retry" in (err.hint or "").lower()
    assert "<html" not in err.message
    assert len(err.to_text()) < 400
