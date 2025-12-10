import os
import sys
import zipfile
import subprocess
from pathlib import Path
import asyncio

import Utils
from NetUtils import ClientStatus
from CommonClient import CommonContext, server_loop, gui_enabled

# Import settings safely (works in package & standalone)
try:
    from . import settings
except ImportError:
    import settings


class SHARContext(CommonContext):
    game = "The Simpsons Hit And Run"

    def __init__(self):
        # Minimal constructor, no async tasks
        super().__init__()
        self._initialized = False

    async def initialize(self):
        """Async-safe initialization: load host options, paths, and tools."""
        if self._initialized:
            return
        print("[SHARClient] Initializing SHARContext asynchronously...")

        # Load options from host.yaml
        opts = Utils.get_options()
        self.host_options = opts.get("simpsonshitnrun_options", None)
        print(f"[SHARClient] Loaded host options: {self.host_options}")

        # Default extraction path
        default_extract = (
            Path.home()
            / "Documents"
            / "My Games"
            / "Lucas' Simpsons Hit & Run Mod Launcher"
            / "Saved Games"
            / "APSHARRandomizer"
        )

        # Use host options if set, otherwise default
        ini_path = getattr(self.host_options, "ini_path", "") or ""
        self.EXTRACT_DIR = Path(ini_path) if ini_path else default_extract
        print(f"[SHARClient] Using extraction path: {self.EXTRACT_DIR}")
        self.EXTRACT_DIR.mkdir(parents=True, exist_ok=True)

        # Optional auto-launch executable paths
        self.SHARRandomizerExe = getattr(self.host_options, "sharrandomizer", "")
        self.LucasLauncherExe = getattr(self.host_options, "lucas_launcher", "")
        print(f"[SHARClient] SHARRandomizerExe: {self.SHARRandomizerExe}")
        print(f"[SHARClient] LucasLauncherExe: {self.LucasLauncherExe}")

        self._initialized = True

    ######################################################################
    # ZIP extraction with path-traversal protection
    ######################################################################
    def safe_extract(self, z: zipfile.ZipFile, path: Path):
        print(f"[SHARClient] Extracting ZIP to {path}...")
        for member in z.namelist():
            if member.endswith("SHAR.ini"):  # Only extract SHAR.ini
                dest = path / member
                if not str(dest.resolve()).startswith(str(path.resolve())):
                    raise ValueError("Blocked ZIP path traversal attempt.")
                z.extract(member, path)
                print(f"[SHARClient] Extracted {member}")
        print(f"[SHARClient] Extraction complete.")

    ######################################################################
    # Extract .apshar (should contain SHAR.ini)
    ######################################################################
    async def handle_apshar(self, file_path: Path):
        print(f"[SHARClient] handle_apshar called with: {file_path}")
        try:
            with zipfile.ZipFile(file_path, "r") as z:
                self.safe_extract(z, self.EXTRACT_DIR)

            self.gui_message(f"Extracted SHAR.ini into: {self.EXTRACT_DIR}")

        except Exception as e:
            self.gui_message(f"Error extracting .apshar: {e}")
            print(f"[SHARClient] Exception during extraction: {e}")
            return

        # Launch configured tools after extraction
        self.launch_external_tools()

    ######################################################################
    # Simple cross-platform file opener
    ######################################################################
    def launch_file(self, path: Path):
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    ######################################################################
    # Launch SHARRandomizer.exe / Lucas Launcher if configured
    ######################################################################
    def launch_external_tools(self):
        print("[SHARClient] Attempting to launch external tools...")

        if self.SHARRandomizerExe and Path(self.SHARRandomizerExe).exists():
            self.gui_message(f"Launching SHARRandomizer.exe...")
            print(f"[SHARClient] Launching SHARRandomizer.exe at {self.SHARRandomizerExe}")
            subprocess.Popen([self.SHARRandomizerExe], cwd=Path(self.SHARRandomizerExe).parent)
        else:
            msg = "SHARRandomizer.exe not configured or missing."
            self.gui_message(msg)
            print(f"[SHARClient] {msg}")

        if self.LucasLauncherExe and Path(self.LucasLauncherExe).exists():
            self.gui_message(f"Launching Lucas Mod Launcher.exe...")
            print(f"[SHARClient] Launching Lucas Launcher.exe at {self.LucasLauncherExe}")
            subprocess.Popen([self.LucasLauncherExe], cwd=Path(self.LucasLauncherExe).parent)
        else:
            msg = "Lucas Launcher.exe not configured or missing."
            self.gui_message(msg)
            print(f"[SHARClient] {msg}")

    ######################################################################
    # GUI message override (headless safe)
    ######################################################################
    def gui_message(self, message: str):
        print(f"[SHARClient][GUI] {message}")

    ######################################################################
    # Start Archipelago networking loop OR manual .apshar handling
    ######################################################################
    async def start(self):
        await self.initialize()

        # Check for drag-and-drop / command-line .apshar argument
        if len(sys.argv) > 1:
            file_path = Path(sys.argv[1])
            print(f"[SHARClient] Opening .apshar from argument: {file_path}")
            await self.handle_apshar(file_path)
            return

        # Prompt user to select a .apshar if Tkinter is available
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            file_path = filedialog.askopenfilename(
                title="Select a .apshar patch",
                filetypes=[("APSHAR files", "*.apshar")],
            )
            if file_path:
                await self.handle_apshar(Path(file_path))
                return
            else:
                print("[SHARClient] No .apshar selected. Starting server loop instead.")
        except ImportError:
            print("[SHARClient] Tkinter not available; pass .apshar as command-line argument.")

        # Fallback to normal server loop
        await server_loop(self)


# ---------------- Run Client Helper ----------------
def run_client(ap_url: str = None):
    """Entry point for Archipelago client component."""
    async def runner():
        ctx = SHARContext()
        await ctx.initialize()
        if ap_url:
            await ctx.handle_apshar(Path(ap_url))
        else:
            await ctx.start()

    asyncio.run(runner())
