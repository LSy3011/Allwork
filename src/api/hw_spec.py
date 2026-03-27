from typing import Dict, List, Optional, Union
from pydantic import BaseModel, Field
import yaml
from pathlib import Path

class ModuleField(BaseModel):
    name: str
    bits: int
    desc: Optional[str] = None

class SIMDLaneConfig(BaseModel):
    lanes: int
    formula: str

class ModuleSpec(BaseModel):
    type: str
    latency: int
    inputs: Optional[List[str]] = None
    outputs: Optional[List[str]] = None
    code_bits: int
    code_fields: List[ModuleField]
    parallelism: Optional[Dict[str, SIMDLaneConfig]] = None

class IQConfig(BaseModel):
    module: str
    count: int = 1
    EN_LOOPS: bool = False
    BITS_LOOPS: int = 0
    EN_DLY: bool = False
    BITS_DLY: int = 0
    BITS_CODE: int

class HardwareSpec(BaseModel):
    params: Dict[str, Union[int, str, bool]] = Field(default_factory=dict)
    delays: Dict[str, int] = Field(default_factory=dict)
    modules: Dict[str, ModuleSpec] = Field(default_factory=dict)
    iqs: Dict[str, IQConfig] = Field(default_factory=dict)
    rtovr_map: Dict[str, Dict[int, str]] = Field(default_factory=dict)

    @classmethod
    def load(cls, config_dir: str = "hw_config") -> "HardwareSpec":
        base_path = Path(config_dir)
        spec_data = {}

        # 1. 基础参数
        with open(base_path / "hw_params.yaml", "r") as f:
            spec_data["params"] = yaml.safe_load(f)["hardware_params"]

        # 2. 流水线延迟
        with open(base_path / "pipeline_delays.yaml", "r") as f:
            spec_data["delays"] = yaml.safe_load(f)["pipeline_delays"]

        # 3. 模块定义
        with open(base_path / "modules.yaml", "r") as f:
            spec_data["modules"] = yaml.safe_load(f)["modules"]

        # 4. IQ 配置
        with open(base_path / "iq_config.yaml", "r") as f:
            spec_data["iqs"] = yaml.safe_load(f)["iq_config"]

        # 5. Rtovr 映射矩阵
        with open(base_path / "rtovr_config.yaml", "r") as f:
            spec_data["rtovr_map"] = yaml.safe_load(f)["rtovr_connectivity"]

        return cls(**spec_data)

    def get_module(self, name: str) -> ModuleSpec:
        return self.modules[name]

    def get_rtovr_dr_name(self, index: int) -> str:
        return self.rtovr_map["consumers_dr"][index]

    def get_rtovr_dvr_name(self, index: int) -> str:
        return self.rtovr_map["producers_dvr"][index]
