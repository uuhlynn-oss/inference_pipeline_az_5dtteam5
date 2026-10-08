import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import Error, sync_playwright


# 파일 위치:
# news-crawlling-scripts\scripts\collect_x_posts_cdp.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent

OUTPUT_DIR = PROJECT_ROOT / "data" / "social_raw"
OUTPUT_FILE = OUTPUT_DIR / "x_posts_logged_in.jsonl"

# 원격 디버깅으로 실행한 로그인 Chrome 연결 주소
CDP_URL = "http://127.0.0.1:9222"

# 검색 결과 화면에서 실제로 확인된 HTML 날짜 형식의 월
MONTH_MAP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12
}

# 이번 실행에서 재개할 범위만 명시
# until은 종료일을 포함하지 않으므로 2026-08-06으로 설정
RESUME_TARGETS = [
    {
        "organization": "서울교통공사",
        "handle": "seoul_metro",
        "start_date": "2026-06-29",
        "end_date_exclusive": "2026-08-06"
    },
    {
        "organization": "서울시 교통정보센터 TOPIS",
        "handle": "seoultopis",
        "start_date": "2024-01-01",
        "end_date_exclusive": "2026-08-06"
    }
]

WINDOW_DAYS = 14

# 검색 결과에서 스크롤할 최대 횟수
MAX_SCROLL_COUNT = 80

# 새 게시물 URL이 연속으로 없으면 현재 2주 구간 종료
MAX_NO_NEW_URL_COUNT = 8

PAGE_WAIT_MILLISECONDS = 4000
SCROLL_WAIT_MILLISECONDS = 2000
WINDOW_WAIT_MILLISECONDS = 3000


