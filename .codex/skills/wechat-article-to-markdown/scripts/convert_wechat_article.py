from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag


WECHAT_HOST = "mp.weixin.qq.com"
INVALID_FILENAME_CHARS = r'[/\\?%*:|"<>]'


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _find_repo_root() -> Path:
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / ".git").exists() or (parent / "AGENTS.md").exists():
            return parent
    return start.parents[2]


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\r", "").replace("\u00a0", " ")
    return re.sub(r"[ \t]+", " ", value).strip()


def _safe_filename(value: str, fallback: str = "wechat-article") -> str:
    safe = re.sub(INVALID_FILENAME_CHARS, "_", _clean_text(value))
    safe = safe.strip(" .")
    return (safe or fallback)[:80]


def normalize_wechat_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("URL is empty")
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)
    if parsed.netloc != WECHAT_HOST:
        raise ValueError("Please provide a valid mp.weixin.qq.com article URL")
    return url


def _headers(referer: str = "https://mp.weixin.qq.com/") -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Referer": referer,
    }


def _meta_content(soup: BeautifulSoup, *selectors: tuple[str, str]) -> str:
    for key, value in selectors:
        tag = soup.find("meta", attrs={key: value})
        if tag and tag.get("content"):
            return _clean_text(tag["content"])
    return ""


def extract_metadata(soup: BeautifulSoup, html: str, source_url: str) -> dict[str, str]:
    title_tag = soup.select_one("#activity-name")
    author_tag = soup.select_one("#js_name")
    title = _clean_text(title_tag.get_text(" ", strip=True) if title_tag else "")
    author = _clean_text(author_tag.get_text(" ", strip=True) if author_tag else "")
    if not title:
        title = _meta_content(
            soup,
            ("property", "og:title"),
            ("name", "twitter:title"),
        )
    if not author:
        author = _meta_content(
            soup,
            ("property", "og:article:author"),
            ("name", "author"),
        )

    publish_time = ""
    match = re.search(r"var\s+ct\s*=\s*['\"]?(\d{10})", html)
    if match:
        publish_time = dt.datetime.fromtimestamp(int(match.group(1))).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "title": title,
        "author": author,
        "publish_time": publish_time,
        "source_url": source_url,
    }


def _image_src(tag: Tag) -> str:
    for attr in ("data-src", "src", "data-backsrc", "data-original"):
        value = tag.get(attr)
        if value:
            value = str(value)
            if value.startswith("//"):
                return "https:" + value
            return value
    return ""


def _inline_text(node: Tag) -> str:
    parts: list[str] = []
    for child in node.descendants:
        if isinstance(child, NavigableString):
            text = _clean_text(str(child))
            if text:
                parts.append(text)
        elif isinstance(child, Tag) and child.name and child.name.lower() == "br":
            parts.append("\n")
    return _clean_text(" ".join(parts).replace(" \n ", "\n"))


def _code_line_text(node: Tag) -> str:
    text = node.get_text("", strip=False)
    return text.replace("\u00a0", " ").rstrip()


def _pre_text(node: Tag) -> str:
    code_lines = node.find_all("code", recursive=False)
    if code_lines:
        return "\n".join(_code_line_text(line) for line in code_lines).strip("\n")
    return node.get_text("\n", strip=False).replace("\u00a0", " ").strip("\n")


