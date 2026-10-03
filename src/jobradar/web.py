import httpx

USER_AGENT = "Mozilla/5.0 (compatible; job-radar/0.1; personal job alerts)"


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
