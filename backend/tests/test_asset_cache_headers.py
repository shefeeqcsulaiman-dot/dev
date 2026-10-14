"""A versioned asset (?v=...) is cached "forever" only when the version is the file's own
(first 10 hex of its SHA-256, as inject-asset-versions.mjs stamps it). During a deploy an
old instance can be asked for the new page's URL; its old file must not be cached
"forever" under it by the CDN."""
import hashlib

from app.main import static_dir


def _version(name):
    return hashlib.sha256((static_dir / "taxflow" / "src" / name).read_bytes()).hexdigest()[:10]


def test_matching_version_is_cached_forever(client):
    for name in ("styles.css", "ess.css", "app.js"):
        r = client.get(f"/taxflow/src/{name}", params={"v": _version(name)})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable", name


def test_other_version_is_not_cached(client):
    for path in ("/taxflow/src/styles.css", "/src/ess.css"):
        r = client.get(path, params={"v": "0000000000"})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache", path
