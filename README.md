# SkyWater-compatible Python PnR tool

Pure-Python, in-memory **place-and-route (PnR)** tool for **SkyWater 130 nm** (`sky130_fd_sc_hd`). You can benchmark your own placement, CTS, and routing algorithms against a fixed data contract, or run the built-in OpenROAD-inspired engines on a gate-level netlist.

**Repo:** [MMSalman26/SKY-Water-PDK-Compatible-Python-based-Custom-PnR-Tool](https://github.com/MMSalman26/SKY-Water-PDK-Compatible-Python-based-Custom-PnR-Tool)

---

## Table of contents

1. [What you get](#what-you-get)
2. [Requirements](#requirements)
3. [Step-by-step: install](#step-by-step-install)
4. [Step-by-step: fetch the PDK](#step-by-step-fetch-the-pdk)
5. [Step-by-step: run the bundled designs](#step-by-step-run-the-bundled-designs)
6. [Step-by-step: run your own netlist](#step-by-step-run-your-own-netlist)
7. [Step-by-step: plug in your own algorithms](#step-by-step-plug-in-your-own-algorithms)
8. [Step-by-step: batch experiments](#step-by-step-batch-experiments)
9. [Outputs](#outputs)
10. [Config knobs](#config-knobs)
11. [CLI reference](#cli-reference)
12. [Tests](#tests)
13. [Architecture](#architecture)
14. [References](#references)
15. [Limitations](#limitations)
16. [License](#license)

---

## What you get

| Stage | Built-in engine |
|---|---|
| Floorplan / IO | Die estimate + port ring |
| Power | Core ring + straps + met1 follow-pin (`pdngen`) |
| Placement | Density + wirelength global place, Abacus-style legalize (`RePlAce` / `OpenDP`) |
| Tap / decap | SkyWater HD tap and decap cells only (no fillers) |
| CTS | Clustered buffered tree (`TritonCTS`) |
| Routing | Pattern L/Z then maze repair (`FastRoute`); pin stubs from LEF |
| DRC | Width-aware shorts/spacing, vias, OBS, min-width, grid (Magic/KLayout *ideas*) |
| STA | Dual-corner NLDM, tree Elmore, CRPR, SDC subset (`OpenSTA` *ideas*) |
| IR | Static DC MNA on the planned PDN (`OpenROAD psm` / `PDNSim` *ideas*) |

Bundled designs:

- **ALU** — OpenLane gate-level 32-bit ALU (`designs/alu/ALU.v`), ~10 s runtime, 10 ns clock.
- **PicoRV32a** — OpenLane-synthesized PicoRV32 (`designs/picorv32a/`), ~15k logic cells; allow ~15–40 min (DRC dominates).

PDK LEF/liberty files are **downloaded on first use**, not stored in git.

---

## Requirements

- **Python 3.10+** (3.11/3.12 recommended)
- Windows, Linux, or macOS
- Network once, to fetch SkyWater cell LEF/liberty JSON
- ~2 GB disk for the PDK cache after `fetch-pdk`

Optional (only for the compare harnesses, not required to run PnR):

- [OpenSTA](https://github.com/The-OpenROAD-Project/OpenSTA)
- [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) (`analyze_power_grid`)
- [ngspice](https://github.com/imr/ngspice)

---

## Step-by-step: install

### 1. Clone

```bash
git clone https://github.com/MMSalman26/SKY-Water-PDK-Compatible-Python-based-Custom-PnR-Tool.git
cd SKY-Water-PDK-Compatible-Python-based-Custom-PnR-Tool
```

### 2. Virtual environment

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
pip install -e .
```

If scripts are blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt
pip install -e .
```

`rtree` needs a libspatialindex wheel. If install fails, the tool falls back to SciPy / grid hashing; DRC will be slower.

### 3. Smoke test

```bash
python -m pytest tests -q
```

Golden fixtures do not need a PDK fetch. Skip is OK for tests that need `medium_design.v`.

---

## Step-by-step: fetch the PDK

Cell LEFs and ff/ss/tt liberty JSON come from [google/skywater-pdk-libs-sky130_fd_sc_hd](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd). They land in `pnr_tool/data/` (gitignored).

**Curated set (enough for ALU + golden tests)**

```bash
python -m pnr_tool fetch-pdk
```

**Also fetch every `sky130_fd_sc_hd__*` cell used by a netlist** (required for Pico and for your own designs):

```bash
python -m pnr_tool fetch-pdk --netlist designs/picorv32a/picorv32a.synthesis.v
python -m pnr_tool fetch-pdk --netlist path/to/your.v
```

`--netlist` can be repeated. `--force` re-downloads. Missing optional files (404) are skipped.

`run` calls fetch automatically unless you pass `--no-fetch`.

---

## Step-by-step: run the bundled designs

### ALU (~1 minute)

```bash
python -m pnr_tool run --netlist designs/alu/ALU.v --top ALU --clock-period-ns 10 --out runs/alu
python -m pnr_tool html-report
```

Windows: use `designs\alu\ALU.v` and `runs\alu` if you prefer backslashes.

### PicoRV32a (tens of minutes)

If the netlist is missing:

```bash
python scripts/fetch_picorv32.py
```

Then:

```bash
python -m pnr_tool fetch-pdk --netlist designs/picorv32a/picorv32a.synthesis.v
python -m pnr_tool run --netlist designs/picorv32a/picorv32a.synthesis.v --top picorv32a --config designs/picorv32a/config.yaml --clock-period-ns 24 --out runs/picorv32a
python -m pnr_tool html-report
```

Pico uses a capped router `step_budget` in `designs/picorv32a/config.yaml`. DRC on this size can take ~20 minutes.

### View results

```bash
python -m http.server 8000 --directory runs
```

Open http://localhost:8000/index.html (needed so the layout viewer can load `layout_view.json`).

Per-run files: `runs/<name>/*.qor.json`, layout PNGs, `layout_view.json`, plus `*.cif`, `*.spef`, `*_ir.sp` when those dumps are enabled.

---

## Step-by-step: run your own netlist

The front-end wants **structural gate-level Verilog** mapped to `sky130_fd_sc_hd__*` cells (Yosys / OpenLane / similar). RTL-only files will not place.

### 1. Synthesize (example: OpenLane or Yosys)

Produce a netlist whose instances look like:

```verilog
sky130_fd_sc_hd__inv_2 u0 (.A(a), .Y(y), .VGND(VGND), .VPWR(VPWR));
```

Power pins (`VGND`, `VPWR`, `VNB`, `VPB`) and fill/tap/decap cells are stripped automatically. You can also start from a flattened Yosys netlist without power pins.

### 2. Fetch cells used by that file

```bash
python -m pnr_tool fetch-pdk --netlist designs/my_chip/my_chip.v
```

### 3. Optional design config

Copy `designs/picorv32a/config.yaml` and tighten routing if the netlist is large:

```yaml
routing:
  step_budget: 8000
  overflow_passes: 2
placement:
  die_utilization: 0.55
sta:
  uncertainty_ns: 0.1
```

Full defaults: [`pnr_tool/config/defaults.yaml`](pnr_tool/config/defaults.yaml). Your YAML **overrides** those keys; you do not need to copy the whole file.

### 4. Run

```bash
python -m pnr_tool run \
  --netlist designs/my_chip/my_chip.v \
  --top my_chip \
  --config designs/my_chip/config.yaml \
  --clock-period-ns 10 \
  --out runs/my_chip
```

`--top` is the Verilog module name. `--clock-period-ns` is the STA/IR clock (nanoseconds).

### 5. Read QoR

- JSON: `runs/my_chip/<top>.qor.json`
- HTML: `python -m pnr_tool html-report` then `runs/index.html`
- STA: setup/hold WNS (ps), failing endpoints
- IR: max drop, current density `J`, instance heatmap (static DC only)
- DRC: counts by type; `pass_on` in config decides which types count as the headline violation number (default: overlap, short, open, spacing)

---

## Step-by-step: plug in your own algorithms

Stages are swappable. Subclass the ABCs in [`pnr_tool/algorithms/base.py`](pnr_tool/algorithms/base.py):

| Class | Must return |
|---|---|
| `PlacementAlgorithm` | `name → {x, y, orientation, is_fixed}` |
| `ClockOptAlgorithm` | `{new_buffers, clock_nets}` |
| `RoutingAlgorithm` | `net → [{layer, x1, y1, x2, y2}, ...]` |

Example plugin: [`tests/plugins/dummy_placement.py`](tests/plugins/dummy_placement.py).

```bash
python -m pnr_tool run --netlist tests/fixtures/golden_three_cell.v \
  --placement tests.plugins.dummy_placement:DummyPlacement \
  --out runs/dummy_place
```

`--placement`, `--clock-opt`, and `--routing` accept:

- built-in aliases: `default`, `force_directed`, `random`, `htree`, `global`
- `module.path:ClassName` on `PYTHONPATH`

Checkers always read the same `DesignObject` fields, so you can replace one engine and keep DRC/STA/IR.

---

## Step-by-step: batch experiments

[`experiments.yaml`](experiments.yaml) is a design × algorithm matrix.

```bash
python -m pnr_tool batch --manifest experiments.yaml --out runs/batch_demo
python -m pnr_tool scoreboard --runs runs/batch_demo
```

Manifest shape:

```yaml
seed: 42
designs:
  - netlist: tests/fixtures/golden_seq.v
    top: null
    clock_period_ns: 10
  - netlist: designs/alu/ALU.v
    top: ALU
    clock_period_ns: 10
algorithms:
  - name: baseline
    placement: default
    clock_opt: default
    routing: default
  - name: my_placer
    placement: mypkg.placer:MyPlacer
    clock_opt: default
    routing: default
```

---

## Outputs

| Artifact | Where | What |
|---|---|---|
| QoR JSON | `runs/<out>/<name>.qor.json` | Metrics + DRC/STA/IR |
| HTML | `runs/index.html` | Compare + layout viewer + checker panels |
| Layout PNG | `runs/<out>/layout_*.png` | Power / place / CTS / route |
| Layout JSON | `runs/<out>/layout_view.json` | Interactive viewer |
| CIF | `runs/<out>/<name>.cif` | Geometry dump (DRC debug) |
| SPEF | `runs/<out>/<name>.spef` | Reduced parasitics (OpenSTA compare) |
| SPICE | `runs/<out>/<name>_ir.sp` | PDN R + sources (ngspice / PDNSim compare) |
| Checkpoints | `runs/<out>/checkpoints/` | Resume with `--resume-from` |

Optional compare scripts (skip if the binary is not on `PATH`):

```bash
python scripts/compare_opensta.py --run-dir runs/alu --clock-period-ns 10
python scripts/compare_drc.py --run-dir runs/alu
python scripts/compare_ir.py --run-dir runs/alu --clock-period-ns 10
```

---

## Config knobs

Override via `--config my.yaml`. Useful keys (see `pnr_tool/config/defaults.yaml`):

| Key | Meaning |
|---|---|
| `thresholds.sta_wns_ps` | Setup WNS limit for notes (ps) |
| `thresholds.ir_min_vdd_ratio` | Instance IR fail if V &lt; ratio × VDD (default 0.95) |
| `sta.wire_model` | `tree_elmore` \| `lumped_elmore` \| `d2m` |
| `sta.use_crpr` | Common-path pessimism removal |
| `drc.pass_on` | Types that count in the headline DRC number |
| `drc.write_cif` | Write CIF after DRC |
| `ir_drop.source_type` | `ring` \| `straps` (default) \| `bumps` |
| `ir_drop.corner` | `tt` 1.8 V / `ff` 1.95 V / `ss` 1.60 V |
| `ir_drop.coupled` | Optional coupled VDD+VSS MNA |
| `ir_drop.write_spice` | Write `*_ir.sp` |
| `placement.die_utilization` | Floorplan density |
| `routing.step_budget` | Maze-search cap (lower = faster, more fallbacks) |
| `report.layout_images` | Stage PNGs |

---

## CLI reference

```text
python -m pnr_tool fetch-pdk [--force] [--cache DIR] [--netlist PATH ...]

python -m pnr_tool run --netlist PATH
    [--top NAME] [--config PATH] [--out DIR]
    [--clock-period-ns FLOAT]
    [--resume-from CHECKPOINT.pkl]
    [--no-fetch] [--no-layout-images]
    [--placement SPEC] [--clock-opt SPEC] [--routing SPEC]

python -m pnr_tool batch --manifest experiments.yaml --out DIR
python -m pnr_tool scoreboard --runs DIR [--out scoreboard.csv]
python -m pnr_tool html-report [--runs DIR] [--out PATH] [--title STR]
```

After `pip install -e .` you can also use `pnr-tool` instead of `python -m pnr_tool`.

---

## Tests

```bash
python -m pytest tests -q
```

Needs the venv packages. PDK fetch is not required for golden tests. Pico elaboration test skips if the netlist is absent.

---

## Architecture

```text
netlist.v  →  elaborate  →  DesignObject
                              │
         floorplan / pdngen / place / tap / decap / CTS / route
                              │
                    DRC  +  STA  +  IR  →  QoR JSON + HTML
```

| Path | Role |
|---|---|
| `pnr_tool/pipeline/run.py` | Stage order |
| `pnr_tool/design/object.py` | In-memory design |
| `pnr_tool/design/contracts.py` | Stage I/O checks |
| `pnr_tool/algorithms/` | Place / CTS / route / power / tap |
| `pnr_tool/checkers/` | DRC, STA, IR |
| `pnr_tool/pdk/fetch.py` | Download LEF/liberty |
| `pnr_tool/config/defaults.yaml` | Defaults |
| `pnr_tool/report/` | QoR, HTML, CIF, SPEF, layout |

---

## References

Algorithms here are **reimplemented in Python** from public papers and OSS tools. We do not vendor those codebases.

### PDK, flow, and cell libraries

| Project | URL | Used for |
|---|---|---|
| SkyWater SKY130 PDK | https://github.com/google/skywater-pdk | Process |
| SkyWater HD standard cells | https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd | LEF + liberty JSON (ff/ss/tt) |
| OpenLane | https://github.com/The-OpenROAD-Project/OpenLane | Flow order, TCL knobs, sky130A layers |
| OpenLane 2 | https://github.com/efabless/openlane2 | Later OpenLane flow (reference only) |
| open_pdks | https://github.com/fossi-foundation/open-pdks | Tech install layout / config paths |
| Magic | https://github.com/RTimothyEdwards/magic | DRC *ideas*; not called |
| KLayout | https://github.com/KLayout/klayout | DRC *ideas*; not called |
| Yosys | https://github.com/YosysHQ/yosys | Typical synthesizer for input netlists |

### OpenROAD engines (algorithm references)

| Project | URL | Used for |
|---|---|---|
| OpenROAD | https://github.com/The-OpenROAD-Project/OpenROAD | Overall PnR / `pdngen` / `psm` |
| RePlAce | https://github.com/The-OpenROAD-Project/RePlAce | Global placement (density + WL) |
| OpenDP | https://github.com/The-OpenROAD-Project/OpenDP | Detailed placement / Abacus row pack |
| TritonCTS | https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/cts | Clustered buffered CTS |
| FastRoute | https://github.com/The-OpenROAD-Project/FastRoute | Global route, L/Z pattern, overflow |
| OpenROAD FastRoute (grt) | https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/grt | Pin stubs, layer directions |
| OpenROAD pdn | https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/pdn | Rings, straps, follow-pin |
| OpenROAD psm (PDNSim) | https://github.com/The-OpenROAD-Project/OpenROAD/tree/master/src/psm | Static IR MNA, sources, EM/J |
| PDNSim (standalone) | https://github.com/The-OpenROAD-Project/PDNSim | Archived IR solver |
| OpenSTA | https://github.com/The-OpenROAD-Project/OpenSTA | NLDM, Elmore, CRPR, SDC |
| OpenROAD-flow-scripts | https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts | Reference sky130 designs |

### Research / other OSS checkers

| Work | URL / note | Used for |
|---|---|---|
| iEDA | https://github.com/OSCC-Project/iEDA | Feature checklist (iPA / iTO / iPL) |
| Liberty / NLDM | Accellera Liberty | Cell delay tables |
| Elmore delay | Elmore, 1948; later interconnect textbooks | Wire delay |
| D2M delay | Alpert et al., two-pole metric | Optional `sta.wire_model: d2m` |
| Abacus legalizer | Cho / Spindler et al. (row-based DP) | Detailed placement |
| Locality IR (research) | e.g. Friedman / Rochester closed-form IR | Speed ideas only; not used at ALU/Pico size |
| Cadence Voltus / PrimeRail | commercial | Accuracy *bar* for IR; **not** cloned |

### Bundled example designs

| Design | URL | Notes |
|---|---|---|
| 32-bit ALU netlist | https://github.com/HafizMutahirAhmed/ASIC-ALU-OpenLane | `designs/alu/ALU.v` |
| PicoRV32 RTL | https://github.com/YosysHQ/picorv32 | ISC; RISC-V core |
| PicoRV32a GL netlist | https://github.com/ABHIMR1502/Digital-SoC-Design | `DAY1/picorv32a.synthesis.v` |

### Python libraries

| Package | URL |
|---|---|
| NumPy | https://github.com/numpy/numpy |
| SciPy | https://github.com/scipy/scipy |
| NetworkX | https://github.com/networkx/networkx |
| Matplotlib | https://github.com/matplotlib/matplotlib |
| Rtree | https://github.com/Toblerity/rtree |
| PyYAML | https://github.com/yaml/pyyaml |

---

## Limitations

- Global routing, not track-accurate detailed routing. Shorts/spacing counts are expected on dense designs; they are a congestion signal, not a GDS clean.
- Static IR only (no VCD / vector / package RLC / thermal co-sim).
- STA is dual-corner NLDM + Elmore, not CCS / SI / extracted SPEF signoff.
- Pico DRC can take tens of minutes (width-aware geometry + OBS).
- Headline DRC uses `pass_on` types; enclosure / min-width / offgrid are reported but may not increment that count.

---

## License

This repository is **Apache License 2.0** ([LICENSE](LICENSE)). Attribution for third-party PDK, OpenLane, and example netlists is in [NOTICE](NOTICE). Those projects keep their own licenses.

SkyWater, OpenROAD, OpenSTA, Magic, KLayout, Yosys, and Voltus are trademarks of their respective owners.
