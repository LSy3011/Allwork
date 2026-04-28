import sys
from pathlib import Path
ws = Path.cwd()
sys.path.append(str(ws / "src"))
from api.hw_spec import HardwareSpec

spec = HardwareSpec.load("hw_config")
dvr_map = spec.get_dvr_map()
print("All Unit Connections in DVR_MAP:")
for unit, ports in dvr_map.items():
    print(f"{unit}: {ports}")
