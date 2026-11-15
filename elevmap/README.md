# elevmap: floor-aligned elevation map from a Gaussian splat

```bash
uv sync                                   # or: pip install -r elevmap/requirements.txt
uv run python -m elevmap --splat trained_splats/splat_30000.ply --out maps/auditorium --min-opacity 0.15
# remote, nothing is downloaded to disk (HTTP Range streaming):
python -m elevmap --splat github:Ishan-mohite/deepsplat@main/trained_splats/splat_30000.ply --out maps/x   # GITHUB_TOKEN if private
python -m elevmap --splat gs://my-bucket/maps/unit.ply --out maps/x                                        # GCS_ACCESS_TOKEN if private
python -m elevmap --splat https://host/path/splat.ply --out maps/x                                         # any Range-capable URL / signed URL
```

Result for the project splat: `maps/auditorium/` (open `elevation_overview.png`, `floor_alignment.png`, `robot_map.png`).
Details and limitations: `docs/ANALYSIS.md` section 4. `map.yaml` + `map.pgm` load in ROS `map_server` / nav2 as they are.
