"""Collect RapidFuzz's dynamic backends without its packaging tests/tool imports."""

# PyInstaller lives in the separately locked builder environment, not the application.
# pyright: reportMissingModuleSource=false
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules(
    "rapidfuzz", filter=lambda name: not name.startswith("rapidfuzz.__pyinstaller")
)
