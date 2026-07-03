"""
White Salary - GPT-SoVITS Voice Training Script

This script automates the full GPT-SoVITS v2 training pipeline:
  1. Slice audio into segments
  2. Denoise (optional, improves quality)
  3. ASR transcription (FunASR)
  4. Feature extraction (BERT + HuBERT + Semantic)
  5. Train SoVITS model
  6. Train GPT model
  7. Auto-update tts_infer.yaml config

Usage:
  cd <your GPT-SoVITS directory>
  call venv_new\Scripts\activate.bat
  python "<project-root>/scripts/train_voice.py"

All paths and parameters follow the exact same logic as GPT-SoVITS webui.py
to ensure compatibility.
"""

import json
import os
import subprocess
import sys
import shutil
from pathlib import Path

# ============================================================
# Configuration
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_sovits_root() -> Path:
    try:
        from resolve_gpt_sovits_dir import resolve_gpt_sovits_dir

        path = resolve_gpt_sovits_dir()
        if path is not None:
            return path
    except Exception:
        pass
    raise RuntimeError(
        "GPT-SoVITS path is not configured. Set external_tools.gpt_sovits_dir "
        "in conf.yaml or WS_GPT_SOVITS_DIR."
    )


def _resolve_ffmpeg_dir() -> str:
    ffmpeg_path = os.environ.get("WS_FFMPEG_PATH", "").strip()
    if ffmpeg_path:
        p = Path(ffmpeg_path)
        return str(p.parent if p.name.lower() == "ffmpeg.exe" else p)
    found = shutil.which("ffmpeg")
    return str(Path(found).parent) if found else ""


TRAIN_NAME = "white_salary_v1"
VERSION = "v2"

# GPT-SoVITS root (configured by WS_GPT_SOVITS_DIR or conf.yaml external_tools.gpt_sovits_dir)
SOVITS_ROOT = _resolve_sovits_root()
INPUT_AUDIO = str(SOVITS_ROOT / "input_audio" / "jiaran.mp3")

# Pretrained model paths (v2)
PRETRAINED_S2G = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth"
PRETRAINED_S2D = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2D2333k.pth"
PRETRAINED_S1 = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
BERT_DIR = "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
CNHUBERT_DIR = "GPT_SoVITS/pretrained_models/chinese-hubert-base"

# Training parameters
SOVITS_EPOCHS = 16
SOVITS_BATCH_SIZE = 16
SOVITS_SAVE_EVERY = 4
GPT_EPOCHS = 20
GPT_BATCH_SIZE = 8
GPT_SAVE_EVERY = 5

# Slice parameters (same defaults as webui)
SLICE_THRESHOLD = -34
SLICE_MIN_LENGTH = 4000
SLICE_MIN_INTERVAL = 300
SLICE_HOP_SIZE = 10
SLICE_MAX_SIL_KEPT = 500
SLICE_MAX = 0.9
SLICE_ALPHA = 0.25

# Directories
EXP_ROOT = "logs"
EXP_DIR = f"{EXP_ROOT}/{TRAIN_NAME}"
SOVITS_WEIGHT_DIR = "SoVITS_weights_v2"
GPT_WEIGHT_DIR = "GPT_weights_v2"
TMP_DIR = "TEMP"

FFMPEG_DIR = _resolve_ffmpeg_dir()

PYTHON = sys.executable


