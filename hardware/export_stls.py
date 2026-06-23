"""
FreeCAD headless script to export all App::Part objects as STL files.
Run with: FreeCADCmd hardware/export_stls.py
"""
import sys
import os
import shutil
import FreeCAD
import MeshPart
import Mesh
import Part

FCSTD_PATH = os.path.join(os.path.dirname(__file__), "desk_buddy.FCStd")
STL_DIR = os.path.join(os.path.dirname(__file__), "STLs")

print(f"Opening {FCSTD_PATH}")
doc = FreeCAD.openDocument(FCSTD_PATH)

# Wipe and recreate STLs directory
if os.path.exists(STL_DIR):
    shutil.rmtree(STL_DIR)
os.makedirs(STL_DIR)

parts = [obj for obj in doc.Objects if obj.TypeId == "App::Part"]
print(f"Found {len(parts)} parts")

errors = []
for part in parts:
    label = part.Label

    # Collect all solid shapes from children recursively
    shapes = []
    for child in part.OutListRecursive:
        if hasattr(child, "Shape") and child.Shape.Solids:
            shapes.append(child.Shape)

    if not shapes:
        print(f"  WARN: {label!r} has no solid shapes, skipping")
        continue

    try:
        compound = Part.makeCompound(shapes)
        mesh = MeshPart.meshFromShape(
            Shape=compound,
            LinearDeflection=0.1,
            AngularDeflection=0.523599,  # 30 degrees
        )
        out_path = os.path.join(STL_DIR, f"{label}.stl")
        mesh.write(out_path)
        size_kb = os.path.getsize(out_path) // 1024
        print(f"  OK: {label}.stl ({size_kb} KB)")
    except Exception as e:
        print(f"  ERROR: {label!r}: {e}")
        errors.append(label)

print(f"\nExported {len(os.listdir(STL_DIR))} STL files to {STL_DIR}")
if errors:
    print(f"ERRORS ({len(errors)}): {errors}")
    sys.exit(1)
