"""Real local TestClient route checks; no listening server or provider acceptance."""
from tests.hybrid.conftest import harness  # noqa: F401; register the isolated fixture


def test_public_shell_assets_and_authenticated_api(harness):
    client, _, _ = harness
    client.headers.clear()
    for path, media in (("/", "text/html"), ("/hybrid/", "text/html"),
                        ("/hybrid/styles.css", "text/css"), ("/hybrid/app.mjs", "text/javascript"),
                        ("/hybrid/client.mjs", "text/javascript"),
                        ("/protocol/canonical-json.mjs", "text/javascript")):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["content-type"].startswith(media)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert "connect-src 'self'" in response.headers["content-security-policy"]
    page = client.get("/").text
    assert 'src="/hybrid/app.mjs"' in page
    assert 'href="/hybrid/styles.css"' in page
    assert 'lang="vi"' in page
    assert "Đăng ký approval" in page
    assert client.get("/api/requests").status_code == 401
    assert client.get("/api/providers/status").status_code == 401
    assert client.get("/hybrid/app.mjs", headers={"Origin": "https://external.invalid"}).status_code == 403
    assert client.post("/hybrid/app.mjs").status_code == 401
