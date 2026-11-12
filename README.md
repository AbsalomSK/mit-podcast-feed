# mit-podcast-feed

유튜브 **머니인더트랩**의 `[얘기좀합시다]` 롱폼을 오디오 팟캐스트 피드로 만들어
Cloudflare R2에 상시 호스팅한다. 스포티파이가 이 피드를 주기적으로 읽어 가므로
유튜브에 새 편이 올라오면 사람 손 없이 스포티파이에 올라간다.

```
GitHub Actions (매시간)
  → 채널에서 [얘기좀합시다] 새 편 감지 (yt-dlp)
  → mp3(모노) 추출 + 정사각 에피소드 아트 생성
  → R2 업로드 (audio/, art/, feed.xml, state/podcast_state.json)
  → 텔레그램 알림
스포티파이 → {PUBLIC_BASE_URL}/feed.xml 주기 수집
```

## 최초 설정 (1회)

1. **Cloudflare R2**: 버킷을 만들고 공개 접근을 켠다. 커스텀 도메인 연결을 권장한다
   (`r2.dev` 주소는 속도 제한이 있는 개발용이다). R2 API 토큰(객체 읽기·쓰기)을 발급한다.
2. **GitHub 저장소 설정 → Secrets and variables → Actions**

   | 종류 | 이름 | 값 |
   |---|---|---|
   | Secret | `R2_ACCOUNT_ID` | Cloudflare 계정 ID |
   | Secret | `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | R2 API 토큰 |
   | Secret | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 알림용 (선택) |
   | Secret | `YT_COOKIES` | 유튜브가 Actions IP를 막을 때만 (선택, Netscape 형식) |
   | Variable | `R2_BUCKET` | 버킷 이름 |
   | Variable | `PUBLIC_BASE_URL` | 버킷 공개 주소 (예: `https://podcast.example.com`) |
   | Variable | `SHOW_EMAIL` | 피드 소유자 메일 (스포티파이 인증 코드 수신 주소) |

3. Actions 탭에서 **팟캐스트 동기화**를 수동 실행한다. 과거분 백필은
   `max_per_run`을 크게 주고 여러 번 돌린다.
4. `{PUBLIC_BASE_URL}/feed.xml`을 Spotify for Creators에 **기존 팟캐스트 추가(RSS)**로 제출한다.
   "호스팅 이전"이 아니라 피드를 그대로 두고 연결하는 쪽이다.

## 운영 메모

- 상태의 단일 출처는 R2의 `state/podcast_state.json`이다. 한 편을 다시 뽑으려면
  그 편을 이 파일에서 지우면 된다.
- 피드 주소(`PUBLIC_BASE_URL`)는 스포티파이 등록 후 바꾸지 않는다.
- 저장소의 `audio/`, `art/`, `feed.xml`은 2026-08 인증 시험용 최소 피드의 잔재다
  (GitHub Pages로 서비스됨). 새 피드가 등록되면 정리해도 된다.
