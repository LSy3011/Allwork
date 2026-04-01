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
            if not (base / f).exists(): return {}
            with open(base / f, "r") as stream:
                return yaml.safe_load(stream)
        
        # 1. 基础载入
        spec_data = {
            "params": load_yaml("hw_params.yaml")["hardware_params"],
            "modules": load_yaml("modules.yaml")["modules"],
            "iqs": load_yaml("iq_config.yaml")["iq_config"],
            "delays": load_yaml("pipeline_delays.yaml")["pipeline_delays"],
            "system_core": load_yaml("specs/system/system_core.yaml")
        }
        
        # 2. 深度加载每个模块的详细物理规格 (Specs Override Modules)
        modules_data = spec_data["modules"]
        for mname in modules_data:
            # 尝试加载 specs/units/<mname>.yaml
            spec_file = base / "specs" / "units" / f"{mname.lower()}.yaml"
            if spec_file.exists():
                with open(spec_file, "r") as f:
                    u_data = yaml.safe_load(f)
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
