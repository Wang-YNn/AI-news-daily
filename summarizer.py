"""AI 总结模块：调用 DeepSeek API 对新闻列表进行结构化总结。

使用方式:
    from summarizer import Summarizer

    s = Summarizer(api_key="sk-xxx")
    result = await s.summarize(articles)  # 返回 dict
"""

import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List

from openai import AsyncOpenAI

from fetcher.base import Article

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-chat"
MAX_TOKENS = 4096
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # 秒，指数退避: 1 → 2 → 4

# 单次输入最多多少条新闻（避免 token 超限）
MAX_INPUT_ARTICLES = 80

_TZ_UTC8 = timezone(timedelta(hours=8))

_SYSTEM_PROMPT = """你是一个专业的 AI 新闻编辑。用户会给你一组过去 24 小时内的 AI 相关新闻，请完成以下任务：

1. 从所有新闻中精选出最重要的 10 条（如果总数不足 10 条则全部保留）
2. 按重要性排序（最重要的排在最前面）
3. 剔除内容重复的新闻，保留信息量更大的那一条
4. 覆盖不同领域：兼顾大模型技术、产品应用、商业融资、政策监管、学术前沿
5. 每条新闻提供：
   - title: 中文标题（精炼概括新闻核心内容，20-40字）
   - title_en: 英文原标题（英文源保留原文，中文源翻译为英文）
   - summary: 中文详细总结（4-5句话，约100-150字，包含核心事实和数据）
   - url: 原文链接（直接使用原始链接）
   - source: 来源名称

输出要求：
- 严格输出 JSON，不要包含任何其他文字
- 标题要突出新闻最关键的信息
- 总结要具体、有信息量，包含"谁/做了什么/为什么重要/影响是什么"
- 避免空洞的套话如"值得关注""引发热议"

输出 JSON 格式：
{
  "date": "YYYY-MM-DD",
  "articles": [
    {
      "title": "中文标题",
      "title_en": "English Title",
      "summary": "4-5句话的详细中文总结，约100-150字，包含具体事实、数据和影响分析",
      "url": "https://原文链接",
      "source": "来源名称"
    }
  ],
  "total_count": 10
}"""


def _format_articles(articles: List[Article]) -> str:
    """将文章列表格式化为 prompt 文本"""
    lines = []
    for i, a in enumerate(articles, 1):
        t = a.published_at.astimezone(_TZ_UTC8).strftime("%H:%M")
        lines.append(
            f"[{i}] [{a.language.upper()}] {a.title}\n"
            f"    摘要: {a.summary[:150]}\n"
            f"    来源: {a.source} | 时间: {t} | URL: {a.url}"
        )
    return "\n".join(lines)


def _validate_summary(data: dict) -> list[str]:
    """校验 AI 返回的 JSON 结构，返回错误列表（空列表 = 合法）"""
    errors = []

    if not isinstance(data, dict):
        return ["根元素应为 dict"]

    if "articles" not in data:
        errors.append("缺少 articles 字段")
    elif not isinstance(data["articles"], list):
        errors.append("articles 应为 list")
    else:
        for i, item in enumerate(data["articles"]):
            if "title" not in item:
                errors.append(f"articles[{i}] 缺少 title")
            if "title_en" not in item:
                errors.append(f"articles[{i}] 缺少 title_en")
            if "summary" not in item:
                errors.append(f"articles[{i}] 缺少 summary")
            if "url" not in item:
                errors.append(f"articles[{i}] 缺少 url")
            if "source" not in item:
                errors.append(f"articles[{i}] 缺少 source")

    return errors


class SummarizerError(Exception):
    """AI 总结失败"""

    pass


class Summarizer:
    """新闻 AI 总结器"""

    def __init__(self, api_key: str = None, base_url: str = DEEPSEEK_BASE_URL,
                 model: str = MODEL, dry_run: bool = False):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._dry_run = dry_run
        self._client = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            if not self._api_key:
                from config import config
                self._api_key = config.deepseek_api_key
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
        return self._client

    async def summarize(self, articles: List[Article]) -> dict:
        """对新闻列表进行 AI 总结，返回结构化 dict"""

        if not articles:
            raise SummarizerError("没有新闻需要总结")

        # 截断
        if len(articles) > MAX_INPUT_ARTICLES:
            logger.warning("输入 %d 条，截断为 %d 条", len(articles), MAX_INPUT_ARTICLES)
            articles = articles[:MAX_INPUT_ARTICLES]

        today = datetime.now(_TZ_UTC8).strftime("%Y-%m-%d")
        user_prompt = (
            f"今天是 {today}。以下是过去 24 小时内的 {len(articles)} 条 AI 相关新闻：\n\n"
            f"{_format_articles(articles)}\n\n"
            f"请按照要求总结以上新闻，输出 JSON。"
        )

        if self._dry_run:
            logger.info("[dry-run] 跳过 API 调用，仅输出 prompt")
            print(f"===== SYSTEM PROMPT =====\n{_SYSTEM_PROMPT}\n")
            print(f"===== USER PROMPT ({len(user_prompt)} chars) =====\n{user_prompt}\n")
            return {"dry_run": True}

        client = self._get_client()

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=MAX_TOKENS,
                    temperature=0.3,  # 低温度，更稳定输出
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content
                usage = response.usage
                logger.info(
                    "DeepSeek 调用成功: prompt=%d, completion=%d, total=%d tokens",
                    usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
                )

                # 解析 JSON
                data = self._parse_json(content)
                return data

            except SummarizerError:
                raise  # 解析错误不重试
            except Exception as e:
                logger.warning("DeepSeek 调用失败 (第 %d/%d 次): %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    time.sleep(delay)
                else:
                    raise SummarizerError(f"API 调用失败，已重试 {MAX_RETRIES} 次: {e}") from e

        raise SummarizerError("不可达")

    @staticmethod
    def _parse_json(content: str) -> dict:
        """解析 AI 返回的 JSON，带容错处理"""
        # 尝试直接解析
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # 尝试提取 ```json ... ``` 代码块
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError as e:
                    raise SummarizerError(f"JSON 解析失败: {e}\n内容: {content[:500]}")
            else:
                raise SummarizerError(f"JSON 解析失败且未找到代码块\n内容: {content[:500]}")

        # 结构校验
        errors = _validate_summary(data)
        if errors:
            logger.warning("JSON 结构不完全合法: %s", errors)
            # 不阻断，仅警告

        return data


# ============================================================
# 自检模式
# ============================================================
if __name__ == "__main__":
    import asyncio
    import sys
    from fetcher.base import MockFetcher
    from filter import pipeline

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async def main():
        dry_run = "--dry-run" in sys.argv
        push = "--push" in sys.argv

        # 使用 MockFetcher 模拟数据
        articles = await MockFetcher().fetch()
        articles = pipeline(articles, hours=24)
        print(f"输入文章: {len(articles)} 条\n")

        s = Summarizer(dry_run=dry_run)
        try:
            result = await s.summarize(articles)
        except SummarizerError as e:
            print(f"总结失败: {e}")
            sys.exit(1)

        if dry_run:
            return

        # 打印结果摘要
        print(f"日期: {result.get('date')}")
        print(f"收录: {result.get('total_count')} 条\n")

        articles = result.get("articles", [])
        for i, a in enumerate(articles, 1):
            print(f"{i}. {a.get('title', 'N/A')}")
            print(f"   {a.get('title_en', 'N/A')}")
            print(f"   {a.get('summary', 'N/A')}")
            print(f"   {a.get('url', 'N/A')}  [{a.get('source', '')}]")
            print()

    asyncio.run(main())
