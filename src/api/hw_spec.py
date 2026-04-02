from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class HardwareParams(BaseModel):
    PARAM_SMC_NUM: int
    PARAM_SMC_VECTOR_WIDTH: int
    PARAM_IQ_DEPTH: int
    PARAM_OUTSTANDING_INST_NUM: int
    PARAM_EMBED_MAX_LEVEL: int
    PARAM_SMC_AXI_EN: int
    PARAM_UR_BANK_NUM: int
    PARAM_UR_BANK_DEPTH: int
    
    # 统合 ISA 头部参数
    ISA_HEADER_BITS: int = 41
    ISA_HEADER_FIELDS: Dict[str, int] = Field(default_factory=dict)

class ModuleField(BaseModel):
    name: str
    bits: int
    default: int = 0
    encoding: Dict[Any, Any] = Field(default_factory=dict)

class ModuleSpec(BaseModel):
    type: str
    latency: int
    inputs: List[str] = []   # 设为可选，增强容错性
    outputs: List[str] = []
    code_bits: int
    code_fields: List[ModuleField] = []

class IQConfig(BaseModel):
    module: str
    count: int = 1
    EN_LOOPS: bool = False
    BITS_LOOPS: int = 0
    EN_DLY: bool = False
    BITS_DLY: int = 0
    EN_INC: bool = False
    BITS_INC: int = 0
    EN_COND: bool = False
    BITS_COND: int = 0
    BITS_EMBED: int = 0
    BITS_EMBED_END: int = 0
    BITS_CODE: int = 0

class HardwareSpec(BaseModel):
    params: HardwareParams
    modules: Dict[str, ModuleSpec]
    iqs: Dict[str, IQConfig]
    delays: Dict[str, int]
    system_core: Optional[Dict[str, Any]] = None

    @classmethod
    def load(cls, config_dir: str):
        import yaml
        from pathlib import Path
        base = Path(config_dir)
        
        def load_yaml(f):
            p = base / f
            if not p.exists(): return {}
            with open(p, "r") as stream:
                return yaml.safe_load(stream)
        
        # 🛸 V2.4 大一统加载尝试 (SSoT)
        master_spec_path = base / "specs" / "aiacc_v2.4_master.yaml"
        master_data = None
        if master_spec_path.exists():
            with open(master_spec_path, "r") as f:
                master_data = yaml.safe_load(f)
        
        # 1. 基础载入
        spec_data = {
            "params": load_yaml("hw_params.yaml")["hardware_params"],
            "modules": load_yaml("modules.yaml")["modules"],
            "iqs": load_yaml("iq_config.yaml")["iq_config"],
            "delays": load_yaml("pipeline_delays.yaml")["pipeline_delays"],
            "system_core": master_data["system_core"] if master_data else load_yaml("specs/system/system_core.yaml")
        }
        
        # 2. 深度加载每个模块的详细物理规格 (Specs Override Modules)
        modules_data = spec_data["modules"]
        for mname in modules_data:
            u_data = None
            if master_data and mname.upper() in master_data["functional_units"]:
                u_data = master_data["functional_units"][mname.upper()]
            else:
                # 回退到独立 YAML (保持兼容)
                spec_file = base / "specs" / "units" / f"{mname.lower()}.yaml"
                if spec_file.exists():
                    with open(spec_file, "r") as f:
                        u_data = yaml.safe_load(f)
            
            if u_data:
                # 映射详细字段到 ModuleSpec 结构
                if "micro_instructions" in u_data:
                    fields = []
                    for f_info in u_data["micro_instructions"]:
                        fields.append({
                            "name": f_info.get("field", f_info.get("name")),
                            "bits": f_info.get("width", f_info.get("bits")),
                            "default": f_info.get("default", 0),
                            "encoding": f_info.get("encoding", {})
                        })
                    modules_data[mname]["code_fields"] = fields
                    modules_data[mname]["latency"] = u_data.get("latency", modules_data[mname]["latency"])

        return cls(**spec_data)

        return cls(**spec_data)

    @property
    def config(self):
        """兼容层：将 system_core 暴露为 config 字典"""
        return self.system_core if self.system_core else {}

    def get_iq_ids(self, unit_type: str) -> List[int]:
        """
        根据物理单元类型获取其下所有实例的 IQ ID。
        """
        registry = self.config.get("iq_registry", {})
        instances = self.config.get("resource_constraints", {}).get("unit_instances", {})
        
        # 1. 处理 UR 特殊端口 (动态从 registry 映射地址)
        if unit_type.upper() == "UR_READ":
            base = registry.get("UR_READ", 30)
            count = self.config["resource_constraints"].get("total_read_ports", 4)
            return [base + i for i in range(count)]
        if unit_type.upper() == "UR_WRITE":
            base = registry.get("UR_WRITE", 35)
            count = self.config["resource_constraints"].get("total_write_ports", 4)
            return [base + i for i in range(count)]
            
        # 2. 处理 ALU 常规端口 (基于 Registry 偏移)
        if unit_type.upper() in instances:
            base_id = registry.get(unit_type.upper(), 0)
            count = instances[unit_type.upper()]
            return [base_id + i for i in range(count)]
        
        return []

    def get_iq_name(self, iq_id: int) -> str:
        """
        反向寻址：根据物理 IQ ID 获取其实例化名称 (用于生成的 HEX 文件名)。
        """
        # 1. 探测 UR 端口 (动态回溯)
        registry = self.config.get("iq_registry", {})
        base_r = registry.get("UR_READ", 30)
        base_w = registry.get("UR_WRITE", 35)
        
        if base_r <= iq_id < base_r + 4: return f"ur-r{iq_id - base_r}"
        if base_w <= iq_id < base_w + 4: return f"ur-w{iq_id - base_w}"
        
        # 2. 探测 ALU 端口
        # (已存在逻辑保持不变)
        registry = self.config.get("iq_registry", {})
        instances = self.config.get("resource_constraints", {}).get("unit_instances", {})
        
        for u_type, base_id in registry.items():
            count = instances.get(u_type, 0)
            if base_id <= iq_id < base_id + count:
                idx = iq_id - base_id
                return f"{u_type.lower()}-{idx}" if count > 1 else u_type.lower()
        
        return "unknown"
