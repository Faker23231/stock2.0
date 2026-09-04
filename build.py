# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本：python build.py

产物：dist/USStockDesk.exe（单文件，含 web 静态资源）
"""
import os
import sys
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))

NAME = "USStockDesk"

# 用当前解释器配套的 pyinstaller
try:
    import PyInstaller.__main__  # noqa
    pyinstaller = ["pyinstaller"]
except ImportError:
    pyinstaller = [sys.executable, "-m", "PyInstaller"]

def main():
    dist = os.path.join(ROOT, "dist")
    build = os.path.join(ROOT, "build")
    spec = os.path.join(ROOT, NAME + ".spec")
    # 注意：只清理 build 工作目录 + 旧 spec/exe，绝不 rmtree(dist)。
    # dist/data、dist/reports、dist/logs 是 exe 运行时的用户真实数据，整目录删除会丢持仓。
    if os.path.isdir(build):
        shutil.rmtree(build, ignore_errors=True)
    if os.path.isfile(spec):
        try:
            os.remove(spec)
        except OSError as e:
            print("[WARN] spec 删除失败: %s" % e)
    old_exe = os.path.join(dist, NAME + ".exe")
    if os.path.isfile(old_exe):
        try:
            os.remove(old_exe)
        except OSError as e:
            print("[FAIL] 旧 exe 仍被占用无法覆盖，请先关闭正在运行的 USStockDesk 再打包：%s" % e)
            return 1

    # Windows 分隔符用 ; ，Linux/macOS 用 :
    sep = ";" if os.name == "nt" else ":"
    args = pyinstaller + [
        "--noconfirm", "--clean",
        "--onefile",
        "--noconsole",                 # 无控制台窗口，日志写 logs/console.log
        "--name", NAME,
        "--distpath", dist,
        "--workpath", build,
        "--specpath", ROOT,
        "--add-data", os.path.join("web", "index.html") + sep + "web",
        "--add-data", os.path.join("web", "app.js") + sep + "web",
        "--add-data", os.path.join("web", "style.css") + sep + "web",
        "--add-data", os.path.join("web", "manifest.webmanifest") + sep + "web",
        "--add-data", os.path.join("web", "sw.js") + sep + "web",
        "--add-data", os.path.join("web", "icon-192.png") + sep + "web",
        "--add-data", os.path.join("web", "icon-512.png") + sep + "web",
        "--add-data", os.path.join("web", "apple-touch-icon.png") + sep + "web",
        "--add-data", os.path.join("web", "vendor", "echarts.min.js") + sep + os.path.join("web", "vendor"),
        os.path.join(ROOT, "main.py"),
    ]
    print(">>", " ".join(args))
    rc = os.system(" ".join('"%s"' % a if (" " in a or "/" in a) else a for a in args))
    exe = os.path.join(dist, NAME + ".exe")
    if os.path.isfile(exe):
        print("\n[OK] 打包完成：%s （%.1f MB）" % (exe, os.path.getsize(exe) / 1048576))
    else:
        print("\n[FAIL] 未找到产物 exe，请检查上方错误输出")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
