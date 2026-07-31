"""Minimal TMDB (The Movie Database) API v3 client -- stdlib `urllib` only.

A free API key (themoviedb.org account -> Settings -> API) is all this
needs, for both movies and TV shows -- unlike TheTVDB, which stopped
offering a no-strings-attached free tier for API access (see
../../notes/organize-apis.md). That's the whole reason this skill leans on TMDB alone
rather than also integrating TheTVDB.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from psammophis.runtime.filesystem import atomic_write_bytes

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p"


class TmdbError(RuntimeError):
    pass


class TmdbClient:
    def __init__(self, api_key: str, user_agent: str, timeout: float = 15.0):
        self.api_key = api_key
        self.user_agent = user_agent
        self.timeout = timeout

    def _get(self, path: str, **params: object) -> dict:
        query = {k: v for k, v in params.items() if v is not None}
        query["api_key"] = self.api_key
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise TmdbError(f"TMDB {path} failed ({exc.code}): {detail[:300]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TmdbError(f"TMDB {path} failed: {reason}") from exc

    def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        data = self._get("/search/movie", query=query, year=year)
        return data.get("results", [])

    def search_tv(self, query: str, first_air_date_year: int | None = None) -> list[dict]:
        data = self._get("/search/tv", query=query, first_air_date_year=first_air_date_year)
        return data.get("results", [])

    def movie_details(self, tmdb_id: int) -> dict:
        return self._get(f"/movie/{tmdb_id}", append_to_response="external_ids,credits")

    def tv_details(self, tmdb_id: int) -> dict:
        return self._get(f"/tv/{tmdb_id}", append_to_response="external_ids")

    def episode_details(self, tv_id: int, season: int, episode: int) -> dict:
        return self._get(
            f"/tv/{tv_id}/season/{season}/episode/{episode}", append_to_response="credits"
        )

    @staticmethod
    def image_url(image_path: str | None, size: str = "original") -> str | None:
        if not image_path:
            return None
        return f"{IMAGE_BASE_URL}/{size}{image_path}"

    def download_image(self, image_path: str, dest: str | Path, size: str = "original") -> bool:
        url = self.image_url(image_path, size)
        if not url:
            return False
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError, urllib.error.URLError, TimeoutError:
            return False
        atomic_write_bytes(Path(dest), data)
        return True
