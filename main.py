"""AI 新闻日报 - 主流程编排。

使用方式:
    python main.py              # dry-run，走完抓取+过滤+总结，不推送
    python main.py --push       # 完整流程，推送到飞书
    python main.py --dry-run    # 同上（默认行为）
    python main.py --help       # 查看帮助
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

from fetcher.rss_fetcher import RSSFetcher, load_sources
from filter import pipeline as filter_pipeline
from summarizer import Summarizer, SummarizerError
from notifier import Notifier, NotifierError

_TZ_UTC8 = timezone(timedelta(hours=8))

logger = logging.getLogger("ainews")


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)


def parse_args():
    parser = argparse.ArgumentParser(
        description="AI 新闻日报 - 抓取过去24小时AI新闻，AI总结后推送飞书",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True,
                       help="仅抓取和总结，不推送（默认）")
    group.add_argument("--push", action="store_true", default=False,
                       help="完整流程，推送到飞书")
    parser.add_argument("--sources", type=str, default="sources.yaml",
                        help="新闻源配置文件路径（默认 sources.yaml）")
    parser.add_argument("--hours", type=int, default=24,
                        help="抓取最近 N 小时的新闻（默认 24）")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="详细日志")
    return parser.parse_args()


def print_stats(stats: dict):
    """打印运行统计"""
    print("\n" + "=" * 50)
    print("📊 运行统计")
    print("=" * 50)
    print(f"  新闻源: {stats['sources']} 个")
    print(f"  抓取耗时: {stats['fetch_time']:.1f}s")
    if stats.get("fetch_errors"):
        print(f"  抓取错误: {stats['fetch_errors']} 个源失败")
    print(f"  原始新闻: {stats['raw_count']} 条")
    print(f"  过滤后: {stats['filtered_count']} 条")
    if "token_usage" in stats:
        print(f"  Token 消耗: {stats['token_usage']}")
    print(f"  总结耗时: {stats['summarize_time']:.1f}s")
    print(f"  AI 精选: {stats['selected_count']} 条")
    print(f"  推送消息: {stats['message_count']} 条")
    print(f"  推送耗时: {stats['notify_time']:.1f}s")
    print(f"  总耗时: {stats['total_time']:.1f}s")
    print(f"  完成时间: {stats['finished_at']}")


async def run(args):
    stats = {
        "sources": 0, "fetch_time": 0, "fetch_errors": 0,
        "raw_count": 0, "filtered_count": 0,
        "summarize_time": 0, "selected_count": 0,
        "message_count": 0, "notify_time": 0, "total_time": 0,
        "finished_at": "",
    }
    t_start = time.time()

    # ========== 1. 抓取 ==========
    print("📡 抓取新闻...")
    t0 = time.time()

    try:
        sources = load_sources(args.sources)
    except FileNotFoundError:
        print(f"❌ 新闻源配置文件不存在: {args.sources}")
        sys.exit(1)
    stats["sources"] = len(sources)
    fetcher = RSSFetcher(sources)
    articles = await fetcher.fetch()

    stats["raw_count"] = len(articles)
    stats["fetch_time"] = time.time() - t0

    # 检查抓取结果
    sources_got = set(a.source for a in articles)
    sources_failed = [s["name"] for s in sources if s["name"] not in sources_got]
    stats["fetch_errors"] = len(sources_failed)

    print(f"  ✓ 抓取完成: {len(articles)} 条, {len(sources_got)}/{len(sources)} 个源成功")
    if sources_failed:
        print(f"  ⚠ 失败源: {', '.join(sources_failed)}")

    if not articles:
        print("❌ 没有抓取到任何新闻，退出")
        sys.exit(1)

    # ========== 2. 过滤 ==========
    print("\n🔍 过滤和去重...")
    articles = filter_pipeline(articles, hours=args.hours, verbose=True)
    stats["filtered_count"] = len(articles)

    if not articles:
        print("❌ 过滤后无新闻，退出")
        sys.exit(0)

    # ========== 3. AI 总结 ==========
    print(f"\n🤖 AI 总结 ({len(articles)} 条新闻)...")
    t0 = time.time()

    dry_run = not args.push
    summarizer = Summarizer(dry_run=dry_run)
    try:
        summary = await summarizer.summarize(articles)
    except SummarizerError as e:
        print(f"❌ AI 总结失败: {e}")
        sys.exit(1)

    stats["summarize_time"] = time.time() - t0

    if dry_run:
        # dry-run 会打印 prompt，无需再打印结果
        stats["selected_count"] = 0
        stats["total_time"] = time.time() - t_start
        now = datetime.now(_TZ_UTC8).strftime("%H:%M:%S")
        stats["finished_at"] = now
        print_stats(stats)
        return

    selected = summary.get("articles", [])
    stats["selected_count"] = len(selected)
    date_str = summary.get("date", "")
    print(f"  ✓ AI 精选 {len(selected)} 条 ({date_str})")

    # 打印标题预览
    for i, a in enumerate(selected, 1):
        print(f"  {i}. {a.get('title', 'N/A')}")

    # ========== 4. 飞书推送 ==========
    print("\n📨 推送飞书...")
    t0 = time.time()

    notifier = Notifier(dry_run=False)
    try:
        results = await notifier.send(summary)
        stats["message_count"] = len(results)
        stats["notify_time"] = time.time() - t0
        print(f"  ✓ 推送成功: {stats['message_count']} 条消息")
    except NotifierError as e:
        print(f"❌ 飞书推送失败: {e}")
        sys.exit(1)

    # ========== 统计 ==========
    stats["total_time"] = time.time() - t_start
    now = datetime.now(_TZ_UTC8).strftime("%H:%M:%S")
    stats["finished_at"] = now
    print_stats(stats)


def main():
    args = parse_args()
    setup_logging(verbose=args.verbose)

    mode = "dry-run (不推送)" if not args.push else "完整流程 (推送飞书)"
    print(f"🤖 AI 新闻日报 | {mode}")
    print(f"   新闻源: {args.sources} | 时间范围: {args.hours}h\n")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
