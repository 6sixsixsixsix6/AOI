import json
from pathlib import Path


config_path = Path(__file__).resolve().parent.parent / "configs/real_world.json"

with config_path.open("r", encoding="utf-8") as f:
    real_world = json.load(f)

print("=== Real World ===")
print(json.dumps(real_world, indent=2, ensure_ascii=False))