def run_cmd(cmd, desc="", env=None):
    """Run a command and check for errors."""
    print(f"  CMD: {cmd}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(cmd, shell=True, env=merged_env)
    if result.returncode != 0:
        print(f"  [ERROR] {desc} failed with code {result.returncode}")
        return False
    return True


def step1_slice():
    """Step 1: Slice audio into segments using positional args."""
    print("=" * 60)
    print("[1/7] Slicing audio...")
    print("=" * 60)

    opt_root = f"{EXP_DIR}/slicer_opt"
    os.makedirs(opt_root, exist_ok=True)

    # slice_audio.py uses positional args:
    # inp, opt_root, threshold, min_length, min_interval,
    # hop_size, max_sil_kept, _max, alpha, i_part, all_part
    cmd = (
        f'"{PYTHON}" tools/slice_audio.py '
        f'"{INPUT_AUDIO}" "{opt_root}" '
        f'{SLICE_THRESHOLD} {SLICE_MIN_LENGTH} {SLICE_MIN_INTERVAL} '
        f'{SLICE_HOP_SIZE} {SLICE_MAX_SIL_KEPT} {SLICE_MAX} {SLICE_ALPHA} '
        f'0 1'
    )
    if not run_cmd(cmd, "Slice audio"):
        return False

    # Verify output
    wav_files = list(Path(opt_root).glob("*.wav"))
    print(f"  Sliced into {len(wav_files)} segments")
    if len(wav_files) == 0:
        print("  [ERROR] No segments produced!")
        return False
    return True


def step2_denoise():
    """Step 2: Denoise audio segments."""
    print()
    print("=" * 60)
    print("[2/7] Denoising audio...")
    print("=" * 60)

    input_dir = f"{EXP_DIR}/slicer_opt"
    output_dir = f"{EXP_DIR}/denoise_opt"
    os.makedirs(output_dir, exist_ok=True)

    # cmd-denoise.py uses argparse: -i input -o output
    cmd = (
        f'"{PYTHON}" tools/cmd-denoise.py '
        f'-i "{input_dir}" -o "{output_dir}"'
    )
    if not run_cmd(cmd, "Denoise"):
        print("  Denoise failed (non-critical), using raw slices.")
        return input_dir

    wav_files = list(Path(output_dir).glob("*.wav"))
    if len(wav_files) == 0:
        print("  Denoise produced no output, using raw slices.")
        return input_dir

    print(f"  Denoised {len(wav_files)} files")
    return output_dir


def step3_asr(inp_wav_dir):
    """Step 3: ASR transcription. Try FunASR first, then Faster-Whisper."""
    print()
    print("=" * 60)
    print("[3/7] Running ASR transcription...")
    print("=" * 60)

    # Use Faster-Whisper directly (more reliable for Chinese)
    # It tries FunASR internally first, then falls back to Whisper segments
    print("  Using Faster-Whisper (with FunASR fallback)...")
    # Delete old list files first
    for f in Path(EXP_DIR).glob("*.list"):
        f.unlink()

    cmd = (
        f'"{PYTHON}" tools/asr/fasterwhisper_asr.py '
        f'-i "{inp_wav_dir}" -o "{EXP_DIR}" '
        f'-l zh -s large-v3'
    )
    run_cmd(cmd, "Faster-Whisper")

    list_file = _find_valid_list_file()
    if list_file:
        print(f"  Faster-Whisper OK: {list_file}")
        return list_file

    print("  [ERROR] All ASR methods failed to produce text!")
    return None


def _find_valid_list_file():
    """Find and validate the ASR output .list file."""
    list_files = list(Path(EXP_DIR).glob("*.list"))
    if not list_files:
        return None

    list_file = max(list_files, key=lambda f: f.stat().st_mtime)

    with open(list_file, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f.readlines() if l.strip()]

    if not lines:
        return None

    # Check that at least some lines have non-empty text
    # Format: path|speaker|LANG|text
    valid_count = 0
    for line in lines:
        parts = line.split("|")
        if len(parts) >= 4 and parts[3].strip():
            valid_count += 1

    print(f"  Found {len(lines)} lines, {valid_count} with text")
    if valid_count == 0:
        return None

    return str(list_file)


def step4_features(inp_text, inp_wav_dir):
    """Step 4: Extract features (BERT + HuBERT + Semantic) using env vars."""
    print()
    print("=" * 60)
    print("[4/7] Extracting features (BERT + HuBERT + Semantic)...")
    print("=" * 60)

    opt_dir = EXP_DIR
    os.makedirs(f"{opt_dir}/3-bert", exist_ok=True)

    # All three scripts use environment variables, not CLI args
    base_env = {
        "inp_text": inp_text,
        "inp_wav_dir": inp_wav_dir,
        "exp_name": TRAIN_NAME,
        "opt_dir": opt_dir,
        "i_part": "0",
        "all_parts": "1",
        "_CUDA_VISIBLE_DEVICES": "0",
        "is_half": "True",
    }

    # 4a: BERT text features (1-get-text.py)
    print("  [4a] Extracting BERT text features...")
    env_1 = {**base_env, "bert_pretrained_dir": BERT_DIR}
    cmd = f'"{PYTHON}" GPT_SoVITS/prepare_datasets/1-get-text.py'
    if not run_cmd(cmd, "BERT features", env=env_1):
        return False

    # Merge name2text parts into single file (same as webui.py)
    final_path = f"{opt_dir}/2-name2text.txt"
    all_text = []
    for part_file in sorted(Path(opt_dir).glob("2-name2text-*.txt")):
        with open(part_file, "r", encoding="utf-8") as f:
            content = f.read().strip("\n")
            if content:
                all_text.extend(content.split("\n"))
        part_file.unlink()
    with open(final_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_text) + "\n")
    print(f"  [4a] OK ({len(all_text)} entries in 2-name2text.txt)")
    if len(all_text) == 0:
        print("  [ERROR] 2-name2text.txt is empty! ASR text may be missing.")
        return False

    # 4b: HuBERT audio features (2-get-hubert-wav32k.py)
    print("  [4b] Extracting HuBERT audio features...")
    env_2 = {
        **base_env,
        "cnhubert_base_dir": CNHUBERT_DIR,
        "sv_path": "",
    }
    cmd = f'"{PYTHON}" GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py'
    if not run_cmd(cmd, "HuBERT features", env=env_2):
        return False
    print("  [4b] OK")

    # 4c: Semantic tokens (3-get-semantic.py)
    print("  [4c] Extracting semantic tokens...")
    env_3 = {
        **base_env,
        "pretrained_s2G": PRETRAINED_S2G,
        "s2config_path": "GPT_SoVITS/configs/s2.json",
    }
    cmd = f'"{PYTHON}" GPT_SoVITS/prepare_datasets/3-get-semantic.py'
    if not run_cmd(cmd, "Semantic tokens", env=env_3):
        return False

    # Merge 6-name2semantic parts into single file (same as webui.py)
    sem_final = f"{opt_dir}/6-name2semantic.tsv"
    all_sem = []
    for part_file in sorted(Path(opt_dir).glob("6-name2semantic-*.tsv")):
        with open(part_file, "r", encoding="utf-8") as f:
            content = f.read().strip("\n")
            if content:
                all_sem.extend(content.split("\n"))
        part_file.unlink()
    with open(sem_final, "w", encoding="utf-8") as f:
        f.write("\n".join(all_sem) + "\n")
    print(f"  [4c] OK ({len(all_sem)} entries in 6-name2semantic.tsv)")

    return True