def _fence_for(code: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`{3,}", code)), default=0)
    return "`" * max(3, longest + 1)


def _is_fence_line(line: str) -> bool:
    return _fence_marker(line) is not None


def _fence_marker(line: str) -> str | None:
    match = re.fullmatch(r"(`{3,})(?:[A-Za-z0-9_-]+)?", line.strip())
    return match.group(1) if match else None


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def _render_markdown_lines(lines: list[str]) -> str:
    rendered: list[str] = []
    active_fence: str | None = None
    previous = ""
    for line in lines:
        if not line:
            continue
        marker = _fence_marker(line)

        if not rendered:
            rendered.append(line)
        else:
            compact = (
                active_fence is not None
                or marker is not None
                or _is_fence_line(previous)
                or (_is_table_line(line) and _is_table_line(previous))
            )
            rendered.append("\n" if compact else "\n\n")
            rendered.append(line)

        if active_fence is not None:
            if line.strip() == active_fence:
                active_fence = None
        elif marker is not None:
            active_fence = marker
        previous = line

    return "".join(rendered)


def _append_blank(lines: list[str]) -> None:
    if lines and lines[-1] != "":
        lines.append("")


def _has_block_child(node: Tag) -> bool:
    block_names = {
        "blockquote",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "img",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
    return any(isinstance(child, Tag) and child.name and child.name.lower() in block_names for child in node.children)


def _walk(node: Tag | NavigableString, lines: list[str], image_urls: list[str]) -> None:
    if isinstance(node, NavigableString):
        text = _clean_text(str(node))
        if text:
            lines.append(text)
        return
    if not isinstance(node, Tag) or not node.name:
        return

    name = node.name.lower()
    if name in {"script", "style", "svg"}:
        return
    if name == "br":
        _append_blank(lines)
        return
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        text = _clean_text(node.get_text(" ", strip=True))
        if text:
            level = min(int(name[1]) + 1, 6)
            _append_blank(lines)
            lines.append(f"{'#' * level} {text}")
            _append_blank(lines)
        return
    if name == "img":
        src = _image_src(node)
        if src:
            image_urls.append(src)
            alt = _clean_text(node.get("alt", "")) or "image"
            lines.append(f"![{alt}]({src})")
            _append_blank(lines)
        return
    if name == "pre":
        code = _pre_text(node)
        if code:
            fence = _fence_for(code)
            _append_blank(lines)
            lines.append(fence)
            lines.extend(code.splitlines())
            lines.append(fence)
            _append_blank(lines)
        return
    if name == "blockquote":
        text = node.get_text("\n", strip=True)
        if text:
            _append_blank(lines)
            for line in text.splitlines():
                cleaned = _clean_text(line)
                if cleaned:
                    lines.append(f"> {cleaned}")
            _append_blank(lines)
        return
    if name in {"ul", "ol"}:
        ordered = name == "ol"
        index = 1
        _append_blank(lines)
        for li in node.find_all("li", recursive=False):
            text = _clean_text(li.get_text(" ", strip=True))
            if text:
                marker = f"{index}. " if ordered else "- "
                lines.append(marker + text)
                index += 1
        _append_blank(lines)
        return
    if name == "table":
        rows: list[list[str]] = []
        for tr in node.find_all("tr"):
            cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if rows:
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
            _append_blank(lines)
            lines.append("| " + " | ".join(rows[0]) + " |")
            lines.append("| " + " | ".join(["---"] * width) + " |")
            for row in rows[1:]:
                lines.append("| " + " | ".join(row) + " |")
            _append_blank(lines)
        return
    if name in {"p", "section", "div", "span"}:
        if _has_block_child(node):
            for child in node.children:
                _walk(child, lines, image_urls)
        else:
            text = _inline_text(node)
            if text:
                lines.append(text)
                _append_blank(lines)
        return

    for child in node.children:
        _walk(child, lines, image_urls)


def content_to_markdown(content: Tag) -> tuple[str, list[str]]:
    for node in content.select("script, style, svg"):
        node.decompose()

    lines: list[str] = []
    image_urls: list[str] = []
    _walk(content, lines, image_urls)

    normalized: list[str] = []
    previous_text = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if normalized and normalized[-1] != "":
                normalized.append("")
            continue
        if line == previous_text:
            continue
        normalized.append(line)
        if not line.startswith("!["):
            previous_text = line

    while normalized and normalized[-1] == "":
        normalized.pop()
    markdown = _render_markdown_lines(normalized)
    return markdown, image_urls


def _image_extension(url: str, content_type: str | None) -> str:
    query = parse_qs(urlparse(url).query)
    fmt = query.get("wx_fmt", [""])[0]
    if fmt:
        return "." + fmt.lower().replace("jpeg", "jpg")
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed.replace(".jpe", ".jpg")
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".jpg"


async def download_images(image_urls: list[str], image_dir: Path, referer: str) -> dict[str, str]:
    image_dir.mkdir(parents=True, exist_ok=True)
    url_map: dict[str, str] = {}
    seen = list(dict.fromkeys(image_urls))
    async with httpx.AsyncClient(headers=_headers(referer), timeout=60.0, follow_redirects=True) as client:
        for index, url in enumerate(seen, start=1):
            try:
                response = await client.get(url)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - keep conversion usable when one image fails.
                print(f"IMAGE_FAILED={url} :: {exc}", file=sys.stderr)
                continue
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
            ext = _image_extension(url, response.headers.get("content-type"))
            filename = f"{index:02d}-{digest}{ext}"
            path = image_dir / filename
            path.write_bytes(response.content)
            url_map[url] = f"images/{filename}"
    return url_map


def build_markdown(meta: dict[str, str], body: str) -> str:
    return (
        f"# {meta['title']}\n\n"
        f"- 来源：[{meta['source_url']}]({meta['source_url']})\n"
        f"- 公众号：{meta.get('author') or '未知'}\n"
        f"- 发布时间：{meta.get('publish_time') or '未知'}\n\n"
        "---\n\n"
        f"{body}\n"
    )


async def convert(url: str, output_dir: Path | None = None, skip_images: bool = False) -> Path:
    normalized_url = normalize_wechat_url(url)
    repo_root = _find_repo_root()
    if output_dir is None:
        output_dir = repo_root / ".tmp" / "wechat-article-to-markdown" / "output"

    async with httpx.AsyncClient(headers=_headers(), timeout=60.0, follow_redirects=True) as client:
        response = await client.get(normalized_url)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    meta = extract_metadata(soup, html, normalized_url)
    if not meta.get("title"):
        output_dir.mkdir(parents=True, exist_ok=True)
        debug_path = output_dir / "debug_direct_fetch.html"
        debug_path.write_text(html, encoding="utf-8")
        raise RuntimeError(f"Article title was not found. Saved debug HTML: {debug_path}")

    content = soup.select_one("#js_content")
    if content is None:
        raise RuntimeError("Article content was not found")

    body, image_urls = content_to_markdown(content)
    safe_title = _safe_filename(meta["title"])
    article_dir = output_dir / safe_title
    image_dir = article_dir / "images"
    article_dir.mkdir(parents=True, exist_ok=True)

    url_map: dict[str, str] = {}
    if not skip_images and image_urls:
        url_map = await download_images(image_urls, image_dir, normalized_url)
        for remote, local in url_map.items():
            body = body.replace(remote, local)

    markdown = build_markdown(meta, body)
    markdown_path = article_dir / f"{safe_title}.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    print(f"TITLE={meta['title']}")
    print(f"AUTHOR={meta.get('author', '')}")
    print(f"PUBLISH_TIME={meta.get('publish_time', '')}")
    print(f"IMAGES={len(set(image_urls))}")
    print(f"DOWNLOADED={len(url_map)}")
    print(f"MARKDOWN={markdown_path.resolve()}")
    print(f"CHARS={len(markdown)}")
    return markdown_path


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="Convert a public WeChat article to local Markdown.")
    parser.add_argument("url", help="WeChat article URL from mp.weixin.qq.com")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output directory")
    parser.add_argument("--skip-images", action="store_true", help="Keep remote image URLs instead of downloading images")
    args = parser.parse_args()
    asyncio.run(convert(args.url, args.output, args.skip_images))


if __name__ == "__main__":
    main()
