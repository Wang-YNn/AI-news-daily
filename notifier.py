"""飞书推送模块：将 AI 总结结果格式化为飞书富文本消息并推送。

使用方式:
    from notifier import Notifier

    n = Notifier(webhook_url="https://...", dry_run=False)
    await n.send(summary_dict)
"""

import json
import logging
import time
from typing import List, Dict, Any, Optional

import httpx

logger = logging.getLogger(__name__)

# 飞书消息体上限约 30KB，保守取 25KB 留余量
_MAX_MESSAGE_BYTES = 25 * 1024
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


class NotifierError(Exception):
    """推送失败"""

    pass


def _build_post_content(summary: dict, page: int = 1, total: int = 1) -> dict:
    """将 AI 总结 dict 转换为飞书 post 格式的 content 体。

    新格式：10 条精选新闻，每条带标题、详细总结、链接、来源
    返回: {"zh_cn": {"title": "...", "content": [[...], [...]]}}
    """
    date = summary.get("date", "")
    articles = summary.get("articles", [])
    total_count = summary.get("total_count", len(articles))

    title = f"🤖 AI 新闻日报 | {date}"
    if total > 1:
        title += f" ({page}/{total})"

    content: list = []

    # 头部标题
    content.append([{"tag": "text", "text": "━━━━━━━━━━━━━━━━━━━━"}])
    content.append([{"tag": "text", "text": f"📰 今日精选 {total_count} 条 AI 新闻\n"}])
    content.append([{"tag": "text", "text": "━━━━━━━━━━━━━━━━━━━━"}])

    for i, a in enumerate(articles, 1):
        title_text = a.get("title", "N/A")
        title_en = a.get("title_en", "")
        summary = a.get("summary", "")
        url = a.get("url", "")
        source = a.get("source", "")

        # 序号 + 中文标题（带链接）
        header = f"\n{i}. {title_text}"
        if url:
            content.append([{"tag": "a", "text": header, "href": url}])
        else:
            content.append([{"tag": "text", "text": header}])

        # 英文原标题
        if title_en and title_en != title_text:
            content.append([{"tag": "text", "text": f"   {title_en}"}])

        # 详细总结
        content.append([{"tag": "text", "text": f"\n{summary}"}])

        # 来源
        content.append([{"tag": "text", "text": f"\n📎 来源: {source}"}])

        # 条目间分隔
        if i < len(articles):
            content.append([{"tag": "text", "text": "\n- - - - - - - - - - - - - - - - - - - -"}])

    # 底部
    content.append([{"tag": "text", "text": "\n━━━━━━━━━━━━━━━━━━━━"}])
    footer = f"🤖 由 DeepSeek 总结生成 | 共收录 {total_count} 条新闻"
    content.append([{"tag": "text", "text": footer}])

    return {
        "zh_cn": {
            "title": title,
            "content": content,
        }
    }


def _message_size(post_content: dict) -> int:
    """估算消息体字节数"""
    return len(json.dumps(post_content, ensure_ascii=False).encode("utf-8"))


def _split_if_needed(summary: dict) -> List[dict]:
    """如果消息体超过大小限制，按文章拆分为多条"""
    articles = summary.get("articles", [])

    # 先尝试不分条
    content = _build_post_content(summary)
    if _message_size(content) <= _MAX_MESSAGE_BYTES:
        return [summary]

    # 分条：每条消息放尽可能多的文章
    parts: List[dict] = []
    remaining = list(articles)

    while remaining:
        batch = []
        while remaining:
            test = {
                "date": summary.get("date", ""),
                "articles": batch + [remaining[0]],
                "total_count": len(articles),
            }
            if _message_size(_build_post_content(test)) <= _MAX_MESSAGE_BYTES:
                batch.append(remaining.pop(0))
            else:
                break
        if batch:
            parts.append({
                "date": summary.get("date", ""),
                "articles": batch,
                "total_count": len(articles),
            })

    logger.info("消息过长，拆分为 %d 条", len(parts))
    return parts


class Notifier:
    """飞书推送器"""

    def __init__(self, webhook_url: str = None, dry_run: bool = False):
        self._webhook_url = webhook_url
        self._dry_run = dry_run

    @property
    def webhook_url(self) -> str:
        if not self._webhook_url:
            from config import config
            self._webhook_url = config.feishu_webhook_url
        return self._webhook_url

    async def send(self, summary: dict) -> List[dict]:
        """发送 AI 总结到飞书，返回每条消息的响应。"""

        parts = _split_if_needed(summary)
        total = len(parts)

        if self._dry_run:
            logger.info("[dry-run] 跳过飞书推送，共 %d 条消息", total)
            for i, part in enumerate(parts, 1):
                content = _build_post_content(part, page=i, total=total)
                print(f"\n===== 飞书消息 {i}/{total} ({_message_size(content)} bytes) =====")
                print(json.dumps({"msg_type": "post", "content": {"post": content}},
                                 ensure_ascii=False, indent=2))
            return [{"dry_run": True}]

        results = []
        async with httpx.AsyncClient(timeout=30) as client:
            for i, part in enumerate(parts, 1):
                body = self._build_message(part, page=i, total=total)
                result = await self._post_with_retry(client, body)
                results.append(result)
                logger.info("飞书推送 %d/%d 成功", i, total)

        logger.info("飞书推送完成: %d 条消息, 共 %d 条新闻",
                     total, summary.get("total_count", 0))
        return results

    def _build_message(self, part: dict, page: int = 1, total: int = 1) -> dict:
        """构建飞书消息体"""
        post_content = _build_post_content(part, page=page, total=total)
        body = {
            "msg_type": "post",
            "content": {"post": post_content},
        }
        return body

    async def _post_with_retry(self, client: httpx.AsyncClient, body: dict) -> dict:
        """发送消息，带重试"""
        last_error = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.post(self.webhook_url, json=body)
                resp_data = response.json()
                code = resp_data.get("code", -1)
                if code == 0:
                    return resp_data
                else:
                    msg = resp_data.get("msg", "未知错误")
                    raise NotifierError(f"飞书返回错误: code={code}, msg={msg}")
            except NotifierError:
                raise
            except Exception as e:
                last_error = e
                logger.warning("飞书推送失败 (第 %d/%d 次): %s", attempt, _MAX_RETRIES, e)
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    time.sleep(delay)

        raise NotifierError(f"飞书推送失败，已重试 {_MAX_RETRIES} 次: {last_error}")


