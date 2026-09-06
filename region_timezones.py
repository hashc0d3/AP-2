"""Часовые пояса регионов Avito (IANA). Не указанные города — Europe/Moscow."""

from __future__ import annotations

DEFAULT_TZ = "Europe/Moscow"

# slug -> IANA timezone
_OVERRIDES: dict[str, str] = {
    "kaliningrad": "Europe/Kaliningrad",
    "samara": "Europe/Samara",
    "saratov": "Europe/Samara",
    "engels": "Europe/Samara",
    "syzran": "Europe/Samara",
    "tolyatti": "Europe/Samara",
    "ulyanovsk": "Europe/Samara",
    "astrakhan": "Europe/Samara",
    "volgograd": "Europe/Samara",
    "volzhskiy": "Europe/Samara",
    "ekaterinburg": "Asia/Yekaterinburg",
    "chelyabinsk": "Asia/Yekaterinburg",
    "magnitogorsk": "Asia/Yekaterinburg",
    "kamensk-uralskiy": "Asia/Yekaterinburg",
    "nizhniy_tagil": "Asia/Yekaterinburg",
    "kurgan": "Asia/Yekaterinburg",
    "tyumen": "Asia/Yekaterinburg",
    "surgut": "Asia/Yekaterinburg",
    "khanty-mansiysk": "Asia/Yekaterinburg",
    "nefteyugansk": "Asia/Yekaterinburg",
    "nizhnevartovsk": "Asia/Yekaterinburg",
    "novyy_urengoy": "Asia/Yekaterinburg",
    "noyabrsk": "Asia/Yekaterinburg",
    "perm": "Asia/Yekaterinburg",
    "izhevsk": "Asia/Yekaterinburg",
    "ufa": "Asia/Yekaterinburg",
    "neftekamsk": "Asia/Yekaterinburg",
    "sterlitamak": "Asia/Yekaterinburg",
    "orenburg": "Asia/Yekaterinburg",
    "orsk": "Asia/Yekaterinburg",
    "omsk": "Asia/Omsk",
    "krasnoyarsk": "Asia/Krasnoyarsk",
    "novosibirsk": "Asia/Novosibirsk",
    "barnaul": "Asia/Barnaul",
    "tomsk": "Asia/Tomsk",
    "kemerovo": "Asia/Novokuznetsk",
    "novokuznetsk": "Asia/Novokuznetsk",
    "biysk": "Asia/Barnaul",
    "abakan": "Asia/Krasnoyarsk",
    "gorno-altaysk": "Asia/Barnaul",
    "norilsk": "Asia/Krasnoyarsk",
    "rubtsovsk": "Asia/Barnaul",
    "irkutsk": "Asia/Irkutsk",
    "angarsk": "Asia/Irkutsk",
    "bratsk": "Asia/Irkutsk",
    "ulan-ude": "Asia/Irkutsk",
    "chita": "Asia/Chita",
    "yakutsk": "Asia/Yakutsk",
    "vladivostok": "Asia/Vladivostok",
    "khabarovsk": "Asia/Vladivostok",
    "ussuriysk": "Asia/Vladivostok",
    "komsomolsk-na-amure": "Asia/Vladivostok",
    "nahodka": "Asia/Vladivostok",
    "yuzhno-sakhalinsk": "Asia/Sakhalin",
    "blagoveshchensk": "Asia/Magadan",
    "petropavlovsk-kamchatskiy": "Asia/Kamchatka",
}


def region_timezone(slug: str) -> str:
    return _OVERRIDES.get((slug or "").strip().lower(), DEFAULT_TZ)
