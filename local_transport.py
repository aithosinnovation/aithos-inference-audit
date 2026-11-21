"""Constrain upstream urllib traffic to one in-process loopback fixture.

The upstream source remains byte-for-byte unchanged. This scoped adapter ignores
all inherited proxy settings and refuses redirects or any non-fixture request.
"""
from contextlib import contextmanager
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@contextmanager
def fixture_only_http(base_url):
    base = urlsplit(base_url)
    if (base.scheme != "http" or base.hostname != "127.0.0.1" or base.port is None
            or base.netloc != f"127.0.0.1:{base.port}" or base.path or base.query or base.fragment):
        raise ValueError("fixture origin must be exact loopback HTTP")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    permitted = {"/aithos-fixture": "GET", "/v1/models": "GET",
                 "/v1/chat/completions": "POST"}

    def open_fixture(url, *args, **kwargs):
        raw = url.full_url if isinstance(url, urllib.request.Request) else url
        parsed = urlsplit(raw)
        method = url.get_method() if isinstance(url, urllib.request.Request) else "GET"
        if (parsed.scheme != "http" or parsed.netloc != base.netloc or parsed.query or parsed.fragment
                or permitted.get(parsed.path) != method):
            raise ValueError("network request outside controlled fixture")
        return opener.open(url, *args, **kwargs)

    with patch.object(urllib.request, "urlopen", open_fixture):
        yield
