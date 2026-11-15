# Vendored viewer dependencies

Local copies so the web viewer runs without internet (e.g. on a hotspot or router with no uplink).
`viewer/index.html` loads them through its import map; nothing is fetched from a CDN.

| Path | Package | Version | License |
|---|---|---|---|
| `three/build/three.module.js`, `three/build/three.core.js` | [three](https://www.npmjs.com/package/three) | 0.187.0 | MIT (`three/LICENSE`) |
| `three/examples/jsm/controls/OrbitControls.js` | three (addons) | 0.187.0 | MIT |
| `three/examples/jsm/postprocessing/Pass.js` | three (addons, imported by Spark) | 0.187.0 | MIT |
| `spark/spark.module.js` | [@sparkjsdev/spark](https://www.npmjs.com/package/@sparkjsdev/spark) | 2.4.0 | MIT (`spark/LICENSE`) |
| `fonts/*.woff2` | Fontsource: ibm-plex-mono 5.3.0, ibm-plex-sans 5.3.0, space-grotesk 5.3.0 | see left | SIL OFL 1.1 (`fonts/LICENSE-*.txt`) |

Spark embeds its WebAssembly and worker code, so it needs no further files. To update a version,
replace the files from `https://cdn.jsdelivr.net/npm/<package>@<version>/...` and adjust this table.
