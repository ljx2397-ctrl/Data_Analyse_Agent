# backend/run.py
# 后端启动脚本。直接运行：python run.py
# 等价于：uvicorn main:app --host 0.0.0.0 --port 8000 --reload
import os
import sys

# 确保 backend 目录在 sys.path 首位
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[BACKEND_DIR],
    )
