"""
FreeCAD headless script to export all App::Part objects as STL files.
Run with:
    "C:\\Program Files\\FreeCAD 1.1\\bin\\FreeCADCmd.exe" export_stls.py
"""
import sys
import os
import shutil
import re
import FreeCAD
import MeshPart
import Part

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FCSTD_PATH = os.path.join(BASE_DIR, "desk_buddy.FCStd")
STL_DIR = os.path.join(BASE_DIR, "STLs")


def safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name or "unnamed_part"


print(f"Opening: {FCSTD_PATH}")

if not os.path.exists(FCSTD_PATH):
    print(f"ERROR: File not found: {FCSTD_PATH}")
    sys.exit(1)

doc = FreeCAD.openDocument(FCSTD_PATH)

if os.path.exists(STL_DIR):
    shutil.rmtree(STL_DIR)
os.makedirs(STL_DIR)

parts = [obj for obj in doc.Objects if obj.TypeId == "App::Part"]
print(f"Found {len(parts)} App::Part objects")

errors = []
exported = 0

for part_obj in parts:
    label = part_obj.Label
    print(f"\nProcessing part: {label}")

    shapes = []
    seen_names = set()

    for child in part_obj.OutListRecursive:
        if child.Name in seen_names:
            continue
        seen_names.add(child.Name)

        if hasattr(child, "Shape") and not child.Shape.isNull() and len(child.Shape.Solids) > 0:
            shapes.append(child.Shape)

    if not shapes:
        print(f"  WARN: No solid shapes found for {label!r}, skipping")
        continue

    try:
        compound = Part.makeCompound(shapes)
        mesh = MeshPart.meshFromShape(
            Shape=compound,
            LinearDeflection=0.1,
            AngularDeflection=0.523599,
            Relative=False,
        )

        filename = safe_filename(label) + ".stl"
        out_path = os.path.join(STL_DIR, filename)
        mesh.write(out_path)

        size_kb = os.path.getsize(out_path) // 1024
        exported += 1
        print(f"  OK: {filename} ({size_kb} KB)")

    except Exception as e:
        print(f"  ERROR: {label!r}: {e}")
        errors.append(label)

print(f"\nExported {exported} STL file(s) to:")
print(STL_DIR)

if errors:
    print(f"\nERRORS ({len(errors)}): {errors}")
    sys.exit(1)

sys.exit(0)