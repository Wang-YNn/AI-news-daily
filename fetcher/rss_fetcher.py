"""RSS 抓取器：异步抓取多个 RSS 源，解析为标准 Article 列表。

使用方式:
    from fetcher.rss_fetcher import RSSFetcher, load_sources

    sources = load_sources("sources.yaml")
    fetcher = RSSFetcher(sources)
    articles = await fetcher.fetch()
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

import feedparser
import httpx
import yaml

from fetcher.base import Article, ArticleFetcher

logger = logging.getLogger(__name__)

# 关键词列表：用于从 RSS 源中初步过滤 AI 无关文章（粗筛，精准过滤在 filter.py）
_AI_KEYWORDS = re.compile(
    r"\b(ai|artificial intelligence|ml|machine learning|llm|large language|"
    r"nlp|deep learning|gpt|claude|gemini|llama|openai|anthropic|"
    r"transformer|diffusion|neural|chatgpt|copilot|agent|"
    r"人工智能|大模型|大语言|机器学习|深度学习|自然语言|"
    r"文心|通义|星火|混元|盘古|悟道|"
    r"模型|算法|训练|推理|多模态|"
    r"AIGC|生成式|智能体)\b",
    re.IGNORECASE,
)

# 默认超时
_HTTP_TIMEOUT = 30.0


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """解析 RSS 日期字符串为 UTC datetime"""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _matches_keywords(title: str, summary: str) -> bool:
    """判断文章标题或摘要是否包含 AI 关键词"""
    text = f"{title} {summary}"
    return bool(_AI_KEYWORDS.search(text))


def load_sources(path: str) -> List[dict]:
    """从 YAML 文件加载新闻源配置"""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sources = [s for s in data["sources"] if s.get("enabled", True)]
    return sources


class RSSFetcher(ArticleFetcher):
    """RSS 通用抓取器"""

    def __init__(self, sources: List[dict], timeout: float = _HTTP_TIMEOUT):
        self._sources = sources
        self._timeout = timeout

    async def fetch(self) -> List[Article]:
        """并行抓取所有启用的 RSS 源"""
        tasks = [self._fetch_one(src) for src in self._sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_articles: List[Article] = []
        for src, result in zip(self._sources, results):
            if isinstance(result, Exception):
                logger.warning("%s: 抓取失败 (%s: %s)", src["name"],
                               type(result).__name__, result)
                continue
            all_articles.extend(result)

        return all_articles

    async def _fetch_one(self, source: dict) -> List[Article]:
        """抓取单个 RSS 源"""
        name = source["name"]
        url = source["url"]
        language = source.get("language", "en")

        # 1. 发送 HTTP 请求
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()
            except httpx.TimeoutException:
                raise RuntimeError(f"请求超时 ({self._timeout}s)") from None
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"HTTP {e.response.status_code}") from None
            except httpx.RequestError as e:
                raise RuntimeError(f"请求错误: {e}") from None

        # 2. 解析 RSS/Atom
        feed = feedparser.parse(response.text)

        if feed.bozo and not feed.entries:
            raise RuntimeError(f"RSS 解析失败: {feed.bozo_exception}")

        # 3. 转换为 Article
        articles = []
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue

            link = entry.get("link", "").strip()
            summary = self._clean_summary(entry.get("summary", ""))
            published = _parse_date(entry.get("published")) or datetime.now(timezone.utc)

            # 粗筛：关键词过滤
            if not _matches_keywords(title, summary):
                continue

            articles.append(Article(
                title=title,
                url=link or f"https://{name.lower().replace(' ', '')}.com",
                summary=summary,
                source=name,
                published_at=published,
                language=language,
            ))

        logger.info("%s: 获取 %d 条 (解析 %d 条, 过滤后 %d 条)",
                     name, len(articles), len(feed.entries), len(articles))
        return articles

    @staticmethod
    def _clean_summary(text: str) -> str:
        """去除 HTML 标签，截断过长摘要"""
        if not text:
            return ""
        # 移除 HTML 标签
        clean = re.sub(r"<[^>]+>", "", text)
        # 合并多余空白
        clean = re.sub(r"\s+", " ", clean).strip()
        # 截断到 500 字符
        if len(clean) > 500:
            clean = clean[:497] + "..."
        return clean


# ============================================================
# 自检模式
# ============================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async def main():
        sources = load_sources("sources.yaml")
        print(f"加载 {len(sources)} 个 RSS 源:\n")
        for s in sources:
            print(f"  [{s['language'].upper()}] {s['name']} → {s['url']}")

        fetcher = RSSFetcher(sources)
        articles = await fetcher.fetch()

        print(f"\n===== 汇总 =====")
        print(f"  总计: {len(articles)} 条")

        # 按来源统计
        from collections import Counter
        by_source = Counter(a.source for a in articles)
        for src, count in by_source.most_common():
            print(f"  {src}: {count} 条")

        # 按语言统计
        zh = sum(1 for a in articles if a.language == "zh")
        en = sum(1 for a in articles if a.language == "en")
        print(f"\n  中文: {zh} 条, 英文: {en} 条")

        # 检查字段完整性
        bad = [a for a in articles if not a.title or not a.url or not a.source]
        if bad:
            print(f"\n  警告: {len(bad)} 条数据不完整")
            for a in bad[:3]:
                print(f"    title={a.title!r}, url={a.url!r}, source={a.source!r}")
        else:
            print(f"\n  数据完整性检查通过")

        # 打印前 5 条样例
        print(f"\n  样例:")
        for a in articles[:5]:
            t = a.published_at.strftime("%m-%d %H:%M")
            print(f"    [{a.language}] {a.title[:60]} | {a.source} | {t}")

    asyncio.run(main())
