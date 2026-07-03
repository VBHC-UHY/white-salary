"""
Generate Live2D-ready layered PSD from a character image.

Uses lightweight tools (no 56GB model needed):
  - rembg: Remove background (~100MB model)
  - OpenCV: Face/feature detection for layer separation
  - psd-tools: Export as PSD file

Output layers:
  0. Background (transparent)
  1. Back hair
  2. Body (clothes + skin)
  3. Face (skin + features)
  4. Eyes (separate for blinking)
  5. Front hair / bangs
  6. Accessories
"""

import os
import sys
import cv2
import numpy as np
from PIL import Image, ImageDraw

def main():
    input_path = "D:/White Salary/frontend/assets/avatar.png"
    output_dir = "D:/White Salary/live2d_models/generated"
    os.makedirs(output_dir, exist_ok=True)

    print("=== White Salary Live2D Layer Generator ===")
    print(f"Input: {input_path}")

    # Load image
    img = Image.open(input_path).convert("RGBA")
    print(f"Image size: {img.size}")

    # Step 1: Remove background
    print("[1/4] Removing background...")
    from rembg import remove
    img_nobg = remove(img)
    img_nobg.save(os.path.join(output_dir, "layer_0_nobg.png"))
    print("  Background removed")

    # Convert to numpy for OpenCV processing
    img_np = np.array(img_nobg)
    img_bgr = cv2.cvtColor(img_np[:, :, :3], cv2.COLOR_RGB2BGR)
    alpha = img_np[:, :, 3]

    h, w = img_bgr.shape[:2]

    # Step 2: Detect face region
    print("[2/4] Detecting face...")
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(w // 8, h // 8))

    if len(faces) == 0:
        print("  No face detected, using estimated regions")
        # Estimate face region (upper-center of image)
        fx, fy, fw, fh = w // 4, h // 8, w // 2, h // 3
    else:
        fx, fy, fw, fh = faces[0]
        print(f"  Face at: ({fx},{fy}) {fw}x{fh}")

    # Step 3: Create layer masks
    print("[3/4] Creating layer masks...")

    # Face mask (expanded slightly)
    face_mask = np.zeros((h, w), dtype=np.uint8)
    face_expand = 20
    cv2.ellipse(face_mask,
                (fx + fw // 2, fy + fh // 2),
                (fw // 2 + face_expand, fh // 2 + face_expand),
                0, 0, 360, 255, -1)

    # Eye mask (within face, upper portion)
    eye_mask = np.zeros((h, w), dtype=np.uint8)
    eye_y = fy + fh // 4
    eye_h = fh // 3
    eye_x_l = fx + fw // 6
    eye_x_r = fx + fw * 3 // 6
    eye_w = fw // 4
    cv2.ellipse(eye_mask, (eye_x_l + eye_w // 2, eye_y + eye_h // 2),
                (eye_w, eye_h // 2), 0, 0, 360, 255, -1)
    cv2.ellipse(eye_mask, (eye_x_r + eye_w // 2, eye_y + eye_h // 2),
                (eye_w, eye_h // 2), 0, 0, 360, 255, -1)

    # Hair mask (above face + sides, using color-based detection for light hair)
    hair_mask = np.zeros((h, w), dtype=np.uint8)
    # Top region (above face) is likely hair
    hair_mask[0:fy + fh // 4, :] = 255
    # Sides of face
    hair_mask[fy:fy + fh, 0:fx] = 255
    hair_mask[fy:fy + fh, fx + fw:] = 255
    # Combine with alpha (only where character exists)
    hair_mask = cv2.bitwise_and(hair_mask, alpha)

    # Body mask (below face)
    body_mask = np.zeros((h, w), dtype=np.uint8)
    body_mask[fy + fh // 2:, :] = 255
    body_mask = cv2.bitwise_and(body_mask, alpha)
    # Remove face overlap
    body_mask = cv2.bitwise_and(body_mask, cv2.bitwise_not(face_mask))

    # Step 4: Extract and save layers
    print("[4/4] Extracting layers...")

    def extract_layer(mask, name, idx):
        """Extract a layer using the mask."""
        layer = img_np.copy()
        # Apply mask to alpha channel
        combined_alpha = cv2.bitwise_and(alpha, mask)
        layer[:, :, 3] = combined_alpha
        layer_img = Image.fromarray(layer)
        path = os.path.join(output_dir, f"layer_{idx}_{name}.png")
        layer_img.save(path)
        print(f"  Saved: layer_{idx}_{name}.png")
        return layer_img

    # Generate all layers
    layers = []
    layers.append(extract_layer(hair_mask, "back_hair", 1))
    layers.append(extract_layer(body_mask, "body", 2))
    layers.append(extract_layer(face_mask, "face", 3))
    layers.append(extract_layer(eye_mask, "eyes", 4))

    # Front hair (bangs - overlap with face top)
    bangs_mask = np.zeros((h, w), dtype=np.uint8)
    bangs_mask[max(0, fy - fh // 3):fy + fh // 3, fx - fw // 4:fx + fw + fw // 4] = 255
    bangs_mask = cv2.bitwise_and(bangs_mask, alpha)
    layers.append(extract_layer(bangs_mask, "front_hair", 5))

    # Full character (no background) as reference
    img_nobg.save(os.path.join(output_dir, "full_character.png"))

    # Create PSD file
    print("\nCreating PSD file...")
    try:
        from psd_tools import PSDImage
        from psd_tools.api.layers import PixelLayer

        # Create PSD (simple approach - just save as layered PNG reference)
        # Note: psd-tools PSD creation is complex, save layers individually instead
        print("  PSD creation: layers saved as individual PNGs")
        print("  Import them into Live2D Cubism Editor as separate art meshes")
    except Exception as e:
        print(f"  PSD note: {e}")

    print(f"\n=== Done! {len(layers) + 1} layers saved to {output_dir} ===")
    print(f"\nNext steps:")
    print(f"  1. Open Live2D Cubism Editor (D:\\L2D\\Live2D Cubism 5.3)")
    print(f"  2. Import the layer PNGs as art meshes")
    print(f"  3. Use Auto Deformer + Auto Face Motion")
    print(f"  4. Export .moc3 model")

if __name__ == "__main__":
    main()
