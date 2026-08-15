"""Lightweight structural Verilog parser + hierarchy elaborator (no Icarus)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pnr_tool.design.object import DesignObject

# Physical-only / filler cells often present in OpenLane final netlists
_PHYSICAL_CELL_RE = re.compile(
    r"(fill_\d+|decap_\d+|tapvpwrvgnd_|sky130_ef_sc_hd__)",
    re.IGNORECASE,
)
_POWER_PIN_NAMES = frozenset({"VGND", "VPWR", "VNB", "VPB", "VDD", "VSS", "VPWRA", "VGNDA"})


@dataclass
class Module:
    name: str
    ports: List[Tuple[str, str]] = field(default_factory=list)  # (name, direction)
    wires: List[str] = field(default_factory=list)
    instances: List[dict] = field(default_factory=list)  # {cell, name, connections}
    assigns: List[Tuple[str, str]] = field(default_factory=list)  # (lhs, rhs) net aliases


_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
_MODULE_RE = re.compile(r"\bmodule\s+(\w+)\s*(?:\#\s*\([^;]*\))?\s*\((.*?)\);", re.DOTALL)
_ENDMODULE_RE = re.compile(r"\bendmodule\b")
_PORT_DECL_RE = re.compile(
    r"\b(input|output|inout)\b\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*([^;]+);",
)
_WIRE_RE = re.compile(
    r"\bwire\b\s*(?:\[\s*(\d+)\s*:\s*(\d+)\s*\])?\s*([^;]+);",
)
_ASSIGN_RE = re.compile(
    r"\bassign\s+(\\[^\\]+?\s|[A-Za-z_\\][\w\\.\[\]:]*)\s*=\s*([^;]+);",
)
# Instance names may be plain ids or Verilog escaped ids (\\name[0]<space>).
_INST_RE = re.compile(
    r"([A-Za-z_]\w*)\s+(?:\#\s*\([^;]*?\)\s*)?"
    r"(\\[^\n]*?\s|[A-Za-z_][\w]*)\s*\((.*?)\);",
    re.DOTALL,
)
_CONN_NAMED_RE = re.compile(r"\.(\w+)\s*\(\s*([^)]*?)\s*\)")


def normalize_ident(name: str) -> str:
    """Normalize Verilog escaped identifiers: \\a_reg[0]  -> a_reg[0]."""
    name = name.strip()
    if name.startswith("\\"):
        name = name[1:].rstrip()
    return name


def _strip_comments(text: str) -> str:
    return _COMMENT_RE.sub("", text)


def _expand_bus(name: str, msb: Optional[str], lsb: Optional[str]) -> List[str]:
    name = normalize_ident(name)
    if msb is None or lsb is None:
        return [name]
    hi, lo = int(msb), int(lsb)
    step = -1 if hi >= lo else 1
    return [f"{name}[{i}]" for i in range(hi, lo + step, step)]


def _split_idents(blob: str, msb: Optional[str] = None, lsb: Optional[str] = None) -> List[str]:
    parts: List[str] = []
    # Escaped idents may contain spaces before terminator
    tokens = re.findall(r"\\[^\\]+?(?=\s*,|\s*$)|[^,\\]+", blob)
    for tok in tokens:
        tok = tok.strip().strip(",")
        if not tok:
            continue
        tok = re.sub(r"\b(input|output|inout|wire|reg)\b", "", tok).strip()
        tok = re.sub(r"\[\s*\d+\s*:\s*\d+\s*\]", "", tok).strip()
        if not tok:
            continue
        # last word or escaped ident
        if tok.startswith("\\"):
            base = normalize_ident(tok)
        else:
            base = tok.split()[-1] if tok.split() else tok
            base = normalize_ident(base)
        parts.extend(_expand_bus(base, msb, lsb))
    return [p for p in parts if p]


def parse_verilog(text: str) -> Dict[str, Module]:
    text = _strip_comments(text)
    modules: Dict[str, Module] = {}

    positions = [(m.start(), m.group(1), m.group(2), m.end()) for m in _MODULE_RE.finditer(text)]
    for _i, (_start, name, portlist, body_start) in enumerate(positions):
        end_m = _ENDMODULE_RE.search(text, body_start)
        if not end_m:
            raise ValueError(f"Module '{name}' missing endmodule")
        body = text[body_start : end_m.start()]
        mod = Module(name=name)

        header_ports = _split_idents(portlist)
        directions: Dict[str, str] = {}
        bus_bits: Dict[str, List[str]] = {}

        for dm in _PORT_DECL_RE.finditer(body):
            direction = dm.group(1)
            msb, lsb, names = dm.group(2), dm.group(3), dm.group(4)
            idents = _split_idents(names, msb, lsb)
            # Track bus root -> bits for header port expansion
            roots = _split_idents(names, None, None)
            for root in roots:
                bits = _expand_bus(root, msb, lsb)
                if len(bits) > 1:
                    bus_bits[root] = bits
            for p in idents:
                directions[p] = direction

        for tok in portlist.split(","):
            tok = tok.strip()
            m = re.match(
                r"(input|output|inout)\b(?:\s+wire|\s+reg)?(?:\s*\[[^\]]+\])?\s+(\w+)",
                tok,
            )
            if m:
                directions[m.group(2)] = m.group(1)
                if m.group(2) not in header_ports:
                    header_ports.append(m.group(2))

        # Expand header bus ports into bit ports when body declared a bus
        expanded_ports: List[Tuple[str, str]] = []
        for p in header_ports:
            if p in bus_bits:
                for bit in bus_bits[p]:
                    expanded_ports.append((bit, directions.get(bit, directions.get(p, "inout"))))
            else:
                # Prefer bit-level directions already collected
                if any(k.startswith(p + "[") for k in directions):
                    for bit, d in directions.items():
                        if bit.startswith(p + "["):
                            expanded_ports.append((bit, d))
                else:
                    expanded_ports.append((p, directions.get(p, "inout")))
        mod.ports = expanded_ports

        for wm in _WIRE_RE.finditer(body):
            msb, lsb, names = wm.group(1), wm.group(2), wm.group(3)
            mod.wires.extend(_split_idents(names, msb, lsb))

        for am in _ASSIGN_RE.finditer(body):
            lhs = normalize_ident(am.group(1))
            rhs = normalize_ident(am.group(2).strip())
            # Only keep simple 1:1 net aliases (skip concatenations / constants extras).
            if lhs and rhs and "{" not in rhs and "}" not in rhs:
                mod.assigns.append((lhs, rhs))

        body_for_inst = _PORT_DECL_RE.sub("", body)
        body_for_inst = _WIRE_RE.sub("", body_for_inst)
        body_for_inst = re.sub(r"\b(assign|parameter|localparam)\b[^;]*;", "", body_for_inst)

        for im in _INST_RE.finditer(body_for_inst):
            cell, inst_name, conns = im.group(1), im.group(2), im.group(3)
            if cell in ("module", "endmodule", "input", "output", "wire", "assign"):
                continue
            if not (cell.startswith("sky130_") or cell[0].isalpha()):
                continue
            connections: Dict[str, str] = {}
            named = list(_CONN_NAMED_RE.finditer(conns))
            if named:
                for cm in named:
                    net = normalize_ident(cm.group(2))
                    if net == "":
                        continue
                    connections[cm.group(1)] = net
            else:
                pos = [normalize_ident(c) for c in conns.split(",") if c.strip()]
                for idx, net in enumerate(pos):
                    connections[f"_{idx}"] = net
            mod.instances.append(
                {"cell": cell, "name": normalize_ident(inst_name), "connections": connections}
            )

        modules[name] = mod
    return modules


def parse_verilog_file(path: Path) -> Dict[str, Module]:
    text = Path(path).read_text(encoding="utf-8")
    return parse_verilog(text)


def is_physical_cell(cell_type: str) -> bool:
    return bool(_PHYSICAL_CELL_RE.search(cell_type))


def is_power_pin(pin: str) -> bool:
    return pin.upper() in _POWER_PIN_NAMES


def elaborate(
    modules: Dict[str, Module],
    top: Optional[str] = None,
    library_cell_names: Optional[set] = None,
    strip_physical: bool = True,
    strip_power_pins: bool = True,
) -> DesignObject:
    """Flatten hierarchy into a DesignObject."""
    if not modules:
        raise ValueError("No modules parsed")
    if top is None:
        instantiated = set()
        for mod in modules.values():
            for inst in mod.instances:
                if inst["cell"] in modules:
                    instantiated.add(inst["cell"])
        tops = [n for n in modules if n not in instantiated]
        top = tops[0] if tops else next(iter(modules))

    design = DesignObject(name=top)
    lib_names = library_cell_names or set()

    def is_leaf(cell: str) -> bool:
        return cell not in modules or cell in lib_names

    def walk(mod_name: str, prefix: str, port_map: Dict[str, str]) -> None:
        mod = modules[mod_name]

        # Build assign-alias map collapsing both names onto one canonical net.
        # Prefer primary-port names so pad connectivity is preserved for
        # ``assign out = internal`` style Yosys aliases.
        port_names = {normalize_ident(p) for p, _d in mod.ports}
        alias_local: Dict[str, str] = {}
        for lhs, rhs in mod.assigns:
            a = normalize_ident(lhs)
            b = normalize_ident(rhs)
            if a in ("1'b0", "1'b1", "1'bx", "1'bz", "0", "1") or b in (
                "1'b0",
                "1'b1",
                "1'bx",
                "1'bz",
                "0",
                "1",
            ):
                continue
            if a in port_names and b not in port_names:
                alias_local[b] = a
            elif b in port_names and a not in port_names:
                alias_local[a] = b
            else:
                alias_local[a] = b

        def _follow_alias(net: str) -> str:
            seen: Set[str] = set()
            while net in alias_local and net not in seen:
                seen.add(net)
                net = alias_local[net]
            return net

        def resolve(net: str) -> str:
            net = normalize_ident(net)
            if net in ("1'b0", "1'b1", "1'bx", "1'bz", "0", "1"):
                return net
            net = _follow_alias(net)
            if net in port_map:
                return port_map[net]
            return f"{prefix}{net}" if prefix else net

        if not prefix:
            for pname, direction in mod.ports:
                design.ports[pname] = {"direction": direction}
                design.nets.setdefault(pname, {"pins": [], "drivers": [], "driver": None})

        for inst in mod.instances:
            cell = inst["cell"]
            if strip_physical and is_physical_cell(cell):
                continue
            hier_name = f"{prefix}{inst['name']}"
            if is_leaf(cell):
                design.cells[hier_name] = {
                    "cell_type": cell,
                    "pins": {},
                }
                for pin, net in inst["connections"].items():
                    if strip_power_pins and is_power_pin(pin):
                        continue
                    resolved = resolve(net)
                    # Skip pure power nets as connectivity for logic graph
                    if strip_power_pins and resolved.upper() in _POWER_PIN_NAMES:
                        continue
                    design.cells[hier_name]["pins"][pin] = resolved
                    design.nets.setdefault(resolved, {"pins": [], "drivers": [], "driver": None})
                    design.nets[resolved]["pins"].append((hier_name, pin))
            else:
                child = modules[cell]
                child_port_map: Dict[str, str] = {}
                conns = inst["connections"]
                if any(k.startswith("_") for k in conns):
                    for idx, (pname, _) in enumerate(child.ports):
                        key = f"_{idx}"
                        if key in conns:
                            child_port_map[pname] = resolve(conns[key])
                else:
                    for pname, net in conns.items():
                        if strip_power_pins and is_power_pin(pname):
                            continue
                        child_port_map[pname] = resolve(net)
                walk(cell, f"{hier_name}/", child_port_map)

        for w in mod.wires:
            design.nets.setdefault(resolve(w), {"pins": [], "drivers": [], "driver": None})

    walk(top, "", {})

    for pname, pinfo in design.ports.items():
        if strip_power_pins and is_power_pin(pname):
            continue
        design.nets.setdefault(pname, {"pins": [], "drivers": [], "driver": None})
        design.nets[pname]["pins"].append((f"PORT:{pname}", "PAD"))

    # Drop power ports/nets from the design object for checkers
    if strip_power_pins:
        design.ports = {
            k: v for k, v in design.ports.items() if not is_power_pin(k)
        }
        design.nets = {
            k: v for k, v in design.nets.items() if not is_power_pin(k)
        }

    return design