"""数据模型 & 抓取器抽象基类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List


# ============================================================
# 统一数据模型
# ============================================================

@dataclass
class Article:
    """新闻文章统一数据结构"""

    title: str
    url: str
    summary: str
    source: str
    published_at: datetime
    language: str  # "zh" | "en"

    def __repr__(self) -> str:
        t = self.published_at.strftime("%Y-%m-%d %H:%M")
        return f"Article(title={self.title[:40]}..., source={self.source}, lang={self.language}, time={t})"


# ============================================================
# 抽象基类
# ============================================================

class ArticleFetcher(ABC):
    """抓取器抽象基类，所有抓取器需实现 fetch()"""

    @abstractmethod
    async def fetch(self) -> List[Article]:
        """异步抓取新闻，返回 Article 列表"""
        ...


# ============================================================
# MockFetcher — 用于独立测试后续模块（过滤/总结/推送）
# ============================================================

_NOW = datetime.now(timezone.utc)
_TZ_UTC8 = timezone(timedelta(hours=8))

# fmt: off
_MOCK_ARTICLES = [
    # === 英文，24h 内，AI 相关 ===
    ("OpenAI Announces GPT-5 with Multimodal Capabilities", "https://example.com/gpt5", "OpenAI released GPT-5, featuring native image, audio, and video understanding.", "TechCrunch", "en", _NOW - timedelta(hours=2)),
    ("Google DeepMind Unveils Gemini 3.0", "https://example.com/gemini3", "Google's latest model surpasses previous benchmarks in reasoning and coding.", "The Verge", "en", _NOW - timedelta(hours=4)),
    ("Meta Releases Llama 4 Open-Source Models", "https://example.com/llama4", "Meta launches Llama 4 with 400B parameters under Apache 2.0 license.", "VentureBeat", "en", _NOW - timedelta(hours=5)),
    ("Anthropic Introduces Claude 4 with Enhanced Safety", "https://example.com/claude4", "New Claude model focuses on reducing hallucinations and improving alignment.", "Ars Technica", "en", _NOW - timedelta(hours=6)),
    ("Nvidia Unveils B200 GPU for AI Training", "https://example.com/b200", "Next-gen Blackwell GPU promises 4x training speed improvement.", "VentureBeat", "en", _NOW - timedelta(hours=7)),
    ("AI Startup Raises $500M to Build Enterprise Agents", "https://example.com/ai-startup", "Stealth startup emerges with massive funding for autonomous enterprise AI.", "TechCrunch", "en", _NOW - timedelta(hours=8)),
    ("EU Passes Comprehensive AI Regulation Framework", "https://example.com/eu-ai-act", "European Union finalizes AI Act, setting global precedent for AI governance.", "MIT Tech Review", "en", _NOW - timedelta(hours=9)),
    ("Apple Integrates On-Device LLM in iOS 20", "https://example.com/ios-llm", "Apple brings local large language model capabilities to iPhone.", "The Verge", "en", _NOW - timedelta(hours=10)),
    # 同一 URL 的重复（测试 URL 精确去重）
    ("OpenAI Announces GPT-5 with Multimodal Capabilities", "https://example.com/gpt5", "Duplicate URL entry.", "DuplicateSource", "en", _NOW - timedelta(hours=3)),
    # 标题高度相似的新闻（测试标题去重）
    ("OpenAI Just Announced GPT-5, a New Multimodal AI", "https://example.com/gpt5-alt", "Another source reporting the same GPT-5 release.", "The Verge", "en", _NOW - timedelta(hours=2)),

    # === 中文，24h 内，AI 相关 ===
    ("百度发布「文心一言 5.0」对标 GPT-5", "https://example.com/wenxin5", "百度最新大模型在多模态理解上取得突破性进展。", "机器之心", "zh", _NOW - timedelta(hours=3)),
    ("阿里云开源 Qwen3 系列模型", "https://example.com/qwen3", "阿里开源通义千问第三代，参数规模从 7B 到 720B。", "量子位", "zh", _NOW - timedelta(hours=5)),
    ("国内首个 AI 大模型备案新规出台", "https://example.com/ai-regulation", "网信办发布生成式 AI 服务管理新规，明确模型上线要求。", "虎嗅", "zh", _NOW - timedelta(hours=6)),
    ("字节跳动组建 AGI 研究院", "https://example.com/bytedance-agi", "字节跳动宣布成立通用人工智能研究院，由顶级学者领衔。", "36氪", "zh", _NOW - timedelta(hours=8)),
    ("华为昇腾 AI 芯片性能追平 A100", "https://example.com/huawei-ascend", "华为最新 AI 训练芯片在 MLPerf 基准测试中表现出色。", "雷锋网", "zh", _NOW - timedelta(hours=12)),
    ("特斯拉 Optimus 机器人进入量产阶段", "https://example.com/optimus", "马斯克宣布人形机器人将在工厂内部署，整合端到端 AI 模型。", "IT之家", "zh", _NOW - timedelta(hours=10)),
    # 中文标题相似去重
    ("阿里云正式开源 Qwen3 系列大模型，参数最高 720B", "https://example.com/qwen3-dup", "同一事件另一报道。", "机器之心", "zh", _NOW - timedelta(hours=6)),

    # === 超过 24 小时（测试时间过滤） ===
    ("Old AI News from Last Week", "https://example.com/old-ai", "This article is 48 hours old and should be filtered out.", "TechCrunch", "en", _NOW - timedelta(hours=48)),
    ("上周发表的 AI 论文解读", "https://example.com/old-paper", "这篇内容已超过 24 小时，应该被时间过滤器移除。", "机器之心", "zh", _NOW - timedelta(hours=30)),

    # === AI 无关（测试关键词过滤） ===
    ("New iPhone 18 Camera Design Leaked", "https://example.com/iphone-camera", "Apple's next iPhone may feature a radical new camera layout.", "The Verge", "en", _NOW - timedelta(hours=4)),
    ("全球气温再创新高 气候危机加剧", "https://example.com/climate", "世界气象组织发布最新报告，全球变暖趋势持续。", "虎嗅", "zh", _NOW - timedelta(hours=5)),
]
# fmt: on


class MockFetcher(ArticleFetcher):
    """模拟抓取器：返回 20 条预设文章，用于开发测试"""

    async def fetch(self) -> List[Article]:
        articles = []
        for title, url, summary, source, lang, published_at in _MOCK_ARTICLES:
            articles.append(Article(
                title=title,
                url=url,
                summary=summary,
                source=source,
                published_at=published_at,
                language=lang,
            ))
        return articles


# ============================================================
# 自检模式
# ============================================================
if __name__ == "__main__":
    import asyncio

    async def main():
        fetcher = MockFetcher()
        articles = await fetcher.fetch()

        print(f"MockFetcher 返回 {len(articles)} 条文章\n")

        # 统计
        zh_count = sum(1 for a in articles if a.language == "zh")
        en_count = sum(1 for a in articles if a.language == "en")
        within_24h = sum(1 for a in articles if a.published_at > _NOW - timedelta(hours=24))
        has_dup_url = len(articles) != len(set(a.url for a in articles))

        print(f"  中文: {zh_count} 条")
        print(f"  英文: {en_count} 条")
        print(f"  24h 内: {within_24h} 条")
        print(f"  24h 外: {len(articles) - within_24h} 条")
        print(f"  含重复 URL: {has_dup_url}")
        print()

        # 验证数据完整性
        errors = []
        for i, a in enumerate(articles):
            if not a.title:
                errors.append(f"  [{i}] title 为空")
            if not a.url:
                errors.append(f"  [{i}] url 为空")
            if not a.source:
                errors.append(f"  [{i}] source 为空")
            if not a.published_at:
                errors.append(f"  [{i}] published_at 为空")
            if a.language not in ("zh", "en"):
                errors.append(f"  [{i}] language 非法: {a.language}")

        if errors:
            print("数据完整性检查失败:")
            for e in errors:
                print(e)
        else:
            print("数据完整性检查通过（全部字段正确）")

        # 打印样例
        print("\n样例文章:")
        for a in articles[:3]:
            print(f"  {a}")

    asyncio.run(main())
