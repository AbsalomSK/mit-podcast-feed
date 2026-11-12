# -*- coding: utf-8 -*-
"""RSS 조립 자체 점검. 실행: python test_sync_podcast.py"""
from xml.etree import ElementTree

from sync_podcast import build_feed, clean_title, hhmmss, rfc2822

CFG = {
    "public_base_url": "https://cdn.example.com/",
    "show_title": "얘기좀합시다",
    "show_link": "https://www.youtube.com/@example",
    "show_description": "설명 & 특수문자 <test>",
    "show_author": "머니인더트랩",
    "show_email": "a@b.com",
    "show_category": "Business",
}
EPS = [
    {"id": "aaa", "title": "옛날 편", "description": "본문", "key": "audio/aaa.mp3",
     "bytes": 100, "duration": 61, "timestamp": 1000,
     "pub_date": "Thu, 01 Jan 1970 00:16:40 +0000"},
    {"id": "bbb", "title": 'AT&T와 "따옴표"', "description": "b", "key": "audio/bbb.mp3",
     "art": "art/bbb.jpg", "bytes": 200, "duration": 3725, "timestamp": 2000,
     "pub_date": "Thu, 01 Jan 1970 00:33:20 +0000"},
]
NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


def test_feed() -> None:
    root = ElementTree.fromstring(build_feed(CFG, EPS))
    ch = root.find("channel")
    assert ch.find("itunes:owner/itunes:email", NS).text == "a@b.com"
    assert ch.find("description").text == "설명 & 특수문자 <test>"
    items = ch.findall("item")
    assert [i.find("guid").text for i in items] == ["bbb", "aaa"]  # 최신순
    assert items[0].find("title").text == 'AT&T와 "따옴표"'
    assert items[0].find("enclosure").get("url") == "https://cdn.example.com/audio/bbb.mp3"
    assert items[0].find("itunes:image", NS).get("href") == "https://cdn.example.com/art/bbb.jpg"
    assert items[1].find("itunes:image", NS) is None  # 아트 없는 편은 태그 없음
    assert items[0].find("itunes:duration", NS).text == "1:02:05"


def test_helpers() -> None:
    assert clean_title("[얘기좀합시다] 제목 | 게스트") == "제목 | 게스트"
    assert clean_title("제목 | [얘기좀합시다]") == "제목"
    assert hhmmss(61) == "0:01:01"
    assert rfc2822({"timestamp": 1000}) == "Thu, 01 Jan 1970 00:16:40 +0000"
    assert rfc2822({"upload_date": "20260312"}).startswith("Thu, 12 Mar 2026")


if __name__ == "__main__":
    test_feed()
    test_helpers()
    print("OK")
