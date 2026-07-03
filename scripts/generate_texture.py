"""
Generate White Salary character texture using ComfyUI API.

Takes the original hiyori texture as input, uses img2img to transform
the appearance while keeping the structure (UV mapping) intact.
"""
import json
import urllib.request
import urllib.parse
import time
import uuid
import os
import io
import struct
from pathlib import Path
from PIL import Image

COMFYUI_URL = "http://127.0.0.1:8188"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXTURE_DIR = PROJECT_ROOT / "live2d_models" / "default" / "hiyori_pro_mic.2048"

def queue_prompt(prompt):
    """Send a prompt to ComfyUI and return the prompt_id."""
    data = json.dumps({"prompt": prompt, "client_id": str(uuid.uuid4())}).encode('utf-8')
    req = urllib.request.Request(f"{COMFYUI_URL}/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())["prompt_id"]

def upload_image(filepath, name):
    """Upload an image to ComfyUI."""
    with open(filepath, "rb") as f:
        img_data = f.read()

    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{COMFYUI_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    result = json.loads(resp.read())
    return result.get("name", name)

def get_history(prompt_id):
    """Get the generation result."""
    while True:
        resp = urllib.request.urlopen(f"{COMFYUI_URL}/history/{prompt_id}")
        history = json.loads(resp.read())
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(2)

def get_image(filename, subfolder, folder_type):
    """Download generated image from ComfyUI."""
    params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
    resp = urllib.request.urlopen(f"{COMFYUI_URL}/view?{params}")
    return resp.read()

def create_img2img_workflow(input_image_name, checkpoint, denoise=0.55):
    """Create a simple img2img workflow to transform character appearance."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint}
        },
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": input_image_name}
        },
        "3": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["2", 0], "vae": ["1", 2]}
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "anime character sheet, silver white long hair, gray silver eyes, "
                        "sailor uniform, navy blue bow, pale skin, beautiful anime girl, "
                        "high quality, detailed, clean lines, transparent background",
                "clip": ["1", 1]
            }
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "bad quality, blurry, deformed, extra limbs, watermark, text, "
                        "realistic, 3d, photo",
                "clip": ["1", 1]
            }
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["3", 0],
                "seed": 42,
                "steps": 25,
                "cfg": 7.0,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": denoise
            }
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]}
        },
        "8": {
            "class_type": "SaveImage",
            "inputs": {"images": ["7", 0], "filename_prefix": "white_salary_texture"}
        }
    }

def main():
    print("=== White Salary Texture Generator ===")
    print(f"ComfyUI: {COMFYUI_URL}")

    # Check ComfyUI
    try:
        urllib.request.urlopen(f"{COMFYUI_URL}/system_stats")
    except Exception:
        print("ERROR: ComfyUI is not running!")
        return

    # Find available checkpoint
    resp = urllib.request.urlopen(f"{COMFYUI_URL}/object_info/CheckpointLoaderSimple")
    info = json.loads(resp.read())
    checkpoints = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    print(f"Available checkpoints: {len(checkpoints)}")

    # Must use SDXL model for 2048x2048 textures
    preferred = ["animagine-xl", "NoobAI-XL", "Illustrious-XL", "blue_pencil-XL"]
    checkpoint = None
    for pref in preferred:
        for ckpt in checkpoints:
            if pref.lower() in ckpt.lower():
                checkpoint = ckpt
                break
        if checkpoint:
            break

    if not checkpoint:
        print("ERROR: No SDXL checkpoint found!")
        return

    print(f"Using checkpoint: {checkpoint}")

    # Process both textures
    for tex_name in ["texture_00.png", "texture_01.png"]:
        tex_path = os.path.join(TEXTURE_DIR, tex_name)
        if not os.path.exists(tex_path):
            continue

        print(f"\nProcessing {tex_name}...")

        # Upload original texture
        uploaded_name = upload_image(tex_path, f"input_{tex_name}")
        print(f"  Uploaded as: {uploaded_name}")

        # Create and run workflow
        # Use lower denoise to keep structure, just change colors/style
        workflow = create_img2img_workflow(uploaded_name, checkpoint, denoise=0.45)
        prompt_id = queue_prompt(workflow)
        print(f"  Queued: {prompt_id}")
        print(f"  Generating (this takes ~30 seconds)...")

        # Wait for result
        print("  Waiting for ComfyUI to finish...")
        history = get_history(prompt_id)

        # Check for errors
        status = history.get("status", {})
        if status.get("status_str") == "error":
            msgs = status.get("messages", [])
            print(f"  ERROR from ComfyUI: {msgs}")
            continue

        outputs = history.get("outputs", {})

        # Find the save image node output
        for node_id, output in outputs.items():
            if "images" in output:
                for img_info in output["images"]:
                    img_data = get_image(img_info["filename"], img_info["subfolder"], img_info["type"])

                    # Save generated texture
                    output_path = os.path.join(TEXTURE_DIR, tex_name)
                    with open(output_path, "wb") as f:
                        f.write(img_data)
                    print(f"  Saved: {output_path} ({len(img_data) // 1024}KB)")

                    # CRITICAL: Preserve original alpha channel!
                    # ComfyUI outputs RGB, we need to keep original transparency
                    generated = Image.open(output_path)
                    if generated.size != (2048, 2048):
                        generated = generated.resize((2048, 2048), Image.LANCZOS)
                    generated = generated.convert("RGBA")

                    # Load original texture to get alpha channel
                    backup_path = os.path.join(TEXTURE_DIR, tex_name.replace(".png", ".backup.png"))
                    original = Image.open(backup_path).convert("RGBA")

                    # Merge: use generated RGB + original Alpha
                    r, g, b, _ = generated.split()
                    _, _, _, orig_alpha = original.split()
                    result = Image.merge("RGBA", (r, g, b, orig_alpha))
                    result.save(output_path)
                    print(f"  Merged with original alpha channel (transparency preserved)")

    print("\n=== Done! ===")
    print("Textures have been replaced. Restart White Salary to see the new look.")
    print("To revert: rename texture_XX.backup.png back to texture_XX.png")

if __name__ == "__main__":
    main()
