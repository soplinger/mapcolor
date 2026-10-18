# mapcolor

**Offline map-colouring toolkit: plain-text region maps in, proper colourings, exact chromatic numbers and CI gates out.**

`mapcolor` reads a region map written as plain text, lints it with line-numbered errors, colours it with greedy, DSatur or exact search, proves how many colours a map really needs, and exports coloured Graphviz diagrams. It is a single Python file using only the standard library. Nothing touches the network, and there are no accounts or API keys.

## Features

- **Readable map format**: one line per region, `Name: Neighbour, Neighbour`, with `#` comments and bare names for isolated regions.
- **Line-numbered lint**: invalid names, malformed pins, duplicate declarations, self-borders, unknown neighbours and conflicting pins are reported as `file:line: error: ...`.
- **Asymmetry detection**: a border listed on one side only is reported as a warning and treated as shared; `lint --strict` turns warnings into failures.
- **Pinned regions**: `fix Name = 2` fixes a region's colour. Pins are honoured by every method and checked against adjacent pins.
- **Three colouring methods**: `greedy` (largest-degree-first), `dsatur` (saturation-based, the default) and `exact` (backtracking that proves the minimum).
- **Exact chromatic number**: the exact method lowers the palette size step by step until the search proves no smaller palette works, with a node budget so large maps fail gracefully instead of hanging.
- **Witness clique**: `bounds` prints the clique that forces the lower bound, so you can see *why* a map needs N colours.
- **Named palettes**: `--names red,green,blue` replaces numbers with your own colour names and refuses palettes that are too small.
- **Colour table or JSON**: a readable table by default, or `--json` for scripts, with the colour count, lower bound and optimality flag.
- **CI gate**: `check --max-colours N` exits `2` when a map cannot be coloured within the limit, and `colour --max-colours N` does the same for a chosen method.
- **Graphviz export**: `dot` writes a coloured `graph map { ... }` you can render with `dot -Tsvg`; pinned regions are drawn with heavier outlines.
- **Neighbour inspection**: `neighbors` lists a region's borders and degree.
- **Stdin support**: pass `-` as the file name to read a map from a pipe.

## Map format

```
# comments start with #
WA: NT, SA              # WA borders NT and SA
TAS                     # an isolated region
fix WA = 1              # pin WA to colour 1
```

Rules: names use letters, digits, `_`, `.` and `-`; every neighbour must be declared on its own line; each region is declared once.

## Quick start

```sh
python3 mapcolor.py lint examples/australia.map
python3 mapcolor.py colour examples/australia.map --names red,green,blue
python3 mapcolor.py bounds examples/australia.map
python3 mapcolor.py check examples/australia.map --max-colours 3
```

Expected output of the colouring step:

```
Region  Colour
WA      red  (pinned)
NT      blue
SA      green
QLD     red
NSW     blue
VIC     red
TAS     red
Colours used: 3 (optimal, method dsatur)
```

Lint a broken map to see line-numbered errors (exit code `1`):

```sh
python3 mapcolor.py lint examples/broken.map
```

Render a coloured picture:

```sh
python3 mapcolor.py dot examples/australia.map | dot -Tsvg -o australia.svg
```

## Commands

| Command | Purpose |
| --- | --- |
| `lint FILE [--strict]` | Check the map and print `file:line` problems |
| `colour FILE [--method M] [--names N] [--max-colours K] [--json]` | Colour the map |
| `bounds FILE [--budget N]` | Show the witness clique, greedy and DSatur bounds, and the exact number |
| `check FILE --max-colours K [--strict]` | CI gate: can the map be coloured with K colours? |
| `dot FILE [--method M]` | Export coloured Graphviz DOT |
| `neighbors FILE REGION` | List a region's neighbours |

Exit codes: `0` success, `1` the map has problems (or `lint --strict` warnings), `2` the colour limit is exceeded, and argparse usage errors also exit `2`.

## Requirements

Python 3.8 or newer. Standard library only for the program. Tests need `pytest`.

## How to test

```sh
pip install pytest
python3 -m pytest tests/
```

The tests cover parsing and line-numbered errors, symmetry warnings, pin validation, every colouring method checked for proper colourings, exact chromatic numbers (odd cycle, complete graph, Petersen graph, Australia), budget exhaustion, and the CLI exit codes for `lint`, `check`, `colour`, `dot`, `neighbors` and `bounds`.

## Limits

The exact search is a backtracking search, so very large or dense maps may hit the node budget (`--budget`, default 200000). When that happens `bounds` and `check` say so and report the best bounds they have, rather than guessing.
