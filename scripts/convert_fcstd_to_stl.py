import os
import FreeCAD
import FreeCADGui
import Part

input_file = os.getenv("INPUT_FILE", "desk_buddy.FCStd")
output_file = os.getenv("OUTPUT_FILE", "desk_buddy.stl")

# Open FreeCAD file
FreeCAD.openDocument(input_file)
doc = FreeCAD.ActiveDocument

# Export the first object as STL
objects = [obj for obj in doc.Objects if hasattr(obj, "Shape")]
if objects:
    Part.export(objects, output_file)
    print(f"Exported {output_file} successfully.")
else:
    print("No valid objects found in the document for export.")

# Close the document
FreeCAD.closeDocument(doc.Name)