def parse_utc_date(date_text: str) -> datetime:
    """
    YYYY-MM-DD 문자열을 UTC 자정 datetime으로 변환합니다.
    """
    return datetime.strptime(
        date_text,
        "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc)


def load_existing_ids() -> set[str]:
    """
    이전에 저장된 게시물 ID를 읽습니다.
    기존 데이터는 유지하고, 같은 게시물은 다시 저장하지 않습니다.
    """
    existing_ids = set()

    if not OUTPUT_FILE.exists():
        return existing_ids

    with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                post = json.loads(line)
                post_id = post.get("id", "")

                if post_id:
                    existing_ids.add(post_id)

            except json.JSONDecodeError:
                continue

    return existing_ids


def create_date_windows(
    start_date: datetime,
    end_date_exclusive: datetime
) -> list[tuple[datetime, datetime]]:
    """
    지정 범위를 14일 단위로 나눕니다.
    """
    windows = []
    current_start = start_date

    while current_start < end_date_exclusive:
        current_end = min(
            current_start + timedelta(days=WINDOW_DAYS),
            end_date_exclusive
        )

        windows.append((current_start, current_end))
        current_start = current_end

    return windows


def build_search_url(
    handle: str,
    window_start: datetime,
    window_end: datetime
) -> str:
    """
    로그인된 X 검색 URL 생성.
    답글과 재게시물은 검색 조건에서 제외합니다.
    """
    query = (
        f"from:{handle} "
        f"since:{window_start.date().isoformat()} "
        f"until:{window_end.date().isoformat()} "
        f"-filter:replies "
        f"-filter:retweets"
    )

    return (
        "https://x.com/search"
        f"?q={quote_plus(query)}"
        "&src=typed_query"
        "&f=live"
    )


def get_post_url(article, handle: str) -> str:
    """
    확인된 구조:
    <a href="/계정/status/게시물ID" aria-label="Jan 15, 2024">

    사진·인용 등 하위 URL은 제외하고 기본 게시물 URL만 반환합니다.
    """
    links = article.locator(
        f'a[href^="/{handle}/status/"][aria-label]'
    )

    for index in range(links.count()):
        href = links.nth(index).get_attribute("href")

        if not href:
            continue

        clean_href = href.split("?")[0]

        # /account/status/123456 형식만 통과
        if clean_href.count("/") != 3:
            continue

        return f"https://x.com{clean_href}"

    return ""


def get_post_id(post_url: str) -> str:
    """
    게시물 URL에서 숫자 ID를 추출합니다.
    """
    if "/status/" not in post_url:
        return ""

    return post_url.split("/status/")[-1].split("?")[0]


def parse_aria_date(date_text: str) -> datetime | None:
    """
    aria-label 날짜 예:
    Jan 15, 2024
    """
    if not date_text:
        return None

    try:
        month_text, day_text, year_text = date_text.split()

        return datetime(
            year=int(year_text),
            month=MONTH_MAP[month_text],
            day=int(day_text.replace(",", "")),
            tzinfo=timezone.utc
        )

    except (ValueError, KeyError):
        return None


def get_post_date(article, handle: str) -> datetime | None:
    """
    기본 게시물 링크의 aria-label에서 날짜를 읽습니다.
    """
    links = article.locator(
        f'a[href^="/{handle}/status/"][aria-label]'
    )

    for index in range(links.count()):
        href = links.nth(index).get_attribute("href")

        if not href:
            continue

        clean_href = href.split("?")[0]

        if clean_href.count("/") != 3:
            continue

        aria_label = links.nth(index).get_attribute("aria-label")

        return parse_aria_date(aria_label)

    return None


def get_post_content(article) -> str:
    """
    확인된 구조:
    <div data-testid="tweetText">...</div>
    """
    text_nodes = article.locator(
        'div[data-testid="tweetText"]'
    )

    parts = []

    for index in range(text_nodes.count()):
        try:
            text = text_nodes.nth(index).inner_text().strip()

            if text:
                parts.append(text)

        except Error:
            continue

    return "\n".join(parts).strip()


def collect_posts_in_window(
    page,
    organization: str,
    handle: str,
    window_start: datetime,
    window_end: datetime,
    existing_ids: set[str]
) -> list[dict]:
    """
    계정 1개, 2주 구간 1개를 수집합니다.
    """
    search_url = build_search_url(
        handle=handle,
        window_start=window_start,
        window_end=window_end
    )

    print("\n" + "-" * 70)
    print(
        f"검색: @{handle} | "
        f"{window_start.date()} ~ "
        f"{(window_end - timedelta(days=1)).date()}"
    )
    print("-" * 70)

    page.goto(
        search_url,
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(PAGE_WAIT_MILLISECONDS)

    if "/i/flow/" in page.url:
        raise RuntimeError(
            "X 검색 페이지가 로그인 화면으로 전환되었습니다. "
            "로그인 Chrome 창의 세션 상태를 확인하세요."
        )

    collected_posts = []
    processed_urls = set()
    no_new_url_count = 0

    for scroll_count in range(1, MAX_SCROLL_COUNT + 1):
        articles = page.locator(
            'article[data-testid="tweet"]'
        )

        article_count = articles.count()
        new_url_count = 0

        for article_index in range(article_count):
            article = articles.nth(article_index)

            post_url = get_post_url(
                article=article,
                handle=handle
            )

            if not post_url or post_url in processed_urls:
                continue

            processed_urls.add(post_url)
            new_url_count += 1

            post_id = get_post_id(post_url)

            if not post_id:
                continue

            saved_id = f"x_{post_id}"

            # 저장되어 있으면 다시 저장하지 않음
            if saved_id in existing_ids:
                continue

            published_at = get_post_date(
                article=article,
                handle=handle
            )

            if published_at is None:
                print(f"[제외] 날짜 없음 | {post_id}")
                continue

            if published_at < window_start or published_at >= window_end:
                continue

            content = get_post_content(article)

            if not content:
                print(f"[제외] 본문 없음 | {post_id}")
                continue

            post = {
                "id": saved_id,
                "platform": "x",
                "organization": organization,
                "account_handle": f"@{handle}",
                "official": True,
                "published_at": published_at.isoformat(),
                "content": content,
                "post_url": post_url,
                "search_period_start": window_start.date().isoformat(),
                "search_period_end_exclusive": window_end.date().isoformat(),
                "document_type": "official_social_notice",
                "collected_at": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            collected_posts.append(post)
            existing_ids.add(saved_id)

            print(
                f"[수집] @{handle} | "
                f"{published_at.date()} | {post_id}"
            )

        print(
            f"[스크롤 {scroll_count}/{MAX_SCROLL_COUNT}] "
            f"게시물={article_count} / "
            f"새 URL={new_url_count} / "
            f"현재 기간 수집={len(collected_posts)}"
        )

        if new_url_count == 0:
            no_new_url_count += 1
        else:
            no_new_url_count = 0

        if no_new_url_count >= MAX_NO_NEW_URL_COUNT:
            print(
                f"새 URL이 {MAX_NO_NEW_URL_COUNT}회 연속 없어 "
                "현재 기간 검색을 종료합니다."
            )
            break

        try:
            page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)"
            )
            page.wait_for_timeout(SCROLL_WAIT_MILLISECONDS)

        except Error as error:
            # 페이지가 자동 이동/새로고침되는 경우
            # 해당 2주 구간만 종료하고 다음 구간으로 넘어감
            print(
                "[현재 기간 중단] 스크롤 중 페이지 이동이 발생했습니다: "
                f"{error}"
            )
            break

    return collected_posts


def append_posts(posts: list[dict]) -> None:
    """
    이번 2주 구간에서 수집한 내용을 즉시 파일에 추가 저장합니다.
    """
    if not posts:
        return

    with open(OUTPUT_FILE, "a", encoding="utf-8") as output_file:
        for post in posts:
            output_file.write(
                json.dumps(post, ensure_ascii=False) + "\n"
            )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    existing_ids = load_existing_ids()
    total_new_posts = 0

    print("X 수집 재개")
    print(f"- 기존 저장 ID 수: {len(existing_ids)}")
    print("- 서울시(@seoulmania)는 다시 검색하지 않습니다.")
    print("- 서울교통공사와 TOPIS만 처리합니다.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            CDP_URL
        )

        context = browser.contexts[0]

        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        for target in RESUME_TARGETS:
            organization = target["organization"]
            handle = target["handle"]

            start_date = parse_utc_date(target["start_date"])
            end_date_exclusive = parse_utc_date(
                target["end_date_exclusive"]
            )

            windows = create_date_windows(
                start_date=start_date,
                end_date_exclusive=end_date_exclusive
            )

            account_new_count = 0

            print("\n" + "=" * 70)
            print(f"계정 재개: {organization} @{handle}")
            print(
                f"처리 범위: {start_date.date()} ~ "
                f"{(end_date_exclusive - timedelta(days=1)).date()}"
            )
            print(f"2주 구간 수: {len(windows)}")
            print("=" * 70)

            for index, (window_start, window_end) in enumerate(
                windows,
                start=1
            ):
                print(f"[기간 {index}/{len(windows)}]")

                posts = collect_posts_in_window(
                    page=page,
                    organization=organization,
                    handle=handle,
                    window_start=window_start,
                    window_end=window_end,
                    existing_ids=existing_ids
                )

                # 기간마다 즉시 저장
                append_posts(posts)

                account_new_count += len(posts)
                total_new_posts += len(posts)

                print(
                    f"[기간 완료] 신규 저장: {len(posts)}건"
                )

                page.wait_for_timeout(WINDOW_WAIT_MILLISECONDS)

            print(
                f"[계정 완료] @{handle} 신규 저장: "
                f"{account_new_count}건"
            )

        browser.close()

    print("\n" + "=" * 70)
    print("X 수집 재개 완료")
    print(f"- 이번 실행 신규 저장: {total_new_posts}건")
    print(f"- 결과 파일: {OUTPUT_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()