"""One verified TLS policy for setup and runtime; no global SSL monkey patch."""
from __future__ import annotations

import os
import ssl

import httpx


def tls_context(ca_file: str = '') -> ssl.SSLContext:
    """Use OS trust and optionally an administrator-supplied PEM bundle."""
    extra = ca_file or os.environ.get('SSL_CERT_FILE', '')
    context: ssl.SSLContext
    try:
        import truststore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        context = ssl.create_default_context()
    if extra:
        context.load_verify_locations(cafile=extra)
    capath = os.environ.get('SSL_CERT_DIR')
    if capath:
        context.load_verify_locations(capath=capath)
    return context


def safe_error(exc: Exception) -> str:
    """Describe the cause without printing exception bodies or submitted values."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, ssl.SSLCertVerificationError) or
           (isinstance(item, (ssl.SSLError, httpx.ConnectError)) and
            'CERTIFICATE_VERIFY_FAILED' in str(item)) for item in chain):
        return ('Не удалось проверить TLS-сертификат API. Получите доверенный корпоративный CA-сертификат у '
                'администратора и укажите файл PEM: --ca-file /путь/к/ca.pem. Проверка TLS остаётся включённой.')
    status = getattr(exc, 'status', None)
    if isinstance(status, int):
        return f'Запрос к API завершился ошибкой (HTTP {status}). Проверьте ключи доступа и права на выбранные воркспейсы.'
    if any(isinstance(item, httpx.TimeoutException) for item in chain):
        return 'API не ответил вовремя. Проверьте сеть и VPN, затем повторите setup.'
    if any(isinstance(item, httpx.ConnectError) for item in chain):
        return 'Не удалось подключиться к API. Проверьте сеть, VPN и адрес API (--base-url).'
    return (f'Не удалось выполнить запрос ({type(exc).__name__}). Проверьте адрес API, '
            'сеть, VPN и доступность указанного CA-файла, затем повторите настройку.')
