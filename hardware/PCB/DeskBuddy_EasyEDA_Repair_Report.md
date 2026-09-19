# DeskBuddy native EasyEDA PCB repair

Repaired 2026-09-19. This is an editable **EasyEDA Standard PCB JSON**, not a
schematic, a replacement BOM, or fabrication approval. The original JSON is
unchanged.

Use [PCB_DeskBuddy_SMD_v1.0_FIXED_2026-09-19.json](PCB_DeskBuddy_SMD_v1.0_FIXED_2026-09-19.json).
The [copper review](DeskBuddy_EasyEDA_Copper_Review.svg) shows the revised top
and bottom layers, with modified copper objects highlighted yellow. Both
layers are viewed from above; the preview is not a fabrication file.

## What was found and corrected

1. **Malformed outline.** The source had overlapping/retraced left-edge
   segments and an unclosed contour. Replaced it with the six main corners
   used in the earlier CAM repair: a 40.132 × 80.010 mm stepped outline.
   Updated the cached board bounding box accordingly.
2. **Tight signal routes and edge clearance.** Transferred all six earlier
   CAM signal-route corrections into their actual native TRACK objects.
   Signal widths, net assignments, pads, and connection points are retained.
   The original approximately 0.154 mm minimum spacing was above the default
   0.152 mm rule: this part is a margin improvement, not a claim that every
   original signal route violated the file's rules.
3. **Two genuine power-net clearance violations.** One +SV approach at C3
   and a tiny GND stub at C6 gave about 0.395 mm clearance instead of the
   configured 0.400 mm. Moved the C3 approach within its original pad and
   removed the redundant 0.005842 mm C6 stub. The adjoining GND route still
   overlaps C6's original pad; its electrical connection is unchanged.
4. **Ten power-branch TRACK objects below their assigned minimum width.**
   Six were 1.0 mm and four were 0.3 mm, versus the power rule's 1.8 mm.
   Widened them to 1.8 mm. Adjusted short approaches at C1–C5 to maintain
   clearance after widening; their endpoints remain inside the original
   pads. Also adjusted the C1 GND bend to clear the +SV trunk. This follows
   the supplied design rules; it is not a motor-current/thermal calculation.
5. **Two vias below their assigned size rule.** Enlarged the +SV via
   `gge25428` and GND via `gge25455` from approximately 0.62/0.31 mm
   copper/drill diameter to 1.20/0.60 mm, matching the existing power rule.
   Their centers and net assignments are unchanged. **The drill sizes have
   changed, so the earlier Gerber ZIP is not the manufacturing output for
   this revision.**

There are 21 changed/removed top-level objects: the outline, 17 copper TRACK
objects, one redundant copper TRACK removed, and two vias. All 29 component
LIB records are byte-for-byte unchanged, including their pads, locations,
rotations, labels, masks, and part metadata. Other text/artwork, the mounting
hole, layer settings, assigned nets, and design/router rules are unchanged.
No rules were relaxed, no pads moved, and no ground pour was introduced.
The removed tiny stub remains recoverable from the original JSON and the
before/after record in the validation report.

## Verification results

These results come from the local geometry/net auditor, **not** a run of
EasyEDA's own DRC engine.

| Check | Original source | Corrected source |
| --- | ---: | ---: |
| Closed, simple outline | No | Yes |
| Different-net copper shorts found | 0 | 0 |
| Disconnected named PCB nets found | 0 | 0 |
| Configured clearance violations | 2 | 0 |
| Undersized TRACK objects | 10 | 0 |
| Undersized vias | 2 | 0 |
| Minimum top copper-island spacing | 0.173 mm | 0.212 mm |
| Minimum bottom copper-island spacing | 0.154 mm | 0.206 mm |
| Top / bottom copper-to-outline margin | Invalid original contour | 0.751 / 0.418 mm |
| Copper outside corrected outline | — | 0 mm² |
| Physical pad/via connectivity groups | 45 | 45, identical membership |
| Drilled features | 85 | 85, centers unchanged |

