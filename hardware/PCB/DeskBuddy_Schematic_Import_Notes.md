# DeskBuddy reconstructed EasyEDA schematic

Created 2026-09-19 from the repaired PCB. This is a real, editable
**EasyEDA Standard schematic JSON** containing symbols, pins, wires,
junctions, and electrical net labels—not a picture inserted into a sheet.

## Files

- [Schematic to open in EasyEDA](SCH_DeskBuddy_SMD_v1.0_RECONSTRUCTED_2026-09-19.json)
- [PDF preview](DeskBuddy_Schematic_Preview.pdf)
- [SVG preview](DeskBuddy_Schematic_Preview.svg)
- [Detailed connection validation](DeskBuddy_Schematic_Validation.json)

The PDF/SVG previews were rendered locally from the generated JSON's actual
primitives. They are not screenshots or exports from the EasyEDA editor.

## Import

1. Open **EasyEDA Standard**, the same edition used for the repaired PCB.
2. Choose **File → Open → EasyEDA…** and select
   `SCH_DeskBuddy_SMD_v1.0_RECONSTRUCTED_2026-09-19.json`. No unzip is needed.
3. Save the sheet into a **copy of your DeskBuddy project**, alongside a copy
   of the repaired PCB. Keep only one version of this schematic in that
   project to avoid duplicate component references.
4. Check the sheet in **Design Manager** and **Tools → Footprint Manager**.
   Inspect the power, connector, and LED pin assignments, then run the
   editor's schematic checks. The 26 header pins labelled “open” intentionally
   have no added electrical no-connect flags: confirm whether they should
   remain unused before dismissing or suppressing related warnings.
5. You do **not** need to regenerate or update the PCB just to view/save the
   schematic. If you later use **Update PCB**, review the proposed changes
   before applying them. It should not require replacing all components,
   changing footprint assignments, or moving the established layout. Cancel
   unexpected changes and keep the known-good board intact.

EasyEDA documents this import route in its
[source-file guide](https://docs.easyeda.com/en/Export/Export-EasyEDA-Source-File/).
Its [Footprint Manager guide](https://docs.easyeda.com/en/Schematic/Footprint-Manager/)
explains the symbol-pin/footprint-pad checks.

All original component instance IDs are retained in this sheet to support
matching to the repaired PCB. If the editor changes IDs during import and
proposes deleting/re-adding components, work on the project copies and follow
EasyEDA's [Reset Component ID guidance](https://docs.easyeda.com/en/Schematic/Reset-Component-ID/)
for **both** schematic and PCB. Do not reset IDs or re-annotate the production
board automatically just to make an unexpected update dialog disappear.

## What was reconstructed

One sheet with five functional sections:

1. Power input, fuse holder, diode, ferrite bead, and bulk/logic filtering.
2. The two 20-pin controller sockets, U3 and U4.
3. Five servo connectors and their shared-rail decoupling capacitors.
4. Button/input and auxiliary headers.
5. The two LED/resistor indicator branches.

The source's **29 components, 19 named nets, 100 logical pins, and 26 open
pins** are all represented. The PCB has 102 physical pads: FH1 uses two pads
numbered 1 and two numbered 2, correctly represented by two schematic pins.
Vias, mounting holes, and board artwork are not added as electrical parts.

Original references—including `1UF`, `DIODE`, `FERRITE`, `BICEP1`, and
`BTNFING1/2`—are deliberately not renumbered. Original values, supplier part
numbers, manufacturer metadata, footprint names, and footprint UUIDs are
preserved. Short readable captions supplement some long part names; the full
original names remain in the component's hidden Name field.

Symbols for all 14 distinct supplier parts were retrieved from the public
LCEDA/EasyEDA component library and embedded in the schematic. The reviewed
snapshot, source URLs, symbol UUIDs, and retrieval metadata are in
[DeskBuddy_Schematic_Symbol_Sources.json](DeskBuddy_Schematic_Symbol_Sources.json).
No private project was uploaded or published.

Important details preserved rather than guessed:

- `DIODE` uses the supplier SS34 symbol: pin 2 is A, pin 1 is K.
- `LED1` and `LED2` use their supplier symbols: pin 1 is A, pin 2 is K.
  Their anodes connect to nets `41` / `42`, respectively; their cathodes
  connect through R1 / R2 to GND, exactly as in the PCB.
- U3 and U4 remain physical female headers. No unverified ESP32 module
  schematic, GPIO numbering, onboard regulator, or USB circuitry was invented.
- `BICEP1` connects to `CLK / 3V / GND`. It is distinct from the servo
  connector `BICEP`, which connects to `S2 / +SV / GND`.
- `+5V`, `3V`, `ESP32_+V`, and `+SV` remain exact net names. Their labels do
  not establish the actual supply voltages or an acceptable input-voltage
  range. This PCB's part list has no voltage regulator.
- FH1 is a fuse holder. The PCB does not specify the installed fuse rating.
- The current C2937625 library symbol references a newer three-pin footprint.
  For all six instances, the schematic keeps the PCB's existing footprint
  UUID `5c0be672313e4206a41dd5398a02f108`, not the newer library binding.
  Do not blindly update these components from the library.

## Verification and limits

The generator re-parses the **serialized output JSON** into a connection
graph using pin contact points, wire segments, junctions, and net-label
anchors. Every logical pin-to-net assignment matches the repaired PCB;
all unassigned pins remain isolated. Graphics are not treated as wires.
Different-net crossings, detached labels, duplicate object IDs, and changed
component/footprint metadata are rejected.

**All 11 regression tests passed**, including deliberately swapped LED pins,
a removed wire, a displaced label, an added supply short, duplicate IDs,
and unsupported symbol primitives. The final sheet was rendered and visually
inspected for readability. The input PCB's SHA-256 remains unchanged.

This is a reconstruction of the board's existing wiring, **not recovery of
the original schematic or independent certification of circuit intent**.
Native EasyEDA import, schematic ERC, footprint-library resolution, and
schematic-to-PCB updating have **not** been exercised locally. The existing
PCB's successful manufacturing checks do not verify supply limits, fuse
selection, motor currents, external module compatibility, or circuit behavior.
No changes were made to the PCB or its manufacturing files during this task.

## Reproduce

The cached-symbol generator and tests require only Python's standard library:

```sh
python build_easyeda_schematic.py
python -m unittest discover -s . -p test_easyeda_schematic.py -v
```

For the optional PDF preview, install `CairoSVG==2.8.2` in a virtual environment
and run `python build_easyeda_schematic.py --pdf`. The SVG preview needs no
extra package. The generator refuses to overwrite a schematic edited since
its previous generation record. It is locked to the reviewed PCB hash.

The separate `fetch_schematic_symbols.py` documents how the public symbol
snapshot was acquired; it refuses to overwrite the existing reviewed cache.
Normal regeneration is offline and uses that cache.

Source PCB SHA-256:
`1ccdc89fb0edde693c4eb8525345681ae8917bf12867a2816e3d31251f47badc`

Schematic SHA-256:
`3a040243b15a1bc4223997db51bbf6081848a53e004683e71c0ec4ab7eab3dc0`

Format references:
[schematic primitives](https://docs.easyeda.com/en/DocumentFormat/2-EasyEDA-Schematic-File-Format/),
[current Standard document format](https://docs.easyeda.com/en/DocumentFormat/EasyEDA-Format-Standard/).
