# backend/app_entry.py
# PyInstaller 打包入口：单端口启动（无 reload）+ 自动开浏览器。
# 开发时不用此文件，直接 python run.py（带 reload + 前端 vite 5173）。
import os
import sys
import threading
import webbrowser

# 确保 backend 目录在 sys.path 首位（PyInstaller 打包后通常已处理，这里兜底）
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _open_browser():
    """服务就绪后自动打开默认浏览器。"""
    try:
        webbrowser.open("http://127.0.0.1:8000")
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    # 显式 import app 对象，让 PyInstaller 能静态追踪到 main 模块及其依赖。
    # 用对象形式（而非字符串 "main:app"）避免 PyInstaller 漏掉 main 的隐藏 import。
    from main import app

    print("=" * 60)
    print("  Data Analysis Agent")
    print("  服务地址: http://127.0.0.1:8000")
    print("  首次使用请在网页『🤖 大模型配置』中填写 API Key")
    print("  按 Ctrl+C 退出")
    print("=" * 60)

    # 延迟开浏览器，等 uvicorn 监听就绪
    threading.Timer(1.5, _open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_level="info")
