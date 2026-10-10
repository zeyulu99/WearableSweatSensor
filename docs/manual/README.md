# Quick Start Guide - English (v1.1.0)

`quickstart-en.tex` is the English manual. `assets/` contains genuine screenshots of the v1.1 GUI running with simulated BLE discovery and acquisition. TikZ adds the red callouts without altering the images.

Compile from this directory with:

```powershell
xelatex -interaction=nonstopmode -halt-on-error quickstart-en.tex
xelatex -interaction=nonstopmode -halt-on-error quickstart-en.tex
```

Requires XeLaTeX and common TeX Live packages, including fontspec, TeX Gyre fonts, TikZ, and titlesec.

## Standard-solution source

The primary source is `Sweat Sensor Mannual.docx` **inside the original `WearableSensorBackUp.zip`**, rather than the incomplete low-point paragraph in the loose root-level Word file. The archived document specifies:

- Low point: apply 150 ppm to the sensing membrane and wait for a stable curve.
- High point: apply 2000 ppm to the sensing membrane and wait for a stable curve.
- Start using the sensor after calibration; recorded data is under Data / wearable device.
- Charge the battery before use; hair and membrane pressure can affect results.

The guide paraphrases these instructions and updates launching and data-folder access to the current GUI. Recording stable voltages and the suggested blue/yellow event convention are user-workflow guidance, not automatic software calibration. No chemical identity, application volume, rinsing process, or fixed waiting time has been invented.

The English manual is supplied separately from the earlier Chinese manual. The application, original Word documents, and existing software releases are unchanged.