All 19 named nets remain connected according to the net assignments stored
in this PCB. All 102 component pads and 10 vias retain their original
physical connection groups. The 26 pads with no assigned net remain isolated;
their intended use cannot be inferred from this PCB alone.

Validation details:

- Parsed native coordinates at 0.254 mm per EasyEDA unit, with origin and
  axis conversion independently checked against the supplied manufacturing
  files. Hole fields were checked against the actual drills, not guessed
  from their names.
- Original native copper agrees with the original Gerbers within 0.0001 mm
  geometry tolerance. Original drill centers/sizes agree within 0.001 mm.
- The intermediate native version containing only the earlier CAM changes
  agrees with that repaired CAM ZIP within the same tolerance. The final
  version then adds the power-branch, via, and capacitor-approach repairs.
- Copper envelopes, pad shapes, and plated layer transitions form the
  connectivity graph. Subtracting the actual drill/slot geometry does not
  split or erase any copper island; plated holes remain inside their pads
  with positive annular rings. All holes remain inside the repaired board.
- Clearance uses the stricter rule of each pair of different nets.
  Checks allow 0.0001 mm for coordinate/curve approximation only.
- All unchanged component objects and document metadata were compared.
  Tests also deliberately add a short, remove a signal route, and introduce
  unsupported copper to verify that the checker detects or rejects them.
- Top/bottom copper previews were rendered and visually inspected.
- All seven regression tests passed on the final scripts and source.

Complete measurements, exact object-level before/after strings, connectivity
partitions, and file hashes are in
[DeskBuddy_EasyEDA_Validation.json](DeskBuddy_EasyEDA_Validation.json).

## Open and finish in EasyEDA

1. In **EasyEDA Standard**, use **File → Open → EasyEDA…** and select the
   corrected JSON. Save it as a separate revision of your project. No unzip
   step is needed. See the [official import/export instructions](https://docs.easyeda.com/en/Export/Export-EasyEDA-Source-File/).
2. Run **Design → Check DRC**, or refresh **Design Manager → DRC Error**.
   Review any remaining findings in the actual editor. The supplied file
   keeps its original settings, including automatic application of rule
   sizes while routing being disabled; consider enabling that setting for
   subsequent edits. See the [official DRC guide](https://docs.easyeda.com/en/PCB/Design-Rule-Check/).
3. Verify the schematic/netlist and connector pinouts against the intended
   electronics. A PCB can be internally connected correctly yet implement
   the wrong circuit. This export contains no schematic.
4. Generate a **new complete Gerber and drill package** from this corrected
   revision. Do not mix its copper with old drill files or submit the earlier
   CAM-repaired ZIP as if it represented this revision. Review the new export
   in the manufacturer's viewer. Check whether the export includes a
   redundant via-only drill file before assembling the fabrication package;
   that was an export-package issue, not a duplicate-via problem in this JSON.

Still unverified: circuit intent, component ratings/polarity correctness,
connector-to-real-device pin mapping, motor stall-current capacity and
thermal rise, copper weight, RF/antenna requirements, solder-mask/paste and
silkscreen manufacturability, mechanical fit, and the manufacturer's full
production rules. Native EasyEDA import/DRC has not been exercised locally.
Share the matching schematic JSON to enable a schematic-aware review.

## Reproduce the checks

From this directory, in a Python virtual environment:

```sh
python -m pip install -r easyeda-repair-requirements.txt
python repair_easyeda.py
python -m unittest discover -s . -p test_easyeda_repair.py -v
python preview_easyeda.py
```

The repair script is locked to the original source SHA-256 and needs both
existing Gerber ZIPs for cross-checking. It refuses to overwrite a corrected
JSON whose contents have changed. Preview generation uses the same geometry
parser but simplifies curves for display only.

Original SHA-256:
`78471450174e37da597a697384db525a9fe6efdecb9fda3a492f804d64988c4e`

Corrected SHA-256:
`1ccdc89fb0edde693c4eb8525345681ae8917bf12867a2816e3d31251f47badc`
