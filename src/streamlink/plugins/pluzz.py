import logging
import re
from datetime import datetime
from urllib.parse import urlparse

from isodate import LOCAL as LOCALTIMEZONE

from streamlink.plugin import Plugin, PluginError, pluginmatcher
from streamlink.plugin.api import useragents, validate
from streamlink.stream.dash import DASHStream
from streamlink.stream.hls import HLSStream
from streamlink.utils.url import update_qsd

log = logging.getLogger(__name__)


@pluginmatcher(re.compile(r"""
    https?://(?:
        (?:www\.)?france\.tv/
        |
        (?:.+\.)?francetvinfo\.fr/
    )
""", re.VERBOSE))
class Pluzz(Plugin):
    PLAYER_VERSION = "5.51.35"
    GEO_URL = "https://geoftv-a.akamaihd.net/ws/edgescape.json"
    API_URL = "https://player.webservices.francetelevisions.fr/v1/videos/{video_id}"

    _re_ftv_player_videos = re.compile(r"window\.FTVPlayerVideos\s*=\s*(?P<json>\[{.+?}])\s*;\s*(?:$|var)", re.DOTALL)
    _re_player_load = re.compile(r"""player\.load\s*\(\s*{\s*src\s*:\s*(['"])(?P<video_id>.+?)\1\s*}\s*\)\s*;""")

    def _get_streams(self):
        self.session.http.headers.update({
            "User-Agent": useragents.CHROME
        })
        CHROME_VERSION = re.compile(r"Chrome/(\d+)").search(useragents.CHROME).group(1)

        # Retrieve geolocation data
        country_code = self.session.http.get(self.GEO_URL, schema=validate.Schema(
            validate.parse_json(),
            {"reponse": {"geo_info": {
                "country_code": str
            }}},
            validate.get(("reponse", "geo_info", "country_code"))
        ))
        log.debug(f"Country: {country_code}")

        # Retrieve URL page and search for video ID
        video_id = None
        try:
            video_id = self.session.http.get(self.url, schema=validate.Schema(
                validate.parse_html(),
                validate.any(
                    validate.all(
                        validate.xml_xpath_string(".//script[contains(text(),'window.FTVPlayerVideos')][1]/text()"),
                        str,
                        validate.transform(self._re_ftv_player_videos.search),
                        validate.get("json"),
                        validate.parse_json(),
                        [{"videoId": str}],
                        validate.get((0, "videoId"))
                    ),
                    validate.all(
                        validate.xml_xpath_string(".//script[contains(text(),'new Magnetoscope')][1]/text()"),
                        str,
                        validate.transform(self._re_player_load.search),
                        validate.get("video_id")
                    ),
                    validate.all(
                        validate.xml_xpath_string(".//*[@id][contains(@class,'francetv-player-wrapper')][1]/@id"),
                        str
                    ),
                    validate.all(
                        validate.xml_xpath_string(".//*[@data-id][@class='magneto'][1]/@data-id"),
                        str
                    )
                )
            ))
        except PluginError:
            pass
        if not video_id:
            return
        log.debug(f"Video ID: {video_id}")

        api_url = update_qsd(self.API_URL.format(video_id=video_id), {
            "country_code": country_code,
            "w": 1920,
            "h": 1080,
            "player_version": self.PLAYER_VERSION,
            "domain": urlparse(self.url).netloc,
            "device_type": "mobile",
            "browser": "chrome",
            "browser_version": CHROME_VERSION,
            "os": "ios",
            "gmt": datetime.now(tz=LOCALTIMEZONE).strftime("%z")
        })
        video_format, token_url, url, self.title = self.session.http.get(api_url, schema=validate.Schema(
            validate.parse_json(),
            {
                "video": {
                    "workflow": "token-akamai",
                    "format": validate.any("dash", "hls"),
                    "token": validate.url(),
                    "url": validate.url()
                },
                "meta": {
                    "title": str
                }
            },
            validate.union_get(
                ("video", "format"),
                ("video", "token"),
                ("video", "url"),
                ("meta", "title")
            )
        ))

        data_url = update_qsd(token_url, {
            "url": url
        })
        video_url = self.session.http.get(data_url, schema=validate.Schema(
            validate.parse_json(),
            {"url": validate.url()},
            validate.get("url")
        ))

        if video_format == "dash":
            yield from DASHStream.parse_manifest(self.session, video_url).items()
        elif video_format == "hls":
            yield from HLSStream.parse_variant_playlist(self.session, video_url).items()


__plugin__ = Pluzz
