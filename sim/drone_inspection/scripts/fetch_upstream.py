"""Download the small MIT-licensed controller/asset subset at an immutable revision."""
from pathlib import Path
from urllib.request import urlopen
import hashlib,json
ROOT = Path(__file__).resolve().parents[1]
REVISION = "410c2395947fb850cfcdf7a2b7f17be0eac44a7e"
BASE = "https://raw.githubusercontent.com/learnsyslab/gym-pybullet-drones/" + REVISION + "/"
FILES = ["LICENSE", "gym_pybullet_drones/control/BaseControl.py", "gym_pybullet_drones/control/DSLPIDControl.py", "gym_pybullet_drones/utils/enums.py", "gym_pybullet_drones/assets/cf2x.urdf", "gym_pybullet_drones/assets/cf2.dae"]
def main():
    checksums = {}
    for name in FILES:
        data = urlopen(BASE + name, timeout=60).read()
        destination = ROOT / "vendor" / name
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data)
        checksums[name] = hashlib.sha256(data).hexdigest()
    for package in ["gym_pybullet_drones", "gym_pybullet_drones/control", "gym_pybullet_drones/utils"]:
        (ROOT / "vendor" / package / "__init__.py").touch()
    (ROOT / "vendor" / "upstream.json").write_text(json.dumps({"repository":"https://github.com/learnsyslab/gym-pybullet-drones", "revision": REVISION,"license":"MIT","sha256":checksums},indent=2)+"\n")
    print("Fetched pinned controller and original URDF/mesh.")
if __name__ == "__main__": main()
