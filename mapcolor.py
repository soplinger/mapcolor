#!/usr/bin/env python3
"""mapcolor: offline map-colouring toolkit for plain-text region maps.

Reads a region map written as plain text, lints it with line-numbered
errors, colours it with greedy, DSatur or exact search, shows a witness
clique as a lower bound, exports Graphviz DOT, and gates CI on the number
of colours a map needs. Python standard library only; no network access.
"""

import argparse
import json
import re
import sys
from collections import namedtuple

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
DEFAULT_BUDGET = 200_000
METHODS = ("greedy", "dsatur", "exact")
PALETTE_HEX = [
    "#e6194b", "#3cb44b", "#4363d8", "#ffe119", "#f58231", "#911eb4",
    "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff",
]

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_TOO_MANY = 2

Solution = namedtuple("Solution", "colours used lower optimal")


class Problem:
    def __init__(self, line, severity, message):
        self.line = line
        self.severity = severity
        self.message = message

    def format(self, filename):
        return f"{filename}:{self.line}: {self.severity}: {self.message}"


class RegionMap:
    """A parsed region map: declaration order, symmetric adjacency, pins."""

    def __init__(self):
        self.order = []
        self.adj = {}
        self.fixed = {}
        self.fix_lines = {}
        self.problems = []

    def add(self, line, severity, message):
        self.problems.append(Problem(line, severity, message))

    @property
    def errors(self):
        return [p for p in self.problems if p.severity == "error"]

    @property
    def warnings(self):
        return [p for p in self.problems if p.severity == "warning"]

    @property
    def edge_count(self):
        return sum(len(v) for v in self.adj.values()) // 2


def parse_map(text):
    """Parse the map format:

        # comment
        Name: Neighbour, Neighbour
        Isolated
        fix Name = 2
    """
    rm = RegionMap()
    declared = {}
    listed = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        if body.split(None, 1)[0] == "fix":
            fm = re.match(r"^fix\s+(\S+)\s*=\s*(\S+)$", body)
            if not fm:
                rm.add(lineno, "error", "expected 'fix NAME = COLOUR'")
                continue
            name, value = fm.groups()
            if not value.isdigit() or int(value) < 1:
                rm.add(lineno, "error", f"colour for {name} must be a whole number from 1 up")
            elif name in rm.fix_lines:
                rm.add(lineno, "error", f"{name} already fixed on line {rm.fix_lines[name][0]}")
            else:
                rm.fix_lines[name] = (lineno, int(value))
            continue

        name, _, rest = body.partition(":")
        name = name.strip()
        if not NAME_RE.match(name):
            rm.add(lineno, "error", f"invalid region name {name!r}")
            continue
        if name in declared:
            rm.add(lineno, "error", f"region {name} already declared on line {declared[name]}")
            continue
        declared[name] = lineno
        rm.order.append(name)

        seen = set()
        items = [x.strip() for x in rest.split(",")] if rest.strip() else []
        for nb in items:
            if not nb:
                rm.add(lineno, "error", "empty neighbour (trailing comma?)")
            elif not NAME_RE.match(nb):
                rm.add(lineno, "error", f"invalid neighbour name {nb!r}")
            elif nb == name:
                rm.add(lineno, "error", f"{name} cannot neighbour itself")
            elif nb in seen:
                rm.add(lineno, "warning", f"duplicate neighbour {nb}")
            else:
                seen.add(nb)
                listed.append((name, nb, lineno))

    rm.adj = {name: set() for name in rm.order}
    directed = {}
    for a, b, line in listed:
        if b not in declared:
            rm.add(line, "error", f"unknown region {b} (no line starts with '{b}:')")
            continue
        directed[(a, b)] = line
    for (a, b), line in directed.items():
        rm.adj[a].add(b)
        rm.adj[b].add(a)
        if (b, a) not in directed:
            rm.add(line, "warning", f"{a} lists {b} but {b} does not list {a}; treated as adjacent")

    for name, (line, colour) in rm.fix_lines.items():
        if name not in declared:
            rm.add(line, "error", f"fix refers to undeclared region {name}")
        else:
            rm.fixed[name] = colour

    pos = _index(rm.order)
    for name in rm.fixed:
        for nb in rm.adj[name]:
            if pos[nb] > pos[name] and nb in rm.fixed and rm.fixed[nb] == rm.fixed[name]:
                later = max(rm.fix_lines[name][0], rm.fix_lines[nb][0])
                rm.add(later, "error", f"{name} and {nb} are both fixed to colour {rm.fixed[name]} but are adjacent")

    rm.problems.sort(key=lambda p: p.line)
    return rm


