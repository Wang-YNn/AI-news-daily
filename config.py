"""配置模块：从环境变量读取所有配置，缺失时明确报错退出。

使用方式:
    from config import config
    print(config.deepseek_api_key)   # 脱敏后的 key
"""

import os
import sys
from dataclasses import dataclass


REQUIRED_VARS = [
    "DEEPSEEK_API_KEY",
    "FEISHU_WEBHOOK_URL",
]


class ConfigError(Exception):
    """配置缺失异常"""

    pass


@dataclass
class Config:
    deepseek_api_key: str
    feishu_webhook_url: str

    @staticmethod
    def mask(value: str, visible: int = 3) -> str:
        """脱敏显示：展示前 visible 个字符，其余用 *** 替代"""
        if len(value) <= visible:
            return value
        return value[:visible] + "***"


def load_config() -> Config:
    """从环境变量加载配置，缺失则抛出 ConfigError"""
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]

    if missing:
        print(f"错误: 缺少环境变量: {', '.join(missing)}")
        print("请参考 .env.example 配置所需变量，例如:")
        print("  export DEEPSEEK_API_KEY=sk-xxx")
        print("  export FEISHU_WEBHOOK_URL=https://open.feishu.cn/...")
        sys.exit(1)

    return Config(
        deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
        feishu_webhook_url=os.environ["FEISHU_WEBHOOK_URL"],
    )


# 模块级单例
try:
    config = load_config()
except ConfigError:
    sys.exit(1)


# ============================================================
# 自检模式：直接运行 python config.py 验证配置加载是否正常
# ============================================================
if __name__ == "__main__":
    print("配置加载成功")
    print(f"  DEEPSEEK_API_KEY   = {Config.mask(config.deepseek_api_key)}")
    print(f"  FEISHU_WEBHOOK_URL = {Config.mask(config.feishu_webhook_url)}")
    print("所有必需变量已就绪。")
