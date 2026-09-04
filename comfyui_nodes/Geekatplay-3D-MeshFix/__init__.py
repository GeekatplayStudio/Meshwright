"""
Meshwright ComfyUI Custom Node Integration — Geekatplay Studio
Author: Vladimir Chopine
"""
import json
import os
import sys

# Locate Meshwright engine
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_THIS_DIR, "meshwright_config.json")

meshwright_root = None
meshwright_venv = None

if os.path.exists(_CONFIG_PATH):
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _cfg = json.load(f)
            meshwright_root = _cfg.get("meshwright_root")
            meshwright_venv = _cfg.get("meshwright_venv")
    except Exception as e:
        print(f"[Meshwright] Warning: could not parse meshwright_config.json: {e}")

# Fallback: check parent directories in case nodes are run in-place
if not meshwright_root or not os.path.exists(meshwright_root):
    # Check if ../../engine exists
    _repo_candidate = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
    if os.path.exists(os.path.join(_repo_candidate, "engine", "service.py")):
        meshwright_root = _repo_candidate

# Add Meshwright root to sys.path
if meshwright_root and os.path.isdir(meshwright_root) and meshwright_root not in sys.path:
    sys.path.insert(0, meshwright_root)

# If Meshwright virtual environment exists, make its site-packages available as fallback
if meshwright_venv and os.path.isdir(meshwright_venv):
    _site_packages = os.path.join(meshwright_venv, "Lib", "site-packages")
    if os.path.isdir(_site_packages) and _site_packages not in sys.path:
        sys.path.append(_site_packages)

from .nodes import NODE_CLASS_MAPPINGS as MESH_NODE_CLASS_MAPPINGS
from .nodes import NODE_DISPLAY_NAME_MAPPINGS as MESH_NODE_DISPLAY_NAME_MAPPINGS
from .texture_nodes import (
    TEXTURE_NODE_CLASS_MAPPINGS,
    TEXTURE_NODE_DISPLAY_NAME_MAPPINGS,
)

NODE_CLASS_MAPPINGS = {**MESH_NODE_CLASS_MAPPINGS, **TEXTURE_NODE_CLASS_MAPPINGS}
NODE_DISPLAY_NAME_MAPPINGS = {**MESH_NODE_DISPLAY_NAME_MAPPINGS, **TEXTURE_NODE_DISPLAY_NAME_MAPPINGS}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

# Optional ComfyUI web extension directory
_web_dir = os.path.join(_THIS_DIR, "web")
if os.path.isdir(_web_dir):
    WEB_DIRECTORY = "./web"
