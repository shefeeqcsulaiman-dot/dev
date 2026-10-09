"""DigitalOcean's managed Redis gives a TLS rediss:// URL without ssl_cert_reqs, which
Celery's Redis result backend rejects; app.worker adds the safe default."""
from celery import Celery

from app.worker import celery_redis_url


def test_rediss_url_gets_cert_check_and_celery_accepts_it():
    url = celery_redis_url("rediss://default:secret@db.example.com:25061")
    assert url.endswith("?ssl_cert_reqs=required")
    assert type(Celery("t", broker=url, backend=url).backend).__name__ == "RedisBackend"


def test_other_urls_unchanged():
    assert celery_redis_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
    assert celery_redis_url("memory://") == "memory://"
    assert celery_redis_url("rediss://h:1/0?ssl_cert_reqs=none") == "rediss://h:1/0?ssl_cert_reqs=none"
    assert celery_redis_url("rediss://h:1/0?db=1") == "rediss://h:1/0?db=1&ssl_cert_reqs=required"