def _index(order):
    return {name: i for i, name in enumerate(order)}


def _taken(adj, colours, name):
    return {colours[nb] for nb in adj[name] if nb in colours}


def _greedy(adj, order, fixed):
    """Largest-degree-first greedy colouring."""
    colours = dict(fixed)
    index = _index(order)
    for name in sorted(order, key=lambda n: (-len(adj[n]), index[n])):
        if name in colours:
            continue
        taken = _taken(adj, colours, name)
        c = 1
        while c in taken:
            c += 1
        colours[name] = c
    return colours


def _dsatur(adj, order, fixed):
    """DSatur: always colour the region with the most distinct neighbour colours."""
    colours = dict(fixed)
    index = _index(order)
    while len(colours) < len(order):
        pick = max(
            (n for n in order if n not in colours),
            key=lambda n: (len(_taken(adj, colours, n)), len(adj[n]), -index[n]),
        )
        taken = _taken(adj, colours, pick)
        c = 1
        while c in taken:
            c += 1
        colours[pick] = c
    return colours


class _BudgetExceeded(Exception):
    pass


def _colour_with_k(adj, order, fixed, k, budget):
    """Backtracking search for a colouring using only colours 1..k.

    Returns a colouring dict, or None if none exists. Raises _BudgetExceeded
    when more than `budget` search nodes are needed.
    """
    if fixed and max(fixed.values()) > k:
        return None
    colours = dict(fixed)
    index = _index(order)
    steps = 0

    def search():
        nonlocal steps
        uncoloured = [n for n in order if n not in colours]
        if not uncoloured:
            return True
        steps += 1
        if steps > budget:
            raise _BudgetExceeded()
        pick = max(
            uncoloured,
            key=lambda n: (len(_taken(adj, colours, n)), len(adj[n]), -index[n]),
        )
        taken = _taken(adj, colours, pick)
        # Without pinned colours, colours are interchangeable, so only one
        # unused colour beyond those already in use needs to be tried.
        ceiling = max(colours.values(), default=0) + 1 if not fixed else k
        for c in range(1, k + 1):
            if c in taken:
                continue
            if c > ceiling:
                break
            colours[pick] = c
            if search():
                return True
            del colours[pick]
        return False

    return dict(colours) if search() else None


def clique_bound(rm):
    """Greedy witness clique; every clique needs its own colour, so it is a lower bound."""
    index = _index(rm.order)
    best = []
    for v in sorted(rm.order, key=lambda n: (-len(rm.adj[n]), index[n])):
        clique = [v]
        for c in sorted(rm.adj[v], key=lambda n: (-len(rm.adj[n]), index[n])):
            if all(c in rm.adj[x] for x in clique):
                clique.append(c)
        if len(clique) > len(best):
            best = clique
    return best


def colour_map(rm, method="dsatur", budget=DEFAULT_BUDGET):
    """Colour the map. Colours are integers 1..used; `used` is the palette size needed."""
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    if not rm.order:
        return Solution({}, 0, 0, True)
    fixed = rm.fixed
    lower = max(len(clique_bound(rm)), max(fixed.values(), default=0), 1)
    if method == "greedy":
        colours = _greedy(rm.adj, rm.order, fixed)
    else:
        colours = _dsatur(rm.adj, rm.order, fixed)
    used = max(colours.values())
    if method != "exact":
        return Solution(colours, used, lower, used == lower)

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * len(rm.order) + 1000))
    best, upper = colours, used
    while upper > lower:
        try:
            found = _colour_with_k(rm.adj, rm.order, fixed, upper - 1, budget)
        except _BudgetExceeded:
            return Solution(best, upper, lower, False)
        if found is None:
            return Solution(best, upper, upper, True)
        best = found
        upper = max(found.values())
    return Solution(best, upper, upper, True)


