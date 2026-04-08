# src/tools/pipeline_utils.py
import os
from pathlib import Path
import json

def setup_isolated_build_dir(base_root, op_category, op_name, layout_id=None):
    """
    🛸 [ADPC V4.0] 物理仿真路径隔离工具
    """
    path = Path(base_root) / op_category / op_name
    if layout_id is not None:
        path = path / f"layout_{layout_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path

def apply_bank_overrides(dfg, bank_map):
    """
    🛸 [ADPC V4.0] 动态物理绑定工具
    支持 MIRNode 对象列表或单纯的 dict。
    bank_map: {"x": 0, "y": 1, "constants": 0}
    """
    nodes = dfg if isinstance(dfg, list) else dfg.get("nodes", {}).values()
    
    for node in nodes:
        # 兼容性处理：MIRNode 对象使用 metadata 字段，dict 格式使用 get()
        if hasattr(node, "metadata"):
            meta = node.metadata
        else:
            meta = node.get("metadata", {})
            
        role = meta.get("role", "")
        meta_name = meta.get("meta_name")
        
        if meta_name in bank_map:
            meta["manual_bank"] = bank_map[meta_name]
        elif role == "constant" and "constants" in bank_map:
            meta["manual_bank"] = bank_map["constants"]
            
    return dfg

def save_layout_metadata(build_path, bank_map):
    with open(Path(build_path) / "layout_config.json", "w") as f:
        json.dump(bank_map, f, indent=4)
