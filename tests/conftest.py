# -*- coding: utf-8 -*-
"""统一把 src 加入 sys.path（此前 17 个测试文件各自重复 insert）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
