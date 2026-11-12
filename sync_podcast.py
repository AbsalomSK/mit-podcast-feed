# -*- coding: utf-8 -*-
"""
유튜브 롱폼([얘기좀합시다]) → 팟캐스트 RSS (피드는 GitHub Pages, 음원은 Cloudflare R2)

동작:
  1. 채널 영상 탭에서 제목에 [얘기좀합시다]가 든 편만 고른다
  2. R2의 state/podcast_state.json에 없는 편을 최신 것부터 mp3(모노)로 뽑는다
  3. mp3·에피소드 아트를 R2에 올린다
  4. 저장소 루트의 feed.xml을 통째로 다시 쓴다 (워크플로가 커밋 → GitHub Pages로 서비스)
  5. 상태를 R2에 기록하고 텔레그램으로 알린다

스포티파이는 feed.xml을 주기적으로 읽어 가므로, 피드에 편이 추가되면
사람 손 없이 스포티파이에 올라간다. 피드 주소(FEED_BASE_URL)는 바뀌면 안 된다.
음원 주소(MEDIA_BASE_URL)는 나중에 바꿔도 피드만 다시 쓰면 된다.

설정은 전부 환경변수로 받는다 (GitHub Actions의 secrets / variables).
실행: python sync_podcast.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

SERIES_TAG = "[얘기좀합시다]"
STATE_KEY = "state/podcast_state.json"
HERE = Path(__file__).resolve().parent


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name) or default
    if v is None:
        sys.exit(f"환경변수 누락: {name}")
    return v


def load_config() -> dict:
    return {
        "channel_videos_url": env(
            "CHANNEL_VIDEOS_URL",
            "https://www.youtube.com/channel/UCtjj8T7n2_jS92pfMeWdoWQ/videos"),
        "feed_base_url": env(
            "FEED_BASE_URL", "https://absalomsk.github.io/mit-podcast-feed").rstrip("/"),
        "media_base_url": env("MEDIA_BASE_URL").rstrip("/"),
        "show_title": env("SHOW_TITLE", "얘기좀합시다"),
        "show_link": env(
            "SHOW_LINK", "https://www.youtube.com/channel/UCtjj8T7n2_jS92pfMeWdoWQ"),
        "show_description": env(
            "SHOW_DESCRIPTION",
            "세상의 덫에 빠지지 않는 가장 돈돈한 대화. 머니인더트랩이 전문가와 함께 "
            "돈과 세상의 트랩을 풀어가는 경제 대담 [얘기좀합시다]의 오디오판입니다."),
        "show_author": env("SHOW_AUTHOR", "머니인더트랩"),
        "show_email": env("SHOW_EMAIL"),
        "show_category": env("SHOW_CATEGORY", "Business"),
        "audio_quality": env("AUDIO_QUALITY", "5"),
        "max_per_run": int(env("MAX_PER_RUN", "10")),
    }


# ---------- R2 (S3 호환) ----------

def r2_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def load_state(s3, bucket: str) -> dict:
    from botocore.exceptions import ClientError
    try:
        body = s3.get_object(Bucket=bucket, Key=STATE_KEY)["Body"].read()
        return json.loads(body)
    except ClientError as ex:
        if ex.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {"episodes": []}
        raise


def save_state(s3, bucket: str, state: dict) -> None:
    s3.put_object(Bucket=bucket, Key=STATE_KEY,
                  Body=json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8"),
                  ContentType="application/json")


# ---------- 유튜브 ----------

def ytdlp_base() -> list[str]:
    cmd = [sys.executable, "-m", "yt_dlp", "--extractor-args", "youtube:lang=ko"]
    # 데이터센터 IP에서 봇 확인에 걸릴 때만 쓴다 (선택).
    cookies = os.environ.get("YT_COOKIES_FILE")
    if cookies and Path(cookies).exists():
        cmd += ["--cookies", cookies]
    return cmd


def list_series(channel_videos_url: str) -> list[dict]:
    """채널 영상 탭에서 시리즈 편만 (최신순).

    lang=ko가 없으면 유튜브가 자동 번역된 제목을 준다([얘기좀합시다] → [Let's Talk]).
    제목으로 시리즈를 거르므로 이 인자가 빠지면 결과가 0건이 된다.
    """
    r = subprocess.run(ytdlp_base() + ["--flat-playlist", "-J", channel_videos_url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"영상 목록 조회 실패: {r.stderr[-500:]}")
    data = json.loads(r.stdout.lstrip("﻿"))
    return [{"id": e["id"], "title": e.get("title", "")}
            for e in (data.get("entries") or []) if e
            and SERIES_TAG in (e.get("title") or "")]


def download_audio(video_id: str, out_dir: Path, quality: str) -> tuple[Path, dict]:
    """오디오만 받아 모노 mp3로 인코딩. (파일경로, info.json) 반환."""
    r = subprocess.run(
        ytdlp_base() + [
            "-f", "ba/b", "-x", "--audio-format", "mp3",
            "--audio-quality", quality,
            "--postprocessor-args", "ffmpeg:-ac 1",
            "--write-info-json",
            "-o", str(out_dir / f"{video_id}.%(ext)s"),
            f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"다운로드 실패: {r.stderr[-500:]}")
    mp3 = out_dir / f"{video_id}.mp3"
    if not mp3.exists():
        raise RuntimeError(f"mp3가 없음 (받은 파일: {[p.name for p in out_dir.iterdir()]})")
    info = json.loads((out_dir / f"{video_id}.info.json").read_text(encoding="utf-8"))
    return mp3, info


def make_art(video_id: str, out_dir: Path) -> Path | None:
    """유튜브 썸네일(16:9)을 정사각 1400×1400으로. 위아래는 블러한 같은 이미지로 채운다."""
    import requests
    dst = out_dir / f"{video_id}.jpg"
    for name in ("maxresdefault", "hqdefault"):  # maxres는 없는 영상이 있다
        try:
            r = requests.get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=30)
        except Exception:
            continue
        if r.status_code != 200 or len(r.content) < 5000:
            continue
        src = out_dir / f"{video_id}.src.jpg"
        src.write_bytes(r.content)
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
             "[0:v]scale=1400:1400:force_original_aspect_ratio=increase,"
             "crop=1400:1400,gblur=sigma=40[bg];"
             "[0:v]scale=1400:-2:flags=lanczos[fg];"
             "[bg][fg]overlay=(W-w)/2:(H-h)/2",
             "-q:v", "3", str(dst)],
            capture_output=True, text=True)
        if p.returncode == 0 and dst.exists():
            return dst
    return None


# ---------- 피드 ----------

def clean_title(title: str) -> str:
    """[얘기좀합시다] 꼬리표는 피드에서 뺀다. 쇼 전체가 그 시리즈다."""
    return title.replace(SERIES_TAG, "").strip(" |·-")


def rfc2822(info: dict) -> str:
    ts = info.get("timestamp")
    if ts:
        dt = datetime.fromtimestamp(ts, timezone.utc)
    else:
        d = info.get("upload_date")  # YYYYMMDD
        dt = (datetime.strptime(d, "%Y%m%d").replace(tzinfo=timezone.utc)
              if d else datetime.now(timezone.utc))
    return format_datetime(dt)


def hhmmss(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


def build_feed(cfg: dict, episodes: list[dict], feed_name: str = "feed.xml") -> str:
    """전체 목록으로 RSS를 통째로 다시 만든다. 상태의 단일 출처는 state json이다."""
    base = cfg["feed_base_url"].rstrip("/")    # feed.xml, cover.jpg (GitHub Pages)
    media = cfg["media_base_url"].rstrip("/")  # mp3, 에피소드 아트 (R2)
    e = escape
    items = []
    for ep in sorted(episodes, key=lambda x: x["timestamp"], reverse=True):
        # 아트가 없는 편은 태그를 빼서 쇼 커버로 떨어지게 둔다.
        art = (f'\n      <itunes:image href="{e(media)}/{e(ep["art"])}"/>'
               if ep.get("art") else "")
        items.append(f"""    <item>
      <title>{e(ep['title'])}</title>{art}
      <description>{e(ep['description'])}</description>
      <itunes:summary>{e(ep['description'])}</itunes:summary>
      <enclosure url="{e(media)}/{e(ep['key'])}" length="{ep['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{e(ep['id'])}</guid>
      <pubDate>{ep['pub_date']}</pubDate>
      <itunes:duration>{hhmmss(ep['duration'])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <link>https://www.youtube.com/watch?v={e(ep['id'])}</link>
    </item>""")

    # 소유자 메일은 세 군데에 같이 넣는다. 수집기마다 읽는 필드가 다르다.
    owner = f"{e(cfg['show_email'])} ({e(cfg['show_author'])})"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{e(cfg['show_title'])}</title>
    <atom:link href="{e(base)}/{e(feed_name)}" rel="self" type="application/rss+xml"/>
    <link>{e(cfg['show_link'])}</link>
    <language>ko</language>
    <managingEditor>{owner}</managingEditor>
    <webMaster>{owner}</webMaster>
    <description>{e(cfg['show_description'])}</description>
    <itunes:summary>{e(cfg['show_description'])}</itunes:summary>
    <itunes:author>{e(cfg['show_author'])}</itunes:author>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{e(base)}/cover.jpg"/>
    <itunes:category text="{e(cfg['show_category'])}"/>
    <itunes:owner>
      <itunes:name>{e(cfg['show_author'])}</itunes:name>
      <itunes:email>{e(cfg['show_email'])}</itunes:email>
    </itunes:owner>
    <copyright>{e(cfg['show_author'])}</copyright>
{chr(10).join(items)}
  </channel>
</rss>
"""


# ---------- 알림 ----------

def notify(text: str) -> None:
    """텔레그램 알림. 토큰이 없으면 조용히 건너뛴다."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    print(text)
    if not (token and chat):
        return
    import requests
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": text[:4000]}, timeout=30)
    except Exception as ex:
        print(f"텔레그램 전송 실패: {ex}")


# ---------- 본체 ----------

def main() -> None:
    cfg = load_config()
    bucket = env("R2_BUCKET")
    if not shutil.which("ffmpeg"):
        notify("[팟캐스트] 중단 - ffmpeg를 찾을 수 없음")
        sys.exit(1)

    s3 = r2_client()
    state = load_state(s3, bucket)
    done = {ep["id"] for ep in state["episodes"]}

    try:
        series = list_series(cfg["channel_videos_url"])
    except RuntimeError as ex:
        notify(f"[팟캐스트] 중단 - {ex}")
        sys.exit(1)
    if not series:
        # 0건은 정상일 수 없다(이미 130편 넘게 있다). 제목 번역·차단을 의심한다.
        notify("[팟캐스트] 중단 - 시리즈 영상이 0건으로 조회됨 (유튜브 응답 이상)")
        sys.exit(1)

    # 최신 것부터. 과거분 백필이 남아 있어도 새 편이 뒤로 밀리지 않는다.
    pending = [s for s in series if s["id"] not in done]
    print(f"시리즈 총 {len(series)}편, 발행 완료 {len(done)}편, 대기 {len(pending)}편")

    added, failures = [], []
    for s in pending[: cfg["max_per_run"]]:
        vid = s["id"]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            try:
                mp3, info = download_audio(vid, td, cfg["audio_quality"])
                key = f"audio/{vid}.mp3"
                size = mp3.stat().st_size
                s3.upload_file(str(mp3), bucket, key,
                               ExtraArgs={"ContentType": "audio/mpeg"})
                art_key = None
                art = make_art(vid, td)
                if art:
                    art_key = f"art/{vid}.jpg"
                    s3.upload_file(str(art), bucket, art_key,
                                   ExtraArgs={"ContentType": "image/jpeg"})
            except Exception as ex:
                failures.append(f"{vid} \"{s['title'][:30]}\": {ex}")
                print(f"실패: {failures[-1]}")
                continue

        ep = {
            "id": vid,
            "title": clean_title(info.get("title") or s["title"]),
            "description": (info.get("description") or "").strip()[:3000],
            "key": key,
            "bytes": size,
            "duration": int(info.get("duration") or 0),
            "timestamp": int(info.get("timestamp") or 0),
            "pub_date": rfc2822(info),
        }
        if art_key:
            ep["art"] = art_key
        state["episodes"].append(ep)
        added.append(ep)
        # 건별로 기록한다. 중간에 죽어도 올린 편을 다시 받지 않는다.
        save_state(s3, bucket, state)
        print(f"발행: {vid} \"{ep['title']}\" ({size // 1024}KB)")

    # 유튜브에서 제목을 고치면 피드도 따라간다.
    titles = {s["id"]: s["title"] for s in series}
    for ep in state["episodes"]:
        if titles.get(ep["id"]):
            ep["title"] = clean_title(titles[ep["id"]])
    save_state(s3, bucket, state)

    # 피드는 마지막에 한 번만 쓴다. 편이 하나도 없으면 쓰지 않는다
    # (빈 피드를 올리면 스포티파이가 쇼를 비운다).
    # 저장소 루트에 쓰고, 커밋·푸시는 워크플로가 한다.
    if state["episodes"]:
        feed = HERE / "feed.xml"
        tmp = feed.with_suffix(".xml.tmp")
        tmp.write_text(build_feed(cfg, state["episodes"]), encoding="utf-8")
        tmp.replace(feed)
        print(f"피드 작성: {len(state['episodes'])}편 → {cfg['feed_base_url']}/feed.xml")

    remaining = len(pending) - len(added)
    if added:
        body = "\n".join(f"- {ep['title'][:50]}" for ep in added[:10])
        notify(f"[팟캐스트] {len(added)}편 발행 (남은 대기 {remaining}편)\n\n{body}")
    if failures:
        notify("[팟캐스트] 실패 {}건\n\n{}".format(
            len(failures), "\n\n".join(f[:400] for f in failures)))
        sys.exit(1)


if __name__ == "__main__":
    main()
