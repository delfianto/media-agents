"""OpenSubtitles.com REST API client -- stdlib `urllib` only.

Needs an API key (register an "API Consumer" in an opensubtitles.com
account's profile). Logging in with a username/password is optional but
raises the daily download quota well above the anonymous ceiling of 5
downloads/24h/IP; see organize/../../notes/organize-apis.md for the exact tiers and the full
endpoint reference this was built against.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from psammophis.runtime.filesystem import atomic_write_bytes

BASE_URL = "https://api.opensubtitles.com/api/v1"


class OpenSubtitlesError(RuntimeError):
    pass


class OpenSubtitlesClient:
    def __init__(
        self,
        api_key: str,
        user_agent: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 15.0,
    ):
        self.api_key = api_key
        self.user_agent = user_agent
        self.username = username
        self.password = password
        self.timeout = timeout
        self._token: str | None = None

    def _headers(self, authenticated: bool) -> dict[str, str]:
        headers = {
            "Api-Key": self.api_key,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        authenticated: bool = False,
    ) -> dict:
        url = f"{BASE_URL}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += f"?{urllib.parse.urlencode(clean)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(authenticated), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise OpenSubtitlesError(
                f"OpenSubtitles {path} failed ({exc.code}): {detail[:300]}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OpenSubtitlesError(f"OpenSubtitles {path} failed: {reason}") from exc

    def login(self) -> None:
        """No-op (stays on the anonymous 5/day/IP quota) if no
        username/password was configured -- login is an optional quota
        boost, not a requirement to use this client at all."""
        if not self.username or not self.password:
            return
        data = self._request(
            "POST", "/login", body={"username": self.username, "password": self.password}
        )
        token = data.get("token")
        if not token:
            raise OpenSubtitlesError("login succeeded but no token was returned")
        self._token = token

    def search(
        self,
        *,
        tmdb_id: int | None = None,
        imdb_id: str | None = None,
        query: str | None = None,
        moviehash: str | None = None,
        languages: str = "en",
        season_number: int | None = None,
        episode_number: int | None = None,
        media_type: str | None = None,
    ) -> list[dict]:
        params: dict[str, object] = {
            "tmdb_id": tmdb_id,
            "imdb_id": imdb_id,
            "query": query,
            "moviehash": moviehash,
            "languages": languages,
            "season_number": season_number,
            "episode_number": episode_number,
            "type": media_type,
        }
        data = self._request("GET", "/subtitles", params=params, authenticated=True)
        return data.get("data", [])

    def download(self, file_id: int) -> dict:
        return self._request("POST", "/download", body={"file_id": file_id}, authenticated=True)

    def download_to(self, file_id: int, dest: str | Path) -> str:
        info = self.download(file_id)
        link = info.get("link")
        if not link:
            raise OpenSubtitlesError(f"no download link returned for file_id={file_id}")
        req = urllib.request.Request(link, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                atomic_write_bytes(Path(dest), resp.read())
        except urllib.error.HTTPError as exc:
            raise OpenSubtitlesError(
                f"subtitle download failed ({exc.code}) for file_id={file_id}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OpenSubtitlesError(
                f"subtitle download failed for file_id={file_id}: {reason}"
            ) from exc
        return str(info.get("file_name", ""))
