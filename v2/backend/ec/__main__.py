"""python -m ec：按环境变量启动平台（接口 + 实时推送 + 前端静态文件）。"""

from __future__ import annotations

import logging
import sys

from .settings import Settings, SettingsError


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
    except SettingsError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    import uvicorn

    from .app import create_app

    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
