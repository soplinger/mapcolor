import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mapcolor  # noqa: E402
from mapcolor import (  # noqa: E402
    _BudgetExceeded,
    _colour_with_k,
    clique_bound,
    colour_map,
    conflicts,
    main,
    parse_map,
)

ROOT = Path(__file__).resolve().parent.parent
AUSTRALIA = ROOT / "examples" / "australia.map"
BROKEN = ROOT / "examples" / "broken.map"

C5 = """A: B, E
B: A, C
C: B, D
D: C, E
E: D, A
"""

K4 = """A: B, C, D
B: A, C, D
C: A, B, D
D: A, B, C
"""


def petersen_text():
    outer = [f"o{i}" for i in range(5)]
    inner = [f"i{i}" for i in range(5)]
    nbrs = {n: set() for n in outer + inner}
    for i in range(5):
        nbrs[outer[i]].update({outer[(i + 1) % 5], outer[i - 1], inner[i]})
        nbrs[inner[i]].update({inner[(i + 2) % 5], inner[i - 2], outer[i]})
    return "\n".join(f"{n}: {', '.join(sorted(v))}" for n, v in nbrs.items()) + "\n"


def write(tmp_path, text, name="map.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_parse_reports_line_numbers_for_syntax_and_name_errors():
    rm = parse_map("A\nbad name: C\nfix X\nA: B\nA: C\n")
    found = {(p.line, p.severity) for p in rm.problems}
    assert (2, "error") in found  # invalid region name
    assert (3, "error") in found  # malformed fix
    assert (5, "error") in found  # A declared twice
    messages = " ".join(p.message for p in rm.problems)
    assert "invalid region name" in messages
    assert "expected 'fix NAME = COLOUR'" in messages
    assert "already declared on line 1" in messages


def test_unknown_neighbour_and_self_loop_are_errors():
    rm = parse_map("A: B, Ghost\nB: A\nC: C\n")
    msgs = {p.line: p.message for p in rm.errors}
    assert "unknown region Ghost" in msgs[1]
    assert "cannot neighbour itself" in msgs[3]


def test_one_sided_listing_is_a_warning_and_is_symmetrised():
    rm = parse_map("A: B\nB\n")
    assert [p.line for p in rm.warnings] == [1]
    assert "B" in rm.adj["A"] and "A" in rm.adj["B"]
    assert rm.errors == []


def test_adjacent_pins_with_same_colour_are_errors():
    rm = parse_map("A: B\nB\nfix A = 1\nfix B = 1\n")
    assert any("adjacent" in p.message and p.line == 4 for p in rm.errors)


def test_fix_of_undeclared_region_is_an_error():
    rm = parse_map("A\nfix Nope = 2\n")
    assert any("undeclared region Nope" in p.message for p in rm.errors)


def test_colour_map_is_proper_for_every_method():
    rm = parse_map(AUSTRALIA.read_text(encoding="utf-8"))
    for method in mapcolor.METHODS:
        sol = colour_map(rm, method)
        assert conflicts(rm, sol.colours) == [], method
        assert sol.used == 3, method


def test_exact_finds_chromatic_number_of_odd_cycle():
    rm = parse_map(C5)
    sol = colour_map(rm, "exact")
    assert sol.used == 3 and sol.optimal
    assert len(clique_bound(rm)) == 2


def test_clique_bound_is_a_witness_for_complete_graph():
    rm = parse_map(K4)
    clique = clique_bound(rm)
    assert sorted(clique) == ["A", "B", "C", "D"]
    assert colour_map(rm, "exact").used == 4


def test_petersen_graph_needs_three_colours():
    rm = parse_map(petersen_text())
    sol = colour_map(rm, "exact")
    assert sol.used == 3 and sol.optimal
    assert conflicts(rm, sol.colours) == []


def test_pinned_colours_are_respected_and_counted():
    rm = parse_map(AUSTRALIA.read_text(encoding="utf-8").replace("fix WA = 1", "fix WA = 2"))
    sol = colour_map(rm, "exact")
    assert sol.colours["WA"] == 2
    assert conflicts(rm, sol.colours) == []
    assert sol.used == 3


def test_pins_above_palette_lower_bound():
    rm = parse_map("A\nfix A = 5\n")
    sol = colour_map(rm, "exact")
    assert sol.colours["A"] == 5 and sol.used == 5 and sol.optimal


def test_exact_search_gives_up_when_budget_is_spent():
    rm = parse_map(C5)
    adj = rm.adj
    with pytest.raises(_BudgetExceeded):
        _colour_with_k(adj, rm.order, {}, 2, 0)
    assert _colour_with_k(adj, rm.order, {}, 2, 1000) is None


def test_lint_command_exit_codes(capsys):
    assert main(["lint", str(AUSTRALIA)]) == 0
    assert "OK: 7 regions" in capsys.readouterr().out
    assert main(["lint", str(BROKEN)]) == 1
    err = capsys.readouterr().err
    assert f"{BROKEN}:" in err
    assert "unknown region Zeta" in err


def test_check_gate_passes_and_fails_by_limit(tmp_path, capsys):
    assert main(["check", str(AUSTRALIA), "--max-colours", "3"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert main(["check", str(AUSTRALIA), "--max-colours", "2"]) == 2
    assert "at least 3 colours" in capsys.readouterr().out


def test_colour_json_and_named_palette(capsys):
    assert main(["colour", str(AUSTRALIA), "--method", "exact", "--json"]) == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert data["colours_used"] == 3 and data["optimal"] is True
    assert {r["name"] for r in data["regions"]} == {"WA", "NT", "SA", "QLD", "NSW", "VIC", "TAS"}

    assert main(["colour", str(AUSTRALIA), "--names", "red,green,blue"]) == 0
    out = capsys.readouterr().out
    assert "WA" in out and "(pinned)" in out
    assert "red" in out

    assert main(["colour", str(AUSTRALIA), "--names", "red"]) == 2


def test_colour_max_colours_gate(capsys):
    assert main(["colour", str(AUSTRALIA), "--max-colours", "2"]) == 2
    assert "limit is 2" in capsys.readouterr().err


def test_dot_export_contains_coloured_nodes_and_edges(capsys):
    assert main(["dot", str(AUSTRALIA)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("graph map {")
    assert '"SA" -- "NSW";' in out or '"NSW" -- "SA";' in out
    assert "penwidth=3" in out  # WA is pinned


def test_neighbors_command(capsys):
    assert main(["neighbors", str(AUSTRALIA), "SA"]) == 0
    assert capsys.readouterr().out.startswith("SA (degree 5): WA, NT, QLD, NSW, VIC")
    assert main(["neighbors", str(AUSTRALIA), "Mars"]) == 1


def test_bounds_command_reports_proven_chromatic_number(capsys):
    assert main(["bounds", str(AUSTRALIA)]) == 0
    out = capsys.readouterr().out
    assert "chromatic number (proven): 3" in out
    assert "witness clique (3)" in out
