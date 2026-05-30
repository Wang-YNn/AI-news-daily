"""新闻过滤器：时间过滤、AI 关键词过滤、去重。

三个过滤器可以组合使用，每个函数输入 list[Article]，输出 list[Article]。
"""

import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import List

from fetcher.base import Article


# ============================================================
# 时间过滤
# ============================================================

def filter_by_time(articles: List[Article], hours: int = 24) -> List[Article]:
    """只保留最近 N 小时内发布的文章"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept = [a for a in articles if a.published_at > cutoff]
    return kept


# ============================================================
# AI 关键词过滤
# ============================================================

# 英文关键词
_EN_KEYWORDS = re.compile(
    r"\b(ai|artificial intelligence|ml|machine learning|llm|large language model|"
    r"nlp|natural language|deep learning|neural network|transformer|diffusion|"
    r"gpt|claude|gemini|llama|openai|anthropic|deepmind|chatgpt|copilot|"
    r"ai agent|ai coding|rag|fine.tun|prompt engine|"
    r"gpu|nvidia|blackwell|hopper|tpu|"
    r"generative ai|genai|aigc|"
    r"robot|robotics|autonomous|computer vision|speech recognition)\b",
    re.IGNORECASE,
)

# 中文关键词
_ZH_KEYWORDS = re.compile(
    r"(人工智能|大模型|大语言|语言模型|机器学习|深度学习|自然语言|"
    r"神经网络|生成式|AIGC|智能体|智能助手|"
    r"文心|通义|星火|混元|盘古|悟道|天工|百川|豆包|"
    r"模型|算法|训练|推理|多模态|"
    r"开源|参数|token|"
    r"GPT|Claude|Gemini|Llama|OpenAI|"
    r"GPU|算力|芯片|训练集群|"
    r"机器人|自动驾驶|具身智能|"
    r"备案|监管|安全对齐|"
    r"Agent|RAG|提示词|微调)"
)


def _is_ai_related(article: Article) -> bool:
    """判断文章是否 AI 相关"""
    if article.language == "zh":
        # 中文优先用中文关键词
        if _ZH_KEYWORDS.search(article.title) or _ZH_KEYWORDS.search(article.summary):
            return True
        # 也检查英文关键词（中文文章可能夹带英文术语）
        if _EN_KEYWORDS.search(article.title) or _EN_KEYWORDS.search(article.summary):
            return True
    else:
        if _EN_KEYWORDS.search(article.title) or _EN_KEYWORDS.search(article.summary):
            return True
    return False


def filter_by_keywords(articles: List[Article]) -> List[Article]:
    """只保留标题或摘要包含 AI 关键词的文章"""
    return [a for a in articles if _is_ai_related(a)]


# ============================================================
# 去重
# ============================================================

def deduplicate(articles: List[Article], title_threshold: float = 0.70) -> List[Article]:
    """去重：URL 精确去重 + 标题相似度去重。

    保留先在列表中出现的那条。
    """
    result: List[Article] = []
    seen_urls: set[str] = set()

    for article in articles:
        # 1. URL 精确去重
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)

        # 2. 标题相似度去重
        is_dup = False
        for existing in result:
            if _title_similarity(article.title, existing.title) >= title_threshold:
                is_dup = True
                break
        if is_dup:
            continue

        result.append(article)

    return result


def _title_similarity(title_a: str, title_b: str) -> float:
    """计算两个标题的相似度（0.0 ~ 1.0）"""
    # 标准化：转小写，移除多余空白
    def normalize(t: str) -> str:
        return re.sub(r"\s+", " ", t.lower().strip())

    a = normalize(title_a)
    b = normalize(title_b)
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# ============================================================
# 组合过滤器
# ============================================================

def pipeline(articles: List[Article], hours: int = 24,
             title_threshold: float = 0.70,
             verbose: bool = False) -> List[Article]:
    """执行完整过滤流水线：时间 → 关键词 → 去重"""
    before = len(articles)

    articles = filter_by_time(articles, hours=hours)
    after_time = len(articles)
    if verbose:
        print(f"  时间过滤 ({hours}h): {before} → {after_time} (移除 {before - after_time})")

    articles = filter_by_keywords(articles)
    after_kw = len(articles)
    if verbose:
        print(f"  关键词过滤: {after_time} → {after_kw} (移除 {after_time - after_kw})")

    articles = deduplicate(articles, title_threshold=title_threshold)
    after_dedup = len(articles)
    if verbose:
        print(f"  去重: {after_kw} → {after_dedup} (移除 {after_kw - after_dedup})")

    if verbose:
        print(f"  最终: {before} → {after_dedup}")

    return articles


# ============================================================
# 自检模式
# ============================================================
if __name__ == "__main__":
    from fetcher.base import MockFetcher
    import asyncio

    async def main():
        fetcher = MockFetcher()
        articles = await fetcher.fetch()

        print(f"原始数据: {len(articles)} 条")
        print("=" * 50)

        # 测试 1: 时间过滤
        filtered = filter_by_time(articles, hours=24)
        old_removed = [a for a in articles if a not in filtered]
        print(f"\n[1] 时间过滤 (24h): {len(articles)} → {len(filtered)}")
        if old_removed:
            for a in old_removed:
                print(f"    移除: {a.title}")

        # 测试 2: 关键词过滤
        keyword_filtered = filter_by_keywords(filtered)
        non_ai = [a for a in filtered if a not in keyword_filtered]
        print(f"\n[2] 关键词过滤: {len(filtered)} → {len(keyword_filtered)}")
        if non_ai:
            for a in non_ai:
                print(f"    移除: {a.title}")

        # 测试 3: 去重
        dedup_result = deduplicate(keyword_filtered)
        dups = len(keyword_filtered) - len(dedup_result)
        print(f"\n[3] 去重: {len(keyword_filtered)} → {len(dedup_result)} (移除 {dups} 条重复)")

        # 验证：URL 去重效果
        urls = [a.url for a in dedup_result]
        url_ok = len(urls) == len(set(urls))
        print(f"    URL 唯一性: {'通过' if url_ok else '失败'}")

        # 验证：标题相似去重效果
        # 检查 "OpenAI Announces GPT-5" 和 "阿里云开源 Qwen3" 的变体是否被合并
        gpt5_articles = [a for a in dedup_result if "gpt-5" in a.title.lower()]
        qwen_articles = [a for a in dedup_result if "qwen" in a.title.lower()]
        print(f"    GPT-5 相关保留: {len(gpt5_articles)} 条 (期望 ≤ 2)")
        print(f"    Qwen3 相关保留: {len(qwen_articles)} 条 (期望 ≤ 1)")

        print(f"\n===== 完整流水线 =====")
        result = pipeline(articles, hours=24, verbose=True)
        print(f"\n  结果文章 ({len(result)} 条):")
        for a in result:
            t = a.published_at.strftime("%m-%d %H:%M")
            print(f"    [{a.language}] {a.title[:55]} | {a.source} | {t}")

    asyncio.run(main())