def step5_train_sovits():
    """Step 5: Train SoVITS model using JSON config (same as webui)."""
    print()
    print("=" * 60)
    print(f"[5/7] Training SoVITS model ({SOVITS_EPOCHS} epochs)...")
    print("=" * 60)

    s2_dir = EXP_DIR
    os.makedirs(f"{s2_dir}/logs_s2_{VERSION}", exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    # Load the base s2 config template
    with open("GPT_SoVITS/configs/s2.json", "r") as f:
        data = json.loads(f.read())

    # Override with our training params (same as webui.py open1Ba)
    data["train"]["batch_size"] = SOVITS_BATCH_SIZE
    data["train"]["epochs"] = SOVITS_EPOCHS
    data["train"]["text_low_lr_rate"] = 0.4
    data["train"]["pretrained_s2G"] = PRETRAINED_S2G
    data["train"]["pretrained_s2D"] = PRETRAINED_S2D
    data["train"]["if_save_latest"] = True
    data["train"]["if_save_every_weights"] = True
    data["train"]["save_every_epoch"] = SOVITS_SAVE_EVERY
    data["train"]["gpu_numbers"] = "0"
    data["train"]["grad_ckpt"] = False
    data["model"]["version"] = VERSION
    data["data"]["exp_dir"] = s2_dir
    data["s2_ckpt_dir"] = s2_dir
    data["save_weight_dir"] = SOVITS_WEIGHT_DIR
    data["name"] = TRAIN_NAME
    data["version"] = VERSION

    # Write temp config
    tmp_config = f"{TMP_DIR}/tmp_s2.json"
    with open(tmp_config, "w") as f:
        f.write(json.dumps(data, indent=2))

    print(f"  Config: {tmp_config}")
    cmd = f'"{PYTHON}" GPT_SoVITS/s2_train.py --config "{tmp_config}"'
    if not run_cmd(cmd, "SoVITS training"):
        return False

    print("  SoVITS training complete!")
    return True


def step6_train_gpt():
    """Step 6: Train GPT model using YAML config (same as webui)."""
    print()
    print("=" * 60)
    print(f"[6/7] Training GPT model ({GPT_EPOCHS} epochs)...")
    print("=" * 60)

    s1_dir = EXP_DIR
    os.makedirs(f"{s1_dir}/logs_s1_{VERSION}", exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)

    # We need PyYAML
    import yaml

    # Load the base s1 config template (v2)
    config_template = "GPT_SoVITS/configs/s1longer-v2.yaml"
    with open(config_template, "r") as f:
        data = yaml.load(f, Loader=yaml.FullLoader)

    # Override with our training params (same as webui.py open1Bb)
    data["train"]["batch_size"] = GPT_BATCH_SIZE
    data["train"]["epochs"] = GPT_EPOCHS
    data["train"]["save_every_n_epoch"] = GPT_SAVE_EVERY
    data["train"]["if_save_every_weights"] = True
    data["train"]["if_save_latest"] = True
    data["train"]["if_dpo"] = False
    data["train"]["half_weights_save_dir"] = GPT_WEIGHT_DIR
    data["train"]["exp_name"] = TRAIN_NAME
    data["pretrained_s1"] = PRETRAINED_S1
    data["train_semantic_path"] = f"{s1_dir}/6-name2semantic.tsv"
    data["train_phoneme_path"] = f"{s1_dir}/2-name2text.txt"
    data["output_dir"] = f"{s1_dir}/logs_s1_{VERSION}"

    # Write temp config
    tmp_config = f"{TMP_DIR}/tmp_s1.yaml"
    with open(tmp_config, "w") as f:
        f.write(yaml.dump(data, default_flow_style=False))

    print(f"  Config: {tmp_config}")

    # Set env vars for GPU
    os.environ["_CUDA_VISIBLE_DEVICES"] = "0"
    os.environ["hz"] = "25hz"

    cmd = f'"{PYTHON}" GPT_SoVITS/s1_train.py --config_file "{tmp_config}"'
    if not run_cmd(cmd, "GPT training"):
        return False

    print("  GPT training complete!")
    return True


def step7_update_config():
    """Step 7: Find latest model weights and update tts_infer.yaml."""
    print()
    print("=" * 60)
    print("[7/7] Updating TTS config...")
    print("=" * 60)

    # Find latest model files
    sovits_dir = Path(SOVITS_WEIGHT_DIR)
    gpt_dir = Path(GPT_WEIGHT_DIR)

    latest_sovits = None
    latest_gpt = None

    if sovits_dir.exists():
        sovits_files = sorted(
            sovits_dir.glob(f"{TRAIN_NAME}*"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if sovits_files:
            latest_sovits = str(sovits_files[0])

    if gpt_dir.exists():
        gpt_files = sorted(
            gpt_dir.glob(f"{TRAIN_NAME}*"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if gpt_files:
            latest_gpt = str(gpt_files[0])

    if not latest_sovits:
        print("  [WARNING] No SoVITS model found, using pretrained.")
        latest_sovits = PRETRAINED_S2G
    if not latest_gpt:
        print("  [WARNING] No GPT model found, using pretrained.")
        latest_gpt = PRETRAINED_S1

    print(f"  GPT:    {latest_gpt}")
    print(f"  SoVITS: {latest_sovits}")

    # Build absolute paths
    sovits_root = str(SOVITS_ROOT).replace("\\", "/")
    gpt_abs = f"{sovits_root}/{latest_gpt}".replace("\\", "/")
    sovits_abs = f"{sovits_root}/{latest_sovits}".replace("\\", "/")

    # Write new tts_infer.yaml
    config_content = f"""custom:
  bert_base_path: {sovits_root}/GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  cnhuhbert_base_path: {sovits_root}/GPT_SoVITS/pretrained_models/chinese-hubert-base
  device: cuda
  is_half: true
  t2s_weights_path: {gpt_abs}
  version: v2
  vits_weights_path: {sovits_abs}
"""

    config_path = Path("GPT_SoVITS/configs/tts_infer.yaml")
    backup_path = Path("GPT_SoVITS/configs/tts_infer.yaml.backup")

    # Backup old config
    if config_path.exists():
        shutil.copy2(config_path, backup_path)
        print("  Old config backed up.")

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(config_content)
    print("  tts_infer.yaml updated!")

    # Save reference audio dir for White Salary
    ref_dir = str(SOVITS_ROOT / "output" / TRAIN_NAME / "denoise_opt").replace("/", "\\")
    ref_file = PROJECT_ROOT / "data" / "tts_ref_audio_dir.txt"
    ref_file.parent.mkdir(parents=True, exist_ok=True)
    with open(ref_file, "w") as f:
        f.write(ref_dir)
    print(f"  Reference audio dir saved: {ref_dir}")

    return True


def main():
    print("=" * 60)
    print("  White Salary - Auto Voice Training (GPT-SoVITS v2)")
    print(f"  Model: {TRAIN_NAME}")
    print(f"  Input: {INPUT_AUDIO}")
    print("=" * 60)
    print()

    # Must run from GPT-SoVITS root
    os.chdir(SOVITS_ROOT)
    print(f"Working directory: {os.getcwd()}")

    # Add ffmpeg to PATH
    if os.path.isdir(FFMPEG_DIR):
        os.environ["PATH"] = FFMPEG_DIR + ";" + os.environ.get("PATH", "")
        print(f"ffmpeg: {FFMPEG_DIR}")
    else:
        print(f"[WARNING] ffmpeg dir not found: {FFMPEG_DIR}")
    print()

    # Verify input audio exists
    if not os.path.exists(INPUT_AUDIO):
        print(f"[ERROR] Input audio not found: {INPUT_AUDIO}")
        sys.exit(1)

    # Verify pretrained models exist
    for name, path in [
        ("Pretrained S2G", PRETRAINED_S2G),
        ("Pretrained S2D", PRETRAINED_S2D),
        ("Pretrained S1", PRETRAINED_S1),
        ("BERT model", BERT_DIR),
        ("HuBERT model", CNHUBERT_DIR),
    ]:
        if not os.path.exists(path):
            print(f"[ERROR] {name} not found: {path}")
            sys.exit(1)
    print("All pretrained models verified.\n")

    # Check if we can resume from previous run
    resume = "--resume" in sys.argv
    slicer_done = len(list(Path(f"{EXP_DIR}/slicer_opt").glob("*.wav"))) > 0 if Path(f"{EXP_DIR}/slicer_opt").exists() else False
    list_valid = _find_valid_list_file() if Path(EXP_DIR).exists() else None
    name2text_valid = Path(f"{EXP_DIR}/2-name2text.txt").exists() and Path(f"{EXP_DIR}/2-name2text.txt").stat().st_size > 10
    semantic_valid = Path(f"{EXP_DIR}/6-name2semantic.tsv").exists() and Path(f"{EXP_DIR}/6-name2semantic.tsv").stat().st_size > 10

    if resume and slicer_done:
        print("[RESUME] Skipping Step 1 (slicing already done)")
    else:
        # Clean old data for fresh start
        if Path(EXP_DIR).exists() and not resume:
            print("Cleaning old training data...")
            shutil.rmtree(EXP_DIR, ignore_errors=True)
        if not step1_slice():
            print("\n[FAILED] Step 1: Slice audio")
            sys.exit(1)

    # Step 2: Denoise
    if resume and slicer_done:
        # Use slicer_opt as fallback
        wav_dir = f"{EXP_DIR}/slicer_opt"
        print("[RESUME] Skipping Step 2 (using existing audio)")
    else:
        wav_dir = step2_denoise()

    # Step 3: ASR
    if resume and list_valid:
        transcript = list_valid
        print(f"[RESUME] Skipping Step 3 (using existing transcript: {transcript})")
    else:
        transcript = step3_asr(wav_dir)
        if not transcript:
            print("\n[FAILED] Step 3: ASR transcription")
            sys.exit(1)

    # Step 4: Features
    if resume and name2text_valid and semantic_valid:
        print("[RESUME] Skipping Step 4 (features already extracted)")
    else:
        if not step4_features(transcript, wav_dir):
            print("\n[FAILED] Step 4: Feature extraction")
            sys.exit(1)

    # Step 5: Train SoVITS
    if not step5_train_sovits():
        print("\n[FAILED] Step 5: SoVITS training")
        sys.exit(1)

    # Step 6: Train GPT
    if not step6_train_gpt():
        print("\n[FAILED] Step 6: GPT training")
        sys.exit(1)

    # Step 7: Update config
    if not step7_update_config():
        print("\n[FAILED] Step 7: Config update")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  Training Complete!")
    print("=" * 60)
    print()
    print("  Next steps:")
    print(f"    1. Start TTS: {PROJECT_ROOT / 'Start-TTS-Local.bat'}")
    print(f"    2. Start all: {PROJECT_ROOT / 'Start.bat'}")
    print()


if __name__ == "__main__":
    main()
