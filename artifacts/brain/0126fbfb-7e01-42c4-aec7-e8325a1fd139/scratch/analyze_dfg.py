import json
from pathlib import Path

def main():
    # Since I couldn't find stage_b_mapped.json, I'll search for any json in the build dir
    build_dir = Path("build/alignment/horner_3rd")
    json_files = list(build_dir.glob("*.json"))
    print(f"Found JSON files: {json_files}")
    
    for jf in json_files:
        print(f"\nAnalyzing {jf.name}:")
        with open(jf, "r") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    for node in data:
                        op = node.get("op_type")
                        if op == "UR_READ":
                            meta = node.get("metadata", {})
                            iq_id = meta.get("iq_id")
                            t_id = node.get("trace_id")
                            print(f"  Node {t_id}: UR_READ, iq_id={iq_id}")
            except:
                pass

if __name__ == "__main__":
    main()