def conflicts(rm, colours):
    """Adjacent pairs that share a colour, as (a, b, colour)."""
    pos = _index(rm.order)
    return [
        (a, b, colours[a])
        for a in rm.order
        for b in rm.adj[a]
        if pos[a] < pos[b] and colours[a] == colours[b]
    ]


# ---------------------------------------------------------------- CLI helpers

def _load(path):
    if path == "-":
        return "<stdin>", parse_map(sys.stdin.read())
    with open(path, encoding="utf-8") as fh:
        return path, parse_map(fh.read())


def _report(filename, rm, strict):
    for p in rm.problems:
        print(p.format(filename), file=sys.stderr)
    failing = rm.errors or (strict and rm.warnings)
    return bool(failing)


def _label(colour, names):
    if names and colour <= len(names):
        return names[colour - 1]
    return str(colour)


def _names(text):
    return [n.strip() for n in text.split(",") if n.strip()] if text else []


def cmd_lint(args):
    filename, rm = _load(args.file)
    if _report(filename, rm, args.strict):
        print(f"{filename}: {len(rm.errors)} error(s), {len(rm.warnings)} warning(s)")
        return EXIT_PROBLEMS
    print(f"OK: {len(rm.order)} regions, {rm.edge_count} borders, "
          f"{len(rm.warnings)} warning(s), {len(rm.fixed)} pinned")
    return EXIT_OK


