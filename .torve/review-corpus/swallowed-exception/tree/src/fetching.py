def fetch(url, download, attempts=3):
    for _ in range(attempts):
        try:
            return download(url)
        except Exception:
            pass
    return None
