# iVCS / iVSA Control Panel
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Python: 3.14+](https://img.shields.io/badge/Python-3.14-blue.svg)
![Platform: Windows-blue](https://img.shields.io/badge/Platform-Windows-blue.svg)

A Python desktop application for connecting to and configuring **Isomet iVCS / iVSA synthesiser systems** using the `imslib` Python API. The main program, `VCO_contol_Panel.py`, provides a graphical control panel for device discovery, device monitoring, RF and amplifier configuration, filter and gain control, compensation management, and startup-state storage.

This repository also includes the files required to build a standalone Windows executable, together with version metadata, application assets, help content, and a GitHub Actions workflow for automated builds.

---

## Features

`VCO_contol_Panel.py` is the core application in this repository and is designed for working with supported iVCS / iVSA hardware. The application includes the following capabilities:

* Device selection dialog for discovering and connecting to supported systems
* Main control interface for configuring device settings and storing startup state to non-volatile memory
* Monitoring tab for viewing device analogue-to-digital converter readings
* Compensation tab for loading, viewing, downloading, and storing LUT-based compensation data
* Advanced tab for filter selection and digital gain control
* About tab for firmware and software version information
* Packaged Windows build support using PyInstaller, including icon, splash screen, and version metadata

The application is intended as an operator-facing control panel for testing and configuring supported Isomet synthesiser systems from a desktop PC without needing to work directly with lower-level scripts.

---

## Repository Contents

The repository contains the following key files and folders:

```text
.github/
  workflows/
    build_windows.yml
build_local.bat
Changelog.md
help/
LICENSE
README.md
requirements.txt
Isomet.ico
Splash.jpg
VCO_contol_Panel.py
version_info.txt
iVCS-iVSA_control_Panel.spec
```

### Key Files

* `VCO_contol_Panel.py` — main application source file and primary entry point
* `iVCS-iVSA_control_Panel.spec` — PyInstaller spec file for building the Windows executable
* `build_local.bat` — local Windows build script
* `version_info.txt` — version metadata used during packaging
* `Isomet.ico` — application icon
* `Splash.jpg` — splash image used in packaged builds
* `.github/workflows/build_windows.yml` — GitHub Actions workflow for automated Windows builds
* `requirements.txt` — Python dependency list for local setup
* `help/` — help content bundled with the application
* `Changelog.md` — project change history
* `LICENSE` — repository license file

---

## Dependencies

The main application is a Python GUI program and depends on the following packages:

```bash
pip install -r requirements.txt
```

Main dependencies include:

* `PySide6`
* `imslib`
* `matplotlib`
* `pyinstaller`

`pyinstaller` is required for building the standalone executable, but not for running the Python source directly during development.

---

## External Code References

This project includes code derived from example and helper utilities provided in the [Isomet imslib-python repository](https://github.com/Isomet-Corporation/imslib-python).

Included modules:

* `ims_events.py`
* `ims_scan.py`

These are used to support device scanning and event-driven SDK workflows.

---

## Precompiled Releases

Precompiled Windows executables are intended to be distributed through the repository’s GitHub Releases page.

```text
iVCS-iVSA_control_Panel.exe
```

---

## Building the EXE Locally

This repository includes `build_local.bat` for building the application locally on Windows.

### Option 1 — Use the Batch Script

From the repository root:

```bat
build_local.bat
```

This is the simplest way to build the packaged executable using the repository’s existing build setup.

### Option 2 — Build with PyInstaller Directly

The repository also includes the spec file:

```text
iVCS-iVSA_control_Panel.spec
```

You can build from that with:

```bash
pyinstaller iVCS-iVSA_control_Panel.spec
```

### Typical Manual Build Environment Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pyinstaller iVCS-iVSA_control_Panel.spec
```

### Build Assets Used

The repository includes the following packaging assets used during build and distribution:

* `Isomet.ico`
* `Splash.jpg`
* `version_info.txt`
* `iVCS-iVSA_control_Panel.spec`

---

## Running the Application

### Run from Source

From the repository root:

```bash
python VCO_contol_Panel.py
```

This is the standard way to launch the application during development.

### Run the Packaged Executable

After building with PyInstaller, run the generated `.exe` from the output folder produced by the build process.

---

## Typical Workflow

A typical user workflow for the application is:

1. Launch `VCO_contol_Panel.py` or the packaged executable.
2. Select and connect to a supported system.
3. Configure the required VCO and RF drive settings.
4. Review monitoring information and adjust advanced settings if needed.
5. Load, inspect, download, or store compensation data as required.
6. Store the desired startup state to non-volatile memory.

---

## Notes

* The main focus of the repository is `VCO_contol_Panel.py`.
* The repository is structured for Windows desktop use.
* The GitHub Actions workflow at `.github/workflows/build_windows.yml` is used to automatically generate a precompiled `.exe` for releases.
* Connected systems are filtered to `iVCS` models during device discovery.

---

## Changelog

The repository includes `Changelog.md` for tracking release history and project updates.

---

## License

This repository includes a `LICENSE` file.

At the time of inspection, the project is licensed under the **MIT License**.
