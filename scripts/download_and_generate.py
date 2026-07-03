"""
Download Qwen-Image-Layered model with auto-retry, then generate layers.
Supports resume from interrupted downloads.
"""
import os
import time
from pathlib import Path
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"  # Use stable downloader

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_RETRIES = 50  # Enough retries to finish the full 56GB download
RETRY_DELAY = 5   # Quick retry

def download_with_retry():
    """Download model with automatic retry on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"\n=== Download attempt {attempt}/{MAX_RETRIES} ===")

            from diffusers import QwenImageLayeredPipeline
            import torch

            print("Downloading/loading model (will resume from where it left off)...")
            try:
                # Try offline first (use cached files)
                pipeline = QwenImageLayeredPipeline.from_pretrained(
                    "Qwen/Qwen-Image-Layered",
                    torch_dtype=torch.float32,
                    device_map="cpu",
                    local_files_only=True,
                )
                print("Loaded from local cache!")
            except Exception:
                # Fall back to download
                pipeline = QwenImageLayeredPipeline.from_pretrained(
                    "Qwen/Qwen-Image-Layered",
                    torch_dtype=torch.float32,
                    device_map="cpu",
                )
            print("Model downloaded successfully!")
            return pipeline

        except Exception as e:
            error_msg = str(e)
            if "peer closed" in error_msg or "timeout" in error_msg or "ConnectionError" in error_msg:
                print(f"  Connection error (will retry in {RETRY_DELAY}s): {error_msg[:100]}")
                time.sleep(RETRY_DELAY)
            else:
                print(f"  Error: {error_msg[:200]}")
                raise

    print("All retries exhausted!")
    return None

def generate_layers(pipeline):
    """Generate character layers."""
    import torch
    from PIL import Image

    # Model needs 30GB+ VRAM, use pure CPU (slow but works)
    print("Using CPU mode (model too large for GPU, will be slow but works)")
    # Already loaded on CPU by default

    image = Image.open(PROJECT_ROOT / "frontend" / "assets" / "avatar.png").convert("RGBA")
    print(f"Input image: {image.size}")

    inputs = {
        "image": image,
        "generator": torch.Generator().manual_seed(42),
        "true_cfg_scale": 4.0,
        "negative_prompt": " ",
        "num_inference_steps": 50,
        "num_images_per_prompt": 1,
        "layers": 6,
        "resolution": 640,
        "cfg_normalize": True,
        "use_en_prompt": True,
    }

    print("Generating 6 layers...")
    with torch.inference_mode():
        output = pipeline(**inputs)
        output_images = output.images[0]

    output_dir = PROJECT_ROOT / "live2d_models" / "generated"
    os.makedirs(output_dir, exist_ok=True)
    for i, img in enumerate(output_images):
        path = output_dir / f"layer_{i}.png"
        img.save(path)
        print(f"  Saved layer_{i}.png ({img.size})")

    print(f"\nDone! {len(output_images)} layers saved to {output_dir}")

if __name__ == "__main__":
    pipeline = download_with_retry()
    if pipeline:
        generate_layers(pipeline)
    else:
        print("Download failed after all retries.")
