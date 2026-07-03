"""
Create a Live2D model from generated character layers.

Approach: Create a PSD file with properly named layers,
then use Cubism Editor's template function to auto-rig it.

Layer naming follows Live2D Cubism conventions:
  - ArtMesh names must match template expectations
"""
import os
from PIL import Image
from psd_tools import PSDImage
from psd_tools.api.layers import PixelLayer
import struct

def create_combined_psd():
    """Create a PSD with all character layers for Cubism import."""
    output_dir = "D:/White Salary/live2d_models/generated"
    psd_path = os.path.join(output_dir, "white_salary_character.psd")

    print("=== Creating Live2D PSD ===")

    # Load all layers
    layers_info = [
        ("layer_1_back_hair.png", "Hair_Back"),
        ("layer_2_body.png", "Body"),
        ("layer_3_face.png", "Face"),
        ("layer_4_eyes.png", "Eye_L"),  # Combined eyes, Cubism can split later
        ("layer_5_front_hair.png", "Hair_Front"),
    ]

    # Get canvas size from the first layer
    ref = Image.open(os.path.join(output_dir, "full_character.png"))
    canvas_w, canvas_h = ref.size
    print(f"Canvas: {canvas_w}x{canvas_h}")

    # Create a simple combined image for Cubism import
    # Cubism can work with a single flat image and auto-mesh it
    # But for better results, we create separate texture files

    # For Cubism Editor, the easiest approach is:
    # 1. Import the full character image as the base
    # 2. Use Auto Mesh to create the mesh
    # 3. Use Auto Face Deformer for face rigging
    # 4. Use Auto Face Motion for expressions

    # Create the model directory structure
    model_dir = "D:/White Salary/live2d_models/white_salary"
    os.makedirs(model_dir, exist_ok=True)

    # Copy the full character as the main texture
    full_char = Image.open(os.path.join(output_dir, "full_character.png"))
    full_char.save(os.path.join(model_dir, "texture_00.png"))

    # Save individual layers as separate textures
    for i, (filename, name) in enumerate(layers_info):
        src = os.path.join(output_dir, filename)
        if os.path.exists(src):
            img = Image.open(src)
            img.save(os.path.join(model_dir, f"texture_{i+1:02d}_{name}.png"))
            print(f"  {name}: {img.size}")

    # Create a model configuration JSON for reference
    import json
    config = {
        "name": "White Salary",
        "canvas_size": {"width": canvas_w, "height": canvas_h},
        "textures": ["texture_00.png"],
        "layers": [
            {"name": info[1], "file": f"texture_{i+1:02d}_{info[1]}.png"}
            for i, info in enumerate(layers_info)
        ],
        "notes": "Import texture_00.png into Cubism Editor, use Auto features for rigging"
    }

    config_path = os.path.join(model_dir, "model_info.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n=== Model files saved to {model_dir} ===")
    print(f"\nTo create Live2D model:")
    print(f"  1. Open Live2D Cubism Editor (D:\\L2D\\Live2D Cubism 5.3)")
    print(f"  2. File > New Model")
    print(f"  3. Drag texture_00.png onto the canvas")
    print(f"  4. Edit > Auto Generate Mesh")
    print(f"  5. Modeling > Auto Generate Face Deformer")
    print(f"  6. Modeling > Auto Generate Face Motion")
    print(f"  7. Physics > Add Hair/Accessory Physics")
    print(f"  8. File > Export for Runtime (.moc3)")
    print(f"  9. Copy .moc3 + .model3.json + textures to:")
    print(f"     D:\\White Salary\\live2d_models\\default\\")

    return model_dir

if __name__ == "__main__":
    create_combined_psd()
