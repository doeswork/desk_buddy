import os
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from stl import mesh

input_file = os.getenv("INPUT_FILE", "desk_buddy.stl")
output_file = os.getenv("OUTPUT_FILE", "desk_buddy.jpg")

def stl_to_jpg(input_file, output_file):
    # Load the STL file
    your_mesh = mesh.Mesh.from_file(input_file)

    # Create a new plot
    figure = plt.figure()
    axes = figure.add_subplot(111, projection='3d')

    # Add the vectors to the plot
    axes.add_collection3d(plt.art3d.Poly3DCollection(your_mesh.vectors))

    # Auto scale to the mesh size
    scale = your_mesh.points.flatten()
    axes.auto_scale_xyz(scale, scale, scale)

    # Turn off the grid and axes
    plt.axis('off')

    # Save the image
    plt.savefig(output_file, dpi=300, bbox_inches='tight', pad_inches=0)
    plt.close()

stl_to_jpg(input_file, output_file)
print(f"Rendered STL to {output_file} successfully.")