# ============================================================
# 测试消息模板（不依赖 AI 总结）
# ============================================================

_MOCK_SUMMARY = {
    "date": "2026-05-30",
    "total_count": 5,
    "articles": [
        {
            "title": "OpenAI 发布 GPT-5，原生多模态能力成为最大亮点",
            "title_en": "OpenAI Announces GPT-5 with Multimodal Capabilities",
            "summary": "OpenAI 于今日凌晨正式发布新一代大模型 GPT-5。该模型首次实现了图像、音频与视频的原生理解能力，不再依赖外部模块进行多模态处理。在多项基准测试中，GPT-5 的推理能力较 GPT-4 提升了约 40%，尤其在数学和代码生成领域表现突出。业内分析认为，这标志着大模型竞赛进入新的阶段，多模态将成为标配。",
            "url": "https://example.com/gpt5",
            "source": "TechCrunch",
        },
        {
            "title": "阿里云开源 Qwen3 系列模型，参数规模最高达 720B",
            "title_en": "Alibaba Cloud Open-Sources Qwen3 Series Models",
            "summary": "阿里云通义千问团队宣布开源 Qwen3 系列大模型，参数规模从 7B 到 720B 不等。旗舰版 Qwen3-720B 在 MMLU、HumanEval 等评测中表现接近 GPT-4 水平，而 7B 版本在消费级显卡上即可运行。此次开源采用 Apache 2.0 协议，允许商用。这是国内首个参数规模突破 700B 的开源模型，引发开发者社区广泛关注。",
            "url": "https://example.com/qwen3",
            "source": "量子位",
        },
        {
            "title": "欧盟 AI 法案正式通过，全球首部全面 AI 监管法规落地",
            "title_en": "EU Passes Comprehensive AI Regulation Framework",
            "summary": "欧洲议会以压倒性多数投票通过了《人工智能法案》，成为全球首部全面监管 AI 的法律框架。该法案按风险等级将 AI 应用分为四类，对高风险应用提出了严格的透明度、可解释性和人工监督要求。法案还明确禁止了社交评分、实时生物识别监控等特定 AI 应用。法案将在未来 6-36 个月分阶段生效，预计将对全球 AI 产业合规产生深远影响。",
            "url": "https://example.com/eu-ai-act",
            "source": "MIT Tech Review",
        },
        {
            "title": "苹果将在 iOS 20 中深度集成端侧大语言模型",
            "title_en": "Apple Integrates On-Device LLM in iOS 20",
            "summary": "据知情人士透露，苹果计划在即将发布的 iOS 20 中全面集成端侧大语言模型，覆盖 Siri、备忘录、邮件等多个系统应用。该模型可在 iPhone 15 Pro 及以上机型本地运行，无需联网即可完成文本摘要、智能回复、图片生成等任务。苹果强调端侧推理可保护用户隐私，所有数据处理均在设备上完成。这一举措预计将推动端侧 AI 芯片和模型的进一步发展。",
            "url": "https://example.com/ios-llm",
            "source": "The Verge",
        },
        {
            "title": "Nvidia 发布 B200 GPU，AI 训练速度提升 4 倍",
            "title_en": "Nvidia Unveils B200 GPU for AI Training",
            "summary": "英伟达在年度 GTC 大会上发布了新一代 Blackwell B200 GPU。该芯片采用台积电 3nm 工艺，集成 2080 亿个晶体管，AI 训练性能较上一代 H100 提升 4 倍，能效比提升 2 倍。B200 支持 192GB HBM3e 显存，可单卡运行万亿参数级模型。英伟达同时宣布了基于 B200 的 GB200 超级芯片，将 Grace CPU 与 B200 GPU 结合，专为大模型训练和推理优化。",
            "url": "https://example.com/b200",
            "source": "VentureBeat",
        },
    ],
}


# ============================================================
# 自检模式
# ============================================================
if __name__ == "__main__":
    import asyncio
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    async def main():
        dry_run = "--dry-run" in sys.argv
        push = "--push" in sys.argv

        n = Notifier(dry_run=dry_run)

        # 测试 1: 模板渲染
        if dry_run:
            print("===== 消息模板预览 (dry-run) =====")
            await n.send(_MOCK_SUMMARY)

        # 测试 2: 长消息拆分
        if "--stress" in sys.argv:
            print("\n===== 长消息拆分测试 =====")
            big = {
                **_MOCK_SUMMARY,
                "articles": _MOCK_SUMMARY["articles"] * 6,  # 30 条
            }
            n_dry = Notifier(dry_run=True)
            await n_dry.send(big)

        # 测试 3: 真实推送
        if push:
            print("\n===== 真实推送 =====")
            n_real = Notifier(dry_run=False)
            await n_real.send(_MOCK_SUMMARY)
            print("推送完成，请检查飞书消息")

    asyncio.run(main())
