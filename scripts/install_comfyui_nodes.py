"""
Meshwright ComfyUI Custom Node Installer — Geekatplay Studio
Installs the Geekatplay-3D-MeshFix custom node package into ComfyUI's custom_nodes folder.
"""
import argparse
import json
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE_SOURCE_DIR = os.path.join(REPO_ROOT, "comfyui_nodes", "Geekatplay-3D-MeshFix")
FOLDER_NAME = "Geekatplay-3D-MeshFix"


def detect_comfyui_installations() -> list[str]:
    """Finds common ComfyUI install directories on Windows."""
    candidates = []

    # Environment variable
    env_path = os.environ.get("COMFYUI_PATH")
    if env_path and os.path.isdir(env_path):
        candidates.append(os.path.abspath(env_path))

    # Common Windows locations
    user_home = os.path.expanduser("~")
    local_appdata = os.environ.get("LOCALAPPDATA", "")

    search_roots = [
        user_home,
        local_appdata,
        "C:\\",
        "D:\\",
        "E:\\",
        os.path.join(user_home, "Desktop"),
        os.path.join(user_home, "Downloads"),
    ]

    folder_patterns = [
        "ComfyUI",
        "ComfyUI_windows_portable",
        "ComfyUI_windows_portable\\ComfyUI",
        "comfyui",
    ]

    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in folder_patterns:
            target = os.path.abspath(os.path.join(root, pat))
            if os.path.isdir(target):
                # Check for standard ComfyUI signature (main.py, custom_nodes, or execution.py)
                has_marker = (
                    os.path.isdir(os.path.join(target, "custom_nodes")) or
                    os.path.isfile(os.path.join(target, "main.py")) or
                    os.path.isdir(os.path.join(target, "ComfyUI", "custom_nodes"))
                )
                if has_marker and target not in candidates:
                    candidates.append(target)

    return candidates


def resolve_custom_nodes_dir(path: str) -> str:
    """
    Given any ComfyUI folder or custom_nodes path, resolves the exact custom_nodes directory.
    """
    cleaned = os.path.abspath(path.strip().strip('"').strip("'"))

    if os.path.basename(cleaned).lower() == FOLDER_NAME.lower():
        # User pointed directly to the node folder
        return os.path.dirname(cleaned)

    if os.path.basename(cleaned).lower() == "custom_nodes":
        return cleaned

    # Check subdirectories
    sub1 = os.path.join(cleaned, "custom_nodes")
    if os.path.isdir(sub1):
        return sub1

    sub2 = os.path.join(cleaned, "ComfyUI", "custom_nodes")
    if os.path.isdir(sub2):
        return sub2

    # If neither exists but path looks like ComfyUI root, suggest creating custom_nodes there
    return os.path.join(cleaned, "custom_nodes")


def install_nodes(target_path: str) -> dict:
    """
    Installs the Geekatplay-3D-MeshFix custom node package to target ComfyUI path.
    """
    custom_nodes_dir = resolve_custom_nodes_dir(target_path)
    os.makedirs(custom_nodes_dir, exist_ok=True)

    dest_folder = os.path.join(custom_nodes_dir, FOLDER_NAME)
    os.makedirs(dest_folder, exist_ok=True)

    if not os.path.isdir(NODE_SOURCE_DIR):
        return {
            "success": False,
            "error": f"Node source directory not found: {NODE_SOURCE_DIR}"
        }

    # Copy files
    for root, dirs, files in os.walk(NODE_SOURCE_DIR):
        rel_path = os.path.relpath(root, NODE_SOURCE_DIR)
        dest_root = os.path.join(dest_folder, rel_path) if rel_path != "." else dest_folder
        os.makedirs(dest_root, exist_ok=True)

        for file in files:
            src_file = os.path.join(root, file)
            dst_file = os.path.join(dest_root, file)
            shutil.copy2(src_file, dst_file)

    # Generate meshwright_config.json linking back to this app
    venv_dir = os.path.join(REPO_ROOT, ".venv")
    cfg = {
        "meshwright_root": REPO_ROOT.replace("\\", "/"),
        "meshwright_venv": venv_dir.replace("\\", "/") if os.path.isdir(venv_dir) else ""
    }
    cfg_file = os.path.join(dest_folder, "meshwright_config.json")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

    sample_workflow = os.path.join(dest_folder, "examples", "mesh_fix_workflow.json")

    return {
        "success": True,
        "destination": dest_folder,
        "custom_nodes_dir": custom_nodes_dir,
        "sample_workflow": sample_workflow
    }


def main():
    parser = argparse.ArgumentParser(description="Install Meshwright custom nodes for ComfyUI.")
    parser.add_argument("--comfy-path", type=str, default="", help="Path to ComfyUI installation or custom_nodes folder")
    parser.add_argument("--check", action="store_true", help="Only detect ComfyUI installations and report")
    args = parser.parse_args()

    print("=" * 60)
    print("  Meshwright ComfyUI Custom Node Installer — Geekatplay")
    print("=" * 60)

    found = detect_comfyui_installations()

    if args.check:
        print("\nDetected ComfyUI installations:")
        if not found:
            print("  None detected automatically.")
        else:
            for p in found:
                print(f"  * {p}")
        return

    chosen_path = args.comfy_path.strip()

    if not chosen_path:
        if found:
            print("\nFound ComfyUI installations on this computer:")
            for idx, p in enumerate(found, 1):
                print(f"  [{idx}] {p}")
            print(f"  [{len(found) + 1}] Enter custom path...")
            print("  [0] Cancel")

            choice = input(f"\nSelect an option [1-{len(found) + 1}] (default 1): ").strip()
            if not choice:
                choice = "1"

            if choice == "0":
                print("Installation canceled.")
                return

            try:
                num = int(choice)
                if 1 <= num <= len(found):
                    chosen_path = found[num - 1]
                else:
                    chosen_path = input("Enter path to your ComfyUI or custom_nodes folder: ").strip()
            except ValueError:
                chosen_path = choice
        else:
            chosen_path = input("\nEnter path to your ComfyUI or custom_nodes folder: ").strip()

    if not chosen_path:
        print("No path provided. Installation aborted.")
        sys.exit(1)

    print(f"\nInstalling custom nodes to: {chosen_path} ...")
    res = install_nodes(chosen_path)

    if res["success"]:
        print("\n" + "=" * 60)
        print("  INSTALLATION SUCCESSFUL!")
        print("=" * 60)
        print(f"Custom nodes folder: {res['destination']}")
        print(f"Sample workflow:     {res['sample_workflow']}")
        print("\nIn ComfyUI:")
        print("1. Restart ComfyUI to load the nodes.")
        print("2. Drag & drop 'mesh_fix_workflow.json' into ComfyUI to run the sample 3D repair workflow.")
        print("=" * 60)
    else:
        print(f"\nError during installation: {res.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
