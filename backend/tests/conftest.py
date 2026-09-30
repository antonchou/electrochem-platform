"""pytest 全局夹具。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _isolate_derived_dir(tmp_path, monkeypatch):
    """拟合/标定报告一律写临时目录。

    未显式设置 EC_DERIVED_DIR 的用例会落到仓库 data/derived/，覆盖本机真实实验的
    同编号报告（09-30 审查 R3-8）。用例自己再 setenv 会覆盖这里的默认值。
    """
    monkeypatch.setenv("EC_DERIVED_DIR", str(tmp_path / "derived"))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """带 lifespan 的 TestClient：启动/关闭持久化服务；DB 指向临时文件。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c
