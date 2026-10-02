"""Entry point. Run from the project root: python -m backend.main"""
import uvicorn

from backend.app import create_app

app = create_app()

if __name__ == "__main__":
    # 127.0.0.1 only (local demo). WebSocket frames are capped like the plugin's (1 MiB).
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True, ws_max_size=1024 * 1024)
