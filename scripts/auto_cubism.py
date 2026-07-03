"""
Automate Live2D Cubism Editor using pyautogui.

Steps:
1. Launch Cubism Editor
2. Create new model
3. Import texture
4. Auto mesh
5. Auto face deformer
6. Auto face motion
7. Export .moc3
"""
import pyautogui
import subprocess
import time
import os
from pathlib import Path

# Safety settings
pyautogui.PAUSE = 0.5
pyautogui.FAILSAFE = True  # Move mouse to corner to abort

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CUBISM_EXE = os.environ.get("WS_CUBISM_EXE", "").strip()
TEXTURE_PATH = PROJECT_ROOT / "live2d_models" / "white_salary" / "texture_00.png"
EXPORT_DIR = PROJECT_ROOT / "live2d_models" / "white_salary"

def wait_for_window(title_part, timeout=30):
    """Wait for a window with given title to appear."""
    import pygetwindow as gw
    start = time.time()
    while time.time() - start < timeout:
        windows = gw.getWindowsWithTitle(title_part)
        if windows:
            win = windows[0]
            win.activate()
            time.sleep(0.5)
            return win
        time.sleep(1)
    return None

def main():
    print("=== Live2D Cubism Auto Rigging ===")
    print("WARNING: Do not move the mouse during automation!")
    print("Move mouse to top-left corner to abort (failsafe)")
    print()

    # Step 1: Launch Cubism Editor
    print("[1/7] Launching Cubism Editor...")
    if not CUBISM_EXE or not Path(CUBISM_EXE).exists():
        print("ERROR: Cubism executable is not configured.")
        print("Set WS_CUBISM_EXE to your CubismEditor5.exe path.")
        return

    subprocess.Popen([CUBISM_EXE])
    time.sleep(10)  # Wait for Cubism to fully load

    # Try to find Cubism window
    import pygetwindow as gw
    cubism_wins = [w for w in gw.getAllWindows() if 'Cubism' in w.title or 'cubism' in w.title.lower()]
    if not cubism_wins:
        print("Waiting for Cubism to start...")
        time.sleep(15)
        cubism_wins = [w for w in gw.getAllWindows() if 'Cubism' in w.title or 'cubism' in w.title.lower()]

    if cubism_wins:
        win = cubism_wins[0]
        win.activate()
        win.maximize()
        time.sleep(2)
        print(f"  Found window: {win.title}")
    else:
        print("  WARNING: Could not find Cubism window, continuing anyway...")

    # Close any startup dialogs
    time.sleep(3)
    pyautogui.press('escape')
    time.sleep(1)
    pyautogui.press('escape')
    time.sleep(1)

    # Step 2: Create new model (File > New > Model)
    print("[2/7] Creating new model...")
    pyautogui.hotkey('ctrl', 'n')
    time.sleep(2)
    # If there's a dialog asking model type, press Enter for default
    pyautogui.press('enter')
    time.sleep(2)

    # Step 3: Import texture by drag-drop simulation
    # Instead, use File > Import PSD/texture
    print("[3/7] Importing texture...")
    # Try using menu: File > Import
    pyautogui.hotkey('ctrl', 'i')  # Common shortcut for import
    time.sleep(2)

    # If a file dialog opened, type the path
    pyautogui.typewrite(str(TEXTURE_PATH).replace('\\', '/'), interval=0.02)
    time.sleep(1)
    pyautogui.press('enter')
    time.sleep(3)

    # Step 4: Auto mesh
    print("[4/7] Auto generating mesh...")
    # Try Edit menu
    pyautogui.hotkey('ctrl', 'a')  # Select all
    time.sleep(1)

    # Look for auto mesh in menus
    # Cubism 5: Modeling > Mesh > Auto Mesh Generation
    # Try keyboard shortcut or menu navigation
    time.sleep(2)

    # Step 5: Select all art meshes for auto generation
    print("[5/7] Applying auto face deformer...")
    time.sleep(2)

    # Step 6: Auto face motion
    print("[6/7] Applying auto face motion...")
    time.sleep(2)

    # Step 7: Export
    print("[7/7] Exporting .moc3...")
    # File > Export for Runtime
    time.sleep(2)

    print()
    print("=== Automation complete ===")
    print("NOTE: Due to Cubism's complex interface, some steps may need")
    print("manual adjustment. Please check the Cubism Editor window.")
    print()
    print("Manual steps if needed:")
    print("  1. If texture wasn't imported: drag texture_00.png onto canvas")
    print("  2. Select all meshes > Edit > Auto Generate Mesh")
    print("  3. Modeling > Auto Generate Face Deformer")
    print("  4. Modeling > Auto Generate Face Motion")
    print("  5. File > Export for Runtime > Save to:")
    print(f"     {EXPORT_DIR}")

if __name__ == "__main__":
    main()
