# -*- coding: utf-8 -*-
"""统一管理运行时路径。

打包成 exe 后，__file__ 指向临时解包目录，数据必须写到 exe 所在目录，
否则用户下次启动会发现持仓丢了。
"""

import os
import sys


def is_frozen():
    return getattr(sys, "frozen", False)


def base_dir():
    """可写数据的根目录（exe 所在目录 / 项目根目录）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    """只读静态资源根目录（PyInstaller 解包目录 / 项目根目录）。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", base_dir())
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_dir(path):
    if path and not os.path.isdir(path):
        try:
            os.makedirs(path)
        except Exception:
            pass
    return path


def data_dir():
    return ensure_dir(os.path.join(base_dir(), "data"))


def reports_dir():
    return ensure_dir(os.path.join(base_dir(), "reports"))


def web_dir():
    return os.path.join(resource_dir(), "web")


def data_file(name):
    return os.path.join(data_dir(), name)
