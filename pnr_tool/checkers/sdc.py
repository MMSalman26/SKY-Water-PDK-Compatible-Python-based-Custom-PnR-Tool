"""Minimal OpenLane-style SDC subset for in-process STA."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def parse_sdc_text(text: str) -> Dict[str, Any]:
    """Parse a small SDC subset. Unknown commands are ignored."""
    clocks: List[Dict[str, Any]] = []
    input_delays: List[Dict[str, Any]] = []
    output_delays: List[Dict[str, Any]] = []
    false_paths: List[Dict[str, Any]] = []
    multicycle: List[Dict[str, Any]] = []
    uncertainty: Optional[float] = None

    # Strip comments
    lines: List[str] = []
    buf = ""
    for raw in text.splitlines():
        line = re.sub(r"#.*$", "", raw).strip()
        if not line:
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        line = buf + line
        buf = ""
        lines.append(line)

    for line in lines:
        if line.startswith("create_clock"):
            clocks.append(_parse_create_clock(line))
        elif line.startswith("set_input_delay"):
            input_delays.append(_parse_io_delay(line, "input"))
        elif line.startswith("set_output_delay"):
            output_delays.append(_parse_io_delay(line, "output"))
        elif line.startswith("set_clock_uncertainty"):
            uncertainty = _first_float(line)
        elif line.startswith("set_false_path"):
            false_paths.append(_parse_from_to(line))
        elif line.startswith("set_multicycle_path"):
            mc = _parse_from_to(line)
            mc["cycles"] = int(_flag_value(line, "-setup") or _nth_number(line, 0) or 1)
            hold_n = _flag_value(line, "-hold")
            mc["hold_cycles"] = int(hold_n) if hold_n is not None else 0
            if "-hold" in line and "-setup" not in line:
                mc["setup"] = False
                mc["hold"] = True
            else:
                mc["setup"] = True
                mc["hold"] = "-hold" in line
            multicycle.append(mc)
    return {
        "clocks": [c for c in clocks if c],
        "input_delays": input_delays,
        "output_delays": output_delays,
        "false_paths": false_paths,
        "multicycle": multicycle,
        "uncertainty_ns": uncertainty,
    }


def parse_sdc_file(path: Path) -> Dict[str, Any]:
    return parse_sdc_text(Path(path).read_text(encoding="utf-8", errors="replace"))


def write_default_sdc(
    path: Path,
    clock_period_ns: float,
    clock_port: str = "clk",
    uncertainty_ns: float = 0.05,
    input_ports: Optional[List[str]] = None,
    output_ports: Optional[List[str]] = None,
) -> Path:
    """Write a minimal OpenLane-style SDC for this checker / OpenSTA."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"create_clock -name {clock_port} -period {clock_period_ns:g} [get_ports {clock_port}]",
        f"set_clock_uncertainty {uncertainty_ns:g} [get_clocks {clock_port}]",
    ]
    ins = [p for p in (input_ports or []) if p != clock_port]
    outs = list(output_ports or [])
    if ins:
        joined = " ".join(ins)
        lines.append(
            f"set_input_delay -clock {clock_port} [expr {{0.2 * {clock_period_ns:g}}}] "
            f"[get_ports {{{joined}}}]"
        )
    if outs:
        joined = " ".join(outs)
        lines.append(
            f"set_output_delay -clock {clock_port} [expr {{0.2 * {clock_period_ns:g}}}] "
            f"[get_ports {{{joined}}}]"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_sdc(config: Dict[str, Any]) -> Dict[str, Any]:
    sta = config.get("sta", {}) or {}
    if sta.get("sdc"):
        return dict(sta["sdc"])
    path = sta.get("sdc_file")
    if path:
        p = Path(path)
        if p.exists():
            return parse_sdc_file(p)
    return {
        "clocks": [],
        "input_delays": [],
        "output_delays": [],
        "false_paths": [],
        "multicycle": [],
        "uncertainty_ns": None,
    }


def port_input_delay_ns(sdc: Dict[str, Any], port: str, default: float = 0.0) -> float:
    best = None
    for d in sdc.get("input_delays") or []:
        if _name_match(port, d.get("ports") or []):
            v = float(d.get("delay_ns", 0.0))
            best = v if best is None else max(best, v)
    return default if best is None else best


def port_output_delay_ns(sdc: Dict[str, Any], port: str, default: float = 0.0) -> float:
    best = None
    for d in sdc.get("output_delays") or []:
        if _name_match(port, d.get("ports") or []):
            v = float(d.get("delay_ns", 0.0))
            best = v if best is None else max(best, v)
    return default if best is None else best


def is_false_path(sdc: Dict[str, Any], launch: str, capture: str) -> bool:
    for fp in sdc.get("false_paths") or []:
        frm = fp.get("from") or []
        to = fp.get("to") or []
        if frm and not _name_match(launch, frm):
            continue
        if to and not _name_match(capture, to):
            continue
        if frm or to:
            return True
    return False


def multicycle_offset(
    sdc: Dict[str, Any], launch: str, capture: str, check: str, period_ns: float
) -> float:
    """Extra required-time credit: (N-1)*period for setup; hold usually 0."""
    extra = 0.0
    for mc in sdc.get("multicycle") or []:
        frm = mc.get("from") or []
        to = mc.get("to") or []
        if frm and not _name_match(launch, frm):
            continue
        if to and not _name_match(capture, to):
            continue
        if check == "setup" and mc.get("setup", True):
            n = max(1, int(mc.get("cycles", 1)))
            extra = max(extra, (n - 1) * period_ns)
        if check == "hold" and mc.get("hold"):
            n = max(0, int(mc.get("hold_cycles", 0)))
            extra = max(extra, n * period_ns)
    return extra


def _parse_create_clock(line: str) -> Dict[str, Any]:
    period = _flag_value(line, "-period")
    name = _flag_value(line, "-name")
    ports = _collect_get_ports(line)
    if name is None and ports:
        name = ports[0]
    return {
        "name": str(name or "clk"),
        "period_ns": float(period) if period is not None else None,
        "ports": ports,
    }


def _parse_io_delay(line: str, kind: str) -> Dict[str, Any]:
    delay = _nth_number(line, 0)
    return {
        "kind": kind,
        "delay_ns": float(delay) if delay is not None else 0.0,
        "clock": _flag_value(line, "-clock"),
        "ports": _collect_get_ports(line) or _collect_get_pins(line),
    }


def _parse_from_to(line: str) -> Dict[str, Any]:
    return {
        "from": _collect_after(line, "-from"),
        "to": _collect_after(line, "-to"),
        "through": _collect_after(line, "-through"),
    }


def _collect_get_ports(line: str) -> List[str]:
    return _collect_get(line, "get_ports")


def _collect_get_pins(line: str) -> List[str]:
    return _collect_get(line, "get_pins") + _collect_get(line, "get_cells")


def _collect_get(line: str, cmd: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(rf"{cmd}\s+\{{([^}}]+)\}}|{cmd}\s+(\S+)", line):
        raw = m.group(1) or m.group(2) or ""
        out.extend(_split_names(raw))
    return out


def _collect_after(line: str, flag: str) -> List[str]:
    idx = line.find(flag)
    if idx < 0:
        return []
    rest = line[idx + len(flag) :]
    # stop at next -flag
    nxt = re.search(r"\s-\w+", rest)
    chunk = rest[: nxt.start()] if nxt else rest
    names = _collect_get(chunk, "get_ports") + _collect_get(chunk, "get_pins")
    names += _collect_get(chunk, "get_cells") + _collect_get(chunk, "get_clocks")
    if not names:
        names = _split_names(chunk.strip().strip("{}[]"))
    return names


def _split_names(raw: str) -> List[str]:
    parts = re.split(r"[\s,]+", raw.strip().strip("{}[]\""))
    return [p.strip() for p in parts if p.strip() and not p.startswith("-")]


def _name_match(name: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    n = str(name)
    for pat in patterns:
        p = str(pat).replace("get_ports", "").replace("get_pins", "").strip()
        if not p:
            continue
        if p in ("*", "{*}"):
            return True
        rx = "^" + re.escape(p).replace(r"\*", ".*") + "$"
        if re.match(rx, n) or p == n or n.endswith("/" + p) or n.startswith(p):
            return True
        # instance vs instance/pin
        if "/" in n and n.split("/")[0] == p:
            return True
        if "/" in p and n == p.split("/")[0]:
            return True
    return False


def _flag_value(line: str, flag: str) -> Optional[str]:
    m = re.search(re.escape(flag) + r"\s+(\S+)", line)
    if not m:
        return None
    return m.group(1).strip("{}[]\"'")


def _first_float(line: str) -> Optional[float]:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", line)
    return float(m.group(0)) if m else None


def _nth_number(line: str, n: int) -> Optional[float]:
    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", line)]
    return nums[n] if n < len(nums) else None
