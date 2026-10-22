import ssl

import httpx
import pytest

from mlspace_mcp import init_cli


def test_tls_cause_is_actionable_without_echo():
    cause = ssl.SSLCertVerificationError(1, 'certificate verify failed secret-value')
    transport = httpx.ConnectError('echo secret-value')
    transport.__cause__ = cause
    outer = RuntimeError('echo secret-value')
    outer.__cause__ = transport
    result = init_cli._safe_error(outer)
    assert 'TLS' in result
    assert '--ca-file' in result
    assert 'secret-value' not in result


def test_context_verifies_certificates_and_hostname():
    from mlspace_mcp.tls import tls_context
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_invalid_ca_fails_closed(tmp_path):
    from mlspace_mcp.tls import tls_context
    bad = tmp_path / 'bad.pem'
    bad.write_text('not a certificate')
    with pytest.raises((ssl.SSLError, ValueError)):
        tls_context(str(bad))


def test_real_https_accepts_trusted_ca_and_rejects_wrong_ca_and_hostname(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import trustme

    from mlspace_mcp.tls import tls_context

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        def log_message(self, *args):
            pass

    ca, wrong = trustme.CA(), trustme.CA()
    leaf = ca.issue_cert('localhost')
    good_file, bad_file = tmp_path / 'good.pem', tmp_path / 'wrong.pem'
    ca.cert_pem.write_to_path(good_file)
    wrong.cert_pem.write_to_path(bad_file)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    leaf.configure_cert(context)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        with httpx.Client(verify=tls_context(str(good_file)), trust_env=False, timeout=3) as client:
            assert client.get(f'https://localhost:{port}').text == 'ok'
            with pytest.raises(httpx.ConnectError):
                client.get(f'https://127.0.0.1:{port}')
        with httpx.Client(verify=tls_context(str(bad_file)), trust_env=False, timeout=3) as client:
            with pytest.raises(httpx.ConnectError):
                client.get(f'https://localhost:{port}')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
