"""Regenerates docs/branch_comparison.png and docs/vps_options.png (qualitative scores, see docs/ANALYSIS.md)."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap

OUT = Path(__file__).resolve().parents[1]

# --- 1. what each branch contains (0 = nothing, 1 = plan / prototype only, 2 = working code, 3 = working + tested on the real splat)
cols = ["Splat map\n(3DGS)", "Visual\npositioning", "Damage\ndetection", "3D lifting /\nissue log", "Viewer /\nUI", "Capture\nclient", "Cloud\ndeploy", "Elevation /\nrobot map", "Brand /\nUX refs"]
rows = {
    "main":                 [3, 0, 0, 0, 0, 0, 0, 0, 0],
    "backend":              [3, 2, 2, 3, 0, 1, 2, 0, 0],
    "mobile-recorder-app":  [0, 1, 0, 0, 0, 1, 0, 0, 0],
    "web-viewer":           [2, 0, 0, 1, 2, 0, 0, 0, 0],
    "ux-design-layouts":    [0, 0, 0, 0, 1, 0, 0, 0, 2],
    "elevation-map (new)":  [3, 0, 0, 0, 0, 0, 0, 3, 0],
}
M = np.array(list(rows.values()))
cmap = ListedColormap(["#f1f5f9", "#fde68a", "#86efac", "#16a34a"])
fig, ax = plt.subplots(figsize=(13, 5.2))
ax.imshow(M, cmap=cmap, vmin=0, vmax=3, aspect="auto")
ax.set_xticks(range(len(cols)), cols, fontsize=9); ax.set_yticks(range(len(rows)), list(rows), fontsize=11)
ax.xaxis.tick_top()
txt = {0: "", 1: "plan", 2: "code", 3: "code + real splat"}
for i in range(M.shape[0]):
    for j in range(M.shape[1]):
        if M[i, j]:
            ax.text(j, i, txt[M[i, j]], ha="center", va="center", fontsize=8.5, color="white" if M[i, j] == 3 else ("#14532d" if M[i, j] == 2 else "#78350f"))
ax.set_xticks(np.arange(-.5, len(cols)), minor=True); ax.set_yticks(np.arange(-.5, len(rows)), minor=True)
ax.grid(which="minor", color="white", lw=2); ax.tick_params(which="both", length=0)
ax.set_title("DeepSplat branches: what each one contains", fontsize=14, weight="bold", pad=44)
fig.tight_layout(); fig.savefig(OUT / "branch_comparison.png", dpi=140); plt.close(fig)

# --- 2. VPS options for phone / drone (qualitative 1-5, higher = better; judgement, not benchmarks)
opts = ["A  SIFT + PnP on CPU\n(backend branch today)", "B  hloc on GPU\n(NetVLAD + SuperPoint + LightGlue)", "C  Hosted VPS\n(MultiSet-style)", "D  Hybrid (recommended)\nGPU hloc fixes + on-device VIO\n+ splat render-and-compare"]
crit = ["Accuracy\n(cm / deg)", "Latency\nper update", "Robustness\n(light, blank walls)", "Works offline /\non drone", "Cost & control"]
S = np.array([[3, 3, 2, 2, 5],
              [4, 3, 4, 3, 4],
              [4, 3, 4, 1, 2],
              [5, 5, 5, 4, 4]])
fig, ax = plt.subplots(figsize=(12, 4.8))
im = ax.imshow(S, cmap="YlGn", vmin=1, vmax=5, aspect="auto")
ax.set_xticks(range(len(crit)), crit, fontsize=10); ax.set_yticks(range(len(opts)), opts, fontsize=10)
ax.xaxis.tick_top()
for i in range(S.shape[0]):
    for j in range(S.shape[1]):
        ax.text(j, i, str(S[i, j]), ha="center", va="center", fontsize=13, weight="bold")
ax.set_title("Visual positioning options for iPhone / drone (1-5, qualitative)", fontsize=13, weight="bold", pad=40)
fig.tight_layout(); fig.savefig(OUT / "vps_options.png", dpi=140); plt.close(fig)
print("ok")
