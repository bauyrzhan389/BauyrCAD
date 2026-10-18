# BauyrCAD - working agreement for Claude

BauyrCAD is Bauyrzhan's own CAD + FEA application (SolidWorks-style modelling, linear static
FEA). Bauyrzhan is a mechanical engineer fluent in SolidWorks and MSC Nastran/Patran. When asked for
a 3D model or an analysis, **build it in BauyrCAD**, and grow the application whenever it's
missing something that the job needs. The goal is an app that gets more capable and more
convenient for both of us with every project.

## Environment
- Python **3.11** venv at `.venv` (the system default Python 3.14 has no OpenCASCADE wheels).
  Always use `.\.venv\Scripts\python.exe`. If `.venv` is missing:
  `py -3.11 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e .[dev]`
- Windows. The Bash tool is Git Bash, so quote paths with spaces.
- Stack: OpenCASCADE (OCP 7.9.3, pinned), Gmsh, NumPy/SciPy/pyamg, PySide6 + VTK/pyvista.

## Doing a CAD/FEA project for Bauyrzhan
1. Make a folder `projects/<short-name>/` (gitignored, personal work).
2. Write the model as a script `projects/<name>/model.py` using `bauyrcad.api`
   (reference: `docs/API.md`). The script is the record of design intent: keep dimensions as
   named variables at the top, so changes are one-line edits.
3. Run it, then **look at the result yourself** before reporting:
   `part.render(...)` / `st.render(...)` write PNGs you can open with the Read tool.
   Check the geometry, fixtures and loads (render with `study=st`), and the stress plot.
4. Engineering checks: reactions must balance the applied loads. Compare against a hand
   calculation where one exists. Converge the quantities that matter with **local**
   refinement (`st.mesh_control(faces, size)` on fillets, bends and holes) plus a moderate
   global size. A finer global mesh quickly exceeds 300k DOF and several GB of RAM.
   - Prefer `face_force` / `pressure` to point forces. When point forces are unavoidable,
     use the `*_away_from_point_loads` summary values or `st.max_in(...)`, and say so.
   - Fixing a whole face is rigid, and its edges show spurious peaks. Read stresses a couple
     of elements away from them.
   - **Bolted joints** (no bolt/contact elements yet): bound the answer with two studies,
     (a) hole walls fixed (flexible, conservative for the flange), and (b) the whole contact
     face fixed (stiff). Report both, and the governing value.
   - Use `st.run(verbose=True)` for long runs, and never pipe a long run through `tail`
     (it hides the progress).
5. Save `projects/<name>/<name>.bcad` (+ STEP if it is going to SolidWorks) and explain how to
   open it: `dist\BauyrCAD\BauyrCAD.exe`, or `.\.venv\Scripts\python.exe -m bauyrcad <file>`.
   If the file is already open in the GUI it **reloads automatically** when rewritten.
6. Report in engineering terms: geometry and assumptions, material, BCs, mesh, max von Mises
   (where), max displacement, factor of safety, convergence, limitations.
7. **Drawings**: when a drawing is wanted (PDF / DXF / DWG), use `bauyrcad.api.Drawing` on the
   saved part or assembly: standard views, then dimension what manufacturing needs
   (`dim`, `diameter`, `auto_dimension`), fill the title block, `dr.render(png)` and look at it,
   then save `<name>.bcdrw` + the exports. A 1:1 single-view DXF (`export(..., view=...)`) is
   what laser / waterjet shops want.
8. **SolidWorks files**: if they are wanted, `part.export_solidworks(...)` /
   `asm.export_solidworks(folder)` (SolidWorks must be installed on this PC). Check the report:
   `native` (feature tree) vs solid body, and that every mate is `ok`. Never close or edit a
   SolidWorks session the user has open (the export only touches its own documents; the
   SolidWorks tests skip themselves while SolidWorks is running).
9. **Assemblies**: save each part (`projects/<name>/parts/*.bcad`), then build
   `projects/<name>/assembly.py` with `bauyrcad.api.Assembly` (mates by `comp.face(...)` in
   part coordinates), check `asm.dof()` and `asm.render(...)`, and save `<name>.bcasm` + a STEP
   of the whole assembly. The GUI opens it with File > Open Assembly.

## Suggesting improvements while working
Every project is a field test of the app. Log each piece of friction (missing feature, API
workaround, slow step, confusing error, awkward GUI step) as a row in `docs/IMPROVEMENTS.md`.
Build small fixes that unblock the job straight away. For bigger ones, finish the job first,
then end the report with 1-3 "BauyrCAD suggestions" (value to the work, size) and offer to
build the top one.

## Improving the application (encouraged)
When a project needs something BauyrCAD can't do (a feature, a load type, an export, a
faster path for the API), implement it properly rather than working around it:
- core logic in `bauyrcad/core` or `bauyrcad/fea` (no Qt imports there), API in
  `bauyrcad/api.py`, GUI in `bauyrcad/gui`, tests in `tests/`.
  Architecture and extension recipes: `docs/ARCHITECTURE.md`.
- Verify: `.\.venv\Scripts\python.exe -m pytest -q` (all green, about 30 s). For GUI changes,
  drive the real window and read screenshots (see `tests/test_gui_smoke.py` for the pattern).
- Keep the scripting API and `docs/API.md` in sync with new capabilities.
- Add a line to `CHANGELOG.md` for every user-visible change.
- **Commit on your own** once a feature is finished and the full test suite is green (standing
  permission, 2026-09-27), author the repository owner's name and e-mail as in earlier commits.
  Never push without being asked. Report what changed and the commit hash.
- The `bauyrcad` skill (`skills/bauyrcad/`) is how chats outside this folder find the app. After
  editing it run `.\.venv\Scripts\python.exe packaging\build_skill.py --install` (updates
  `~/.claude/skills` and `dist/bauyrcad.skill` for claude.ai).
- Rebuild the Windows app when a new version is wanted: `.\packaging\build_windows.ps1`
  (runs a self-test inside the frozen exe). The runnable app is `dist\BauyrCAD\BauyrCAD.exe`,
  **not** anything under PyInstaller's work folder.

## Conventions
- Units mm · N · MPa. Y is up; Front = XY (+Z), Top = XZ (+Y), Right = YZ (+X).
- SolidWorks naming and behaviour for UI features. AutoCAD-style aliases for the command line
  (`MainWindow._register_commands`).
- All OCC calls stay in `core/geometry.py`, `core/modeling.py` (feature operations),
  `core/exchange.py`, `core/step_io.py`.
- Licensing: own code is MIT. Gmsh is GPL, so avoid adding more copyleft dependencies
  (this is why the sketch solver is in-house and not SolveSpace).
