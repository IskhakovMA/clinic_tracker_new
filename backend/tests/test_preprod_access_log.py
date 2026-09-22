"""Pre-production hardening: patient-name search text stays out of access logs.

Regression test for audit lead NV-10: three list views send patient-name
search text as ?search=..., and uvicorn's default access log records the
full request target including the query string.
"""
import logging


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", path, "1.1", 200), None,
    )


def test_uvicorn_access_log_drops_query_strings(client):
    logger = logging.getLogger("uvicorn.access")
    record = _access_record("/api/notifications?search=%D0%A2%D0%B5%D1%81%D1%82%D0%BE%D0%B2&limit=100")
    assert all(f.filter(record) if hasattr(f, "filter") else f(record) for f in logger.filters)
    message = record.getMessage()
    assert "search=" not in message
    assert "/api/notifications" in message


def test_access_log_filter_leaves_plain_paths_alone(client):
    logger = logging.getLogger("uvicorn.access")
    record = _access_record("/api/patients")
    for f in logger.filters:
        f.filter(record)
    assert '"GET /api/patients HTTP/1.1" 200' in record.getMessage()
