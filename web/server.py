"""
星衍AI放射质控软件 · Web 版后端服务
前后端分离架构：Flask REST API + 静态前端（SPA）

启动方式：
  cd web && python server.py
  或 python -m web.server

访问：http://localhost:5000
"""

import os
import sys

# 将 src/ 加入路径，使 engine/samplelib/ris 等模块可导入
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from flask import Flask, jsonify, request
from flask_cors import CORS

from api.qc import qc_bp
from api.samples import samples_bp
from api.ris import ris_bp


def create_app():
    app = Flask(
        __name__,
        static_folder="static",
        static_url_path="/static",
        template_folder="templates",
    )
    CORS(app)  # 开发期全开 CORS

    # 注册 API 蓝图
    app.register_blueprint(qc_bp, url_prefix="/api/qc")
    app.register_blueprint(samples_bp, url_prefix="/api/samples")
    app.register_blueprint(ris_bp, url_prefix="/api/ris")

    # 前端 SPA 路由兜底（所有非 API 请求返回 index.html）
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def SPA_fallback(path):
        if path.startswith("api/") or path.startswith("static/"):
            return "Not Found", 404
        return app.send_static_file("index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    print("=" * 50)
    print("星衍AI放射质控 · Web 版")
    print("http://localhost:5001")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5001, debug=True)