def cmd_colour(args):
    filename, rm = _load(args.file)
    if _report(filename, rm, False):
        return EXIT_PROBLEMS
    sol = colour_map(rm, args.method, args.budget)
    names = _names(args.names)
    if names and len(names) < sol.used:
        print(f"palette has {len(names)} names but the map needs {sol.used} colours", file=sys.stderr)
        return EXIT_TOO_MANY

    if args.json:
        payload = {
            "method": args.method,
            "colours_used": sol.used,
            "lower_bound": sol.lower,
            "optimal": sol.optimal,
            "regions": [
                {"name": n, "colour": sol.colours[n], "pinned": n in rm.fixed}
                for n in rm.order
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        width = max([len("Region")] + [len(n) for n in rm.order])
        print(f"{'Region':<{width}}  Colour")
        for n in rm.order:
            pin = "  (pinned)" if n in rm.fixed else ""
            print(f"{n:<{width}}  {_label(sol.colours[n], names)}{pin}")
        proof = "optimal" if sol.optimal else f"not proven optimal, lower bound {sol.lower}"
        print(f"Colours used: {sol.used} ({proof}, method {args.method})")

    if args.max_colours is not None and sol.used > args.max_colours:
        print(f"needs {sol.used} colours, limit is {args.max_colours}", file=sys.stderr)
        return EXIT_TOO_MANY
    return EXIT_OK


def cmd_bounds(args):
    filename, rm = _load(args.file)
    if _report(filename, rm, False):
        return EXIT_PROBLEMS
    if not rm.order:
        print("no regions")
        return EXIT_OK
    clique = clique_bound(rm)
    greedy = colour_map(rm, "greedy").used
    dsatur = colour_map(rm, "dsatur").used
    exact = colour_map(rm, "exact", args.budget)
    print(f"witness clique ({len(clique)}): {', '.join(clique)}")
    print(f"lower bound (clique and pins): {exact.lower}")
    print(f"greedy upper bound: {greedy}")
    print(f"DSatur upper bound: {dsatur}")
    if exact.optimal:
        print(f"chromatic number (proven): {exact.used}")
    else:
        print(f"chromatic number: unknown, between {exact.lower} and {exact.used} "
              f"(search budget of {args.budget} nodes exhausted)")
    return EXIT_OK


def cmd_check(args):
    filename, rm = _load(args.file)
    if _report(filename, rm, args.strict):
        print(f"FAIL: {filename} has problems")
        return EXIT_PROBLEMS
    sol = colour_map(rm, "exact", args.budget)
    limit = args.max_colours
    if sol.lower > limit:
        print(f"FAIL: at least {sol.lower} colours are needed, limit is {limit}")
        return EXIT_TOO_MANY
    if sol.used <= limit:
        print(f"PASS: colourable with {limit} colours (uses {sol.used})")
        return EXIT_OK
    if sol.optimal:
        print(f"FAIL: needs {sol.used} colours, limit is {limit}")
    else:
        print(f"FAIL: no colouring within {limit} colours found; "
              f"best {sol.used}, lower bound {sol.lower} (budget exhausted)")
    return EXIT_TOO_MANY


def cmd_dot(args):
    filename, rm = _load(args.file)
    if _report(filename, rm, False):
        return EXIT_PROBLEMS
    sol = colour_map(rm, args.method, args.budget)
    lines = ["graph map {", "  node [style=filled, shape=box];"]
    for n in rm.order:
        c = sol.colours[n]
        extra = ", penwidth=3" if n in rm.fixed else ""
        lines.append(f'  "{n}" [label="{n}\\n{c}", fillcolor="{PALETTE_HEX[(c - 1) % len(PALETTE_HEX)]}"{extra}];')
    for a in rm.order:
        for b in sorted(rm.adj[a], key=rm.order.index):
            if rm.order.index(a) < rm.order.index(b):
                lines.append(f'  "{a}" -- "{b}";')
    lines.append("}")
    print("\n".join(lines))
    return EXIT_OK


def cmd_neighbors(args):
    filename, rm = _load(args.file)
    if _report(filename, rm, False):
        return EXIT_PROBLEMS
    if args.region not in rm.adj:
        print(f"{filename}: no region named {args.region}", file=sys.stderr)
        return EXIT_PROBLEMS
    nbs = sorted(rm.adj[args.region], key=rm.order.index)
    print(f"{args.region} (degree {len(nbs)}): {', '.join(nbs) if nbs else '-'}")
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mapcolor",
        description="Offline map-colouring toolkit for plain-text region maps.",
        epilog="Exit codes: 0 ok, 1 map has problems, 2 colour limit exceeded.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, budget=True):
        p.add_argument("file", help="map file, or - for stdin")
        if budget:
            p.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                           help="exact-search node budget (default %(default)s)")

    p = sub.add_parser("lint", help="check the map file with line-numbered errors")
    add_common(p, budget=False)
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("colour", help="colour the map and print the assignment")
    add_common(p)
    p.add_argument("--method", choices=METHODS, default="dsatur")
    p.add_argument("--names", help="comma-separated colour names, e.g. red,green,blue")
    p.add_argument("--max-colours", type=int, help="exit 2 if more colours are needed")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.set_defaults(func=cmd_colour)

    p = sub.add_parser("bounds", help="show clique, greedy, DSatur and exact bounds")
    add_common(p)
    p.set_defaults(func=cmd_bounds)

    p = sub.add_parser("check", help="CI gate: can the map be coloured with N colours?")
    add_common(p)
    p.add_argument("--max-colours", type=int, required=True)
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("dot", help="export a coloured Graphviz DOT graph")
    add_common(p)
    p.add_argument("--method", choices=METHODS, default="dsatur")
    p.set_defaults(func=cmd_dot)

    p = sub.add_parser("neighbors", help="list a region's neighbours")
    add_common(p, budget=False)
    p.add_argument("region")
    p.set_defaults(func=cmd_neighbors)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"mapcolor: {exc}", file=sys.stderr)
        return EXIT_PROBLEMS


if __name__ == "__main__":
    sys.exit(main())
