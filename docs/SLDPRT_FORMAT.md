# SolidWorks native files (.sldprt / .sldasm), decoded

Decoded on 2026-10-11 (containers, geometry) and 2026-10-15 (feature tree, mates) from real
files on this PC, without SolidWorks running and without disassembling any SolidWorks code.
Reader: `bauyrcad/core/sldprt.py` (pure Python and NumPy, no OCC, no Qt, no SolidWorks),
tests: `tests/test_sldprt.py` (fixtures saved by SolidWorks 2024 in `tests/data/sw/`).

```
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt part.sldprt                  # summary (JSON)
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt part.sldprt --sections       # list sections
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt part.sldprt --stl part.stl   # mesh -> STL
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt part.sldprt --parasolid part.xmp_bin
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt part.sldprt --tree [--all] [--config N] [--json]
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt assy.sldasm --mates [--json]
.\.venv\Scripts\python.exe -m bauyrcad.core.sldprt part.sldprt --keywords       # dimensions
```

**Result in one line.** A SolidWorks file is a package of named sections. The exact solid is a
**Parasolid** transmit stream (Parasolid is the kernel SolidWorks runs on), and a ready-made
**triangle tessellation** is stored next to it. STL can be produced today from the
tessellation. STEP needs a Parasolid XT to OpenCASCADE translator (roadmap below). The
**FeatureManager tree** (names, feature types, suppression, absorbed sketches, displayed
dimensions) and the **mates** of an assembly are readable too (section 7); the SolidWorks
export tests use them to prove from the saved file that it holds native features.

## 1. Containers

SolidWorks has used two containers. Both hold the same section names.

### 1a. OLE2 compound file (observed in SolidWorks 2013-era files)

Starts with `D0 CF 11 E0 A1 B1 1A E1`: a Microsoft Compound File Binary (MS-CFB), the format
of old `.doc` / `.xls` files. Streams are stored in storages (`Contents/Config-0-Partition`).
Streams whose name ends in `__ZLB` hold ZLB chunks (section 3); BauyrCAD unwraps them and
drops the suffix. `sldprt.py` has a small stdlib CFB reader (no `olefile` dependency).

### 1b. ZIP-like container (SolidWorks 2015+; observed for Parasolid 28-37 files)

A disguised ZIP archive: there is no central directory to rely on, and a section is a ZIP
*local file header* with three changes.

| Offset | Size | Value | Meaning |
|---|---|---|---|
| 0 | 4 | varies **per file** (e.g. `6A AC E4 5D`) | replaces ZIP's `PK 03 04` signature |
| 4 | 2 | `14 00` | version needed = 20 |
| 6 | 2 | `06 00` | flags |
| 8 | 2 | `08 00` | method 8 = deflate |
| 10 | 4 | constant per file | DOS time/date |
| 14 | 4 | | CRC-32 of the uncompressed data |
| 18 | 4 | | compressed size |
| 22 | 4 | | uncompressed size |
| 26 | 4 | | name length *n* (ZIP's name + extra length fields merged into one) |
| 30 | *n* | nibble-swapped ASCII | section name: `0x34 0xF6 0xE6 ...` swapped gives `C o n ...` |
| 30+*n* | csize | | raw deflate data (`zlib` wbits = -15) |

The file starts with 4 varying bytes followed by `00 00 00 04`. The same 6-byte marker also
appears in directory-like records whose fields do not validate. The reader therefore accepts a
section only when its data inflates to the stated size **and** the CRC-32 matches. This needs
no knowledge of the per-file signature, and it holds for every file tested.

Independent descriptions match this layout: the UK National Archives' PRONOM registry
(fmt/1967: `00000004` at offset 4, then nibble-swapped `Contents/`), and the open projects
listed under Sources. One of them reads the name rotation as `(byte 7) & 7` bits, which is 4
in every file seen here; `sldprt.py` assumes 4, because it accepts only `00 00 00 04`.

The package is laid out like Office Open XML (OPC): it contains `[Content_Types].xml`,
`_rels/.rels`, `docProps/*.xml` (custom properties in plain XML) and `swXmlContents/*`.

## 2. Sections that matter

| Section | Content |
|---|---|
| `Contents/Config-N-Partition` | **Exact B-rep** of configuration N: Parasolid partition + deltas (section 4) |
| `Contents/Config-0-GhostPartition` | A second, small Parasolid partition in some files (purpose not confirmed) |
| `Contents/DisplayLists` | **Tessellation** of the faces and edges (section 5) |
| `Contents/Config-N` | Model objects (MFC `CArchive` serialisation) |
| `Contents/Config-N-ResolvedFeatures` | **Feature tree** of configuration N: one record per feature (section 7a) |
| `swXmlContents/KeyWords` | Search index: every feature with its display type and shown dimensions (7b) |
| `Contents/3DExperienceExchange2` | UnQLite database of MessagePack documents: planes, axes, Hole Wizard, mates (7c) |
| `SwDocContentMgr/SwDocContentMgrInfo` | Document Manager summary: **mate list** of an assembly (7d) |
| `Header2`, `Contents/Config-N-ModelHeader` | Model header (`moHeader_c`, history stamps) |
| `PreviewPNG` / `Preview` | Thumbnail (PNG / DIB) |
| `docProps/custom.xml`, `docProps/ISolidWorksInformation.xml` | Custom properties (BOM part number, material, ...) as XML |
| `swXmlContents/COMPINSTANCETREE` | **Assemblies:** XML list of referenced files and component instances |
| `FaceTessellations/*` | **Assemblies:** cached component tessellation (same record format as section 5) |
| `ThirdPty/*`, `ThirdPtyStore/*` | Add-in data (CAMWorks, Motion, ...) |

The `mo*_c` / `ui*_c` / `uo*_c` streams are Microsoft Foundation Classes `CArchive` object
streams: a new class is introduced by `FF FF <schema u16> <len u16> <class name>`
(for example `FF FF 01 00 0A 00 "moHeader_c"`), and its fields follow in class-specific order.
Strings are `FF FE FF <len u8> <UTF-16LE>`. The feature *records* (name, id, class,
suppression, absorbed children) are decoded (section 7); the feature-specific fields after
them (end conditions, selected edges) are not.

## 3. ZLB chunks (inside sections, both containers)

```
[16-byte tag 23 1D D5 71 DA 81 48 A2 A8 58 98 B2 1B 89 EF 99]
[u32 LE uncompressed size][u32 LE compressed size][zlib stream (78 01 ...)] [8 bytes]
```

In `Config-N-Partition` each chunk is additionally prefixed by its u32 LE length, and there are
two chunks: the partition and its deltas.

## 4. Parasolid B-rep

Each inflated chunk is a Parasolid **neutral binary transmit** stream:

```
"PS" [u32 BE len] ": TRANSMIT FILE (partition) created by modeller version 3501256"
     [u32 BE len] "SCH_3501256_35102_13006"   <- base schema 13006 + embedded schema delta
     ... schema delta (C/I/D/A-coded field edits, e.g. "I" mesh, polyline, lattice) ...
     ... nodes (BODY, SHELL, FACE, LOOP, FIN, EDGE, VERTEX, surfaces, curves, ...) ...
```

* The integers in the binary transmit stream are **big-endian**, unlike the container.
* `modeller version 3501256` means Parasolid 35.1. On this PC the versions range from 25
  (the 2013-era OLE2 files) to 37.
* The first chunk is the **partition** (all bodies of the configuration). The second chunk
  holds the **deltas** (rollback history, not needed for geometry).
* Saved alone, these are standard Parasolid files: `.xmp_bin` (partition) and `.xmd_bin`
  (deltas). This is the input the STEP translator must read. The schema-delta mechanism
  and the node layouts are documented by Siemens in the *Parasolid XT Format Reference*
  (see Sources).

## 5. Display-list tessellation (STL path)

`Contents/DisplayLists` (in parts) and `FaceTessellations/*` (in assemblies) hold one record
per face (`uoTempFaceTessData_c`) built from typed arrays:

```
typed array = [u32 element size][u32 type][u32 2][u32 count][count * element size bytes]
   type 0x08 = plain ints/bytes, type 0x64 = float32 xyz
face record = int32[k]          triangle-strip lengths (each >= 3)
              float32 xyz[V]    vertex positions in METRES   (V = sum of strip lengths)
              float32 xyz[V]    unit vertex normals
              int32[2V-2k]      strip edge adjacency / boundary edge ids
              int32[k]          adjacency count per strip (2L-2)
              ...               then (observed, not decoded field by field) byte flags, the face
                                bounding box as doubles, boundary-edge polylines with edge ids,
                                and moFromSktEntSurfIdRep_c (persistent face id)
```

Strip *i* of length *L* gives triangles `(s+t, s+t+1, s+t+2)` with alternating winding. The
reader orients each triangle by the stored normals and welds coincident vertices, which gives
watertight meshes. Not every file stores tessellation. Files saved without display data
(720 of 1794 parts here, many of them SolidWorks samples and vendor downloads) have no
`DisplayLists` section and need the B-rep path.

## 6. Assemblies

An assembly stores no solid of its own (no `Partition`).
`swXmlContents/COMPINSTANCETREE` is readable XML: `swFile` entries (doc type and absolute path of
every referenced part), `swModel` per configuration with bounding boxes, and `swReference` per
component. `FaceTessellations/*` caches the component meshes. Open item: the component
placement transforms (not yet located; the only test assembly placed its parts at the origin).

## 7. Feature tree, dimensions and mates (export verification)

```python
from bauyrcad.core import sldprt
tree = sldprt.feature_tree("part.SLDPRT")            # [SwFeature], FeatureManager order
sldprt.tree_mismatches("part.SLDPRT", [("Sketch1", "ProfileFeature"), ("Boss-Extrude1", "Extrusion")])
sldprt.mates("assy.SLDASM")                          # [SwMate]
sldprt.keywords("part.SLDPRT"); sldprt.exchange_docs("part.SLDPRT")   # raw sources
```

`SwFeature`: `name`, `id` (= `IFeature.GetID`), `mfc_class`, `type_name`
(= `IFeature.GetTypeName2`), `suppressed` (None where the file does not tell), `parent`
and `children` (absorbed sketches), `internal` (a sketch made inside its parent, e.g. Hole
Wizard's), `hidden`, `dims` (dimension name -> shown value), `system`, `resolved_by`.
`tree_mismatches(src, expected, helpers=(), exact=False)` lists why a file does not hold the
expected steps (missing, out of order, wrong type or suppression, a step name that belongs to
another feature's internal sketch, a body without history); `[]` proves the native tree.
`SwMate`: `name`, `type` (`swMateType_e`), `mfc_class`, `id`, `components`, `suppressed`,
`align` / `alignment`, `distance` (m), `angle` (rad), `flip`, `lock_rotate`, `entities`.

Four sections are combined.

### 7a. `Contents/Config-N-ResolvedFeatures`: one record per feature

An MFC stream that continues the archive of `Contents/Config-N` (its first u32 is the map
count it starts from). Every feature is a record found at a `CString`:

```
[class tag][80 04][CString name][7 bytes][u8 flag][u32 feature id]
[u32 0][FF FE FF 00][6 x 00][u16 n children][n child references][u32][u32 suppressed 0/1]...
```

* **Class tag**: a new-class header `FF FF <schema> <len> <name>` (the class is named right
  there), or a reference `0x8000|k` (big form `7F FF` + u32 `0x80000000|k`) to a class
  introduced earlier. References are resolved by vote: the candidates are the classes whose
  header comes before k is first used (any class when k is below the start count), and the
  records using k vote with their KeyWords display type through a static table built from
  the exact headers of 1,781 files (`('Feature','Fillet') -> Fillet_c`,
  `('Extrusion','Boss-Extrude') -> moExtrusion_c`...). Counting the MFC map is only the
  fallback: bytes that look like tags make the count drift (by hundreds in files with Hole
  Wizard sketches).
* **Type**: `GetTypeName2` is the class name without `mo` and `_c` (`moRevCut_c` -> `RevCut`,
  `Fillet_c` -> `Fillet`, `moICE_c` -> `ICE`, `moBaseBody_c` -> `BaseBody`), checked against
  the API list; one exception (`moOffsetRefSurface_c` -> `OffsetRefSuface`).
* **Order**: stream order is tree order. Everything up to the `Origin` record is the default
  folders and planes (`system`). Absorbed profile sketches come just before their owner; Hole
  Wizard writes its two sketches after itself.
* **Feature id** = `IFeature.GetID` = the KeyWords id. Records with id `FFFFFFFF` are
  sub-objects. The flag byte is 0x40 or 0xC0; it is **not** a user/system marker (SolidWorks
  sets 0xC0 on user features once a part has a second configuration).
* **Children** (absorbed features). A child stored earlier (an extrude's profile sketch) is a
  2-byte back reference `u16 k` to its MFC object index; a child stored inline (Hole
  Wizard's sketches) is a whole object (a class tag with the high bit). Links are made from
  inline children (exact), KeyWords `DissectableChildren` (exact; Instant3D and Hole Wizard),
  and for back references only when the anchored object count lands exactly on the nearest
  earlier free sketch (object index = class index + 1 for a record that introduces a class
  referenced later). Otherwise `parent` stays None.
* **Suppression**: the u32 after the child references (offset +20 with no children, +2 or +6
  per back reference), per configuration (`Config-K-ResolvedFeatures`). After an inline
  child its offset is unknown; then the 3DExperience document gives it where one exists
  (planes, axes, points, coordinate systems, Hole Wizard).

### 7b. `swXmlContents/KeyWords`

One prefix byte (0x86), then XML: `<Feature|Sketch|Extrusion|Cut|HoleWizard|Reference id=
Name= Type=>` per feature (`Reference` = assembly component), with `DissectableChildren` and
`<Dimension Name="D1">20</Dimension>` children holding the **shown** values in document units
(`20`, `R6`, `270°`, `<MOD-DIAM>6.6`; `sldprt.dim_value` parses them). Sketch dimensions are
D1..Dn of the sketch; Hole Wizard dimensions sit on its placement sketch ("Thru Hole Dia.").
`<Configuration id=...>` items are configurations, not features. The display types are
localised strings (these files come from an English install).

### 7c. `Contents/3DExperienceExchange2`: UnQLite + MessagePack

An UnQLite database ('lhash' engine, page size in the header at offset 19, big-endian).
Each bucket page starts with the u16 offset of its first cell; a cell is u32 hash, u32 key
length, u64 data length, u16 next cell, u64 first overflow page, then key and data (overflow
pages: 18-byte header on the first, 8 bytes on the next). Values are MessagePack maps in which
**keys repeat** (`class`, `suppressed`, `hidden` twice; one `model` per configuration in the
TOC), so the decoder keeps them (`sldprt.Repeated`, the last value is current).

* `TOC`: per configuration (`configid`, `configname`) the GUIDs of its documents.
* Feature documents (reference planes, axes, points, coordinate systems, Hole Wizard, Split):
  `class`, `name`, `id` (feature id), `suppressed`, `hidden`, plus geometry (plane normal and
  point, axis points in metres).
* Mate group (`moMateGroup_c`/`moNodeList_c`, `NumberMates`, `Mate0..n`): per mate `class`,
  `description` (name), `id`, `suppressed`, `Aligment` (sic: 0 none, 1 aligned, 2
  anti-aligned, i.e. `IMate2.Alignment + 1`, confirmed by the SolidWorks test), `Distance`
  (m), `Angle` (rad), `FlipDim`, `LockRotate`, and `Ref0/Ref1` (entity class
  `moPlaneRef_w`/`moCylinderRef_w`, component `pin-1@hinge`).

### 7d. `SwDocContentMgr/SwDocContentMgrInfo`: the mate list

The MFC stream the Document Manager reads; one `moBCMate_c` record per mate:
`CString configuration, u32 0, CString name, i32 2, i32 swMateType_e, CString class,
CString "name (compA<1>,compB<1>)", u32 0, u32 mate id`. It is in every assembly; the
3DExperience details are in most (1,929 of 2,278 mates here). `sldprt.mates` merges the two
by name.

### 7e. Verification (2026-10-15)

* **Against the SolidWorks API** (files saved by SolidWorks 2024 from BauyrCAD exports,
  `tests/data/sw`, and every file the real-SolidWorks tests save): names, ids, GetTypeName2
  and order of every top-level feature, IsSuppressed2 in two configurations (including
  extrudes whose suppression follows a back reference, and Hole Wizard through 3DX), and per
  mate IMate2.Type, IMate2.Alignment and suppression all equal the decoded values
  (`tests/test_solidworks.py` checks this on every export: `assert_decoded_matches_api`).
* **Corpus** (all 1,973 SolidWorks files on this PC, 1,805 with a feature tree):
  * 49,543 feature records: 34,161 classes from their own header, 15,093 by KeyWords vote,
    121 by MFC count, 168 (0.34 %) unresolved (user-named motion plots and sensors, external
    reference objects in old vendor files).
  * Suppression of features without children equals the independent 3DX `suppressed` field
    in 6,962 of 6,962 records.
  * All 61,774 live UnQLite cells decode; the Document Manager mate type equals the class's
    `swMateType_e` for all 2,278 mates in 169 assemblies.
  * Back-referenced children: where KeyWords names the child, the anchored count finds it in
    73 cases and is wrong in 1, which the KeyWords link takes precedence over; elsewhere it
    is rejected unless it lands on the nearest sketch (the "nearest sketch" guess alone is
    right in only 98 %, so it is never used alone).

### 7f. Limitations

* Feature parameters other than the shown dimensions (end conditions, flip flags, selected
  edges and faces, pattern seeds) are not decoded: the real-SolidWorks tests read those back
  through the API (`IFeature.GetDefinition`).
* `moICE_c` (Instant3D) covers boss and cut extrudes alike: tell them apart by name or
  `IFeature.GetTypeName` in SolidWorks.
* Suppression after an inline child is only known where a 3DExperience document exists.
* Equations and global variables are not decoded (only the `Equations` folder record).
* Class votes use English KeyWords types; a localised SolidWorks needs its own table (the
  class headers and the MFC-count fallback still work).
* MFC class names never seen in this corpus (Wrap, Loft-cut, Boundary-cut, curve-driven and
  fill patterns, Thread) resolve only once a sample with that class header is decoded.

## 8. Verification of the geometry readers (2026-10-11)

* **Corpus:** 2079 SolidWorks files on this PC (2063 ZIP-like, 16 OLE2; 1794 parts).
  * All 2079 files read, with every section CRC-checked.
  * A Parasolid partition was found in all 1794 parts.
  * The mesh decodes for all 1069 parts that store tessellation.
* **Against independent STEP files** (`projects/<project>/solidworks/SW0x_*.SLDPRT` vs
  `.step`, measured with OCC), decoded mesh against B-rep:

| Part | Faces | Volume error | Area error | Bounding box |
|---|---|---|---|---|
| SW01 Frame | 4045 | +0.07 % | -0.02 % | 0.001 mm |
| SW02 Moving jaw | 422 | -0.03 % | -0.01 % | 0.001 mm |
| SW03 Vacuum pad | 426 | -0.32 % | -0.06 % | 0.000 mm |
| SW07 Cylinder body | 11 | -0.59 % | -0.26 % | 0.051 mm |
| SW08 Cylinder rod | 14 | -0.31 % | -0.04 % | 0.000 mm |
| SW09 Packs | 12 | 0.000 % | 0.000 % | 0.000 mm |
| Box_1 (hand calculation 200x130x100) | 6 | exact | exact | exact |

  The remaining errors are chord error on curved faces (the mesh is the display tessellation).
  SW04, SW05 and SW06 differ by 10-18 mm because those `.SLDPRT` files predate their STEP files
  (redesigned on 2 Oct). Their decoded meshes are still closed and manifold.
* **Visual check:** `projects/sldprt_decode/decoded_meshes.png` shows SW01 frame, the SolidWorks
  sample triple tree with engraved text, and a 2013 OLE2 Festo body.

## 9. Roadmap to SLDPRT -> STEP

| Step | What | Size |
|---|---|---|
| 0 | **Done:** containers, sections, Parasolid extraction, display mesh to STL | |
| 1 | `Import > SolidWorks (mesh)`: STL-like body from the display lists, as a reference body or for FEA and printing | S |
| 2 | *If SolidWorks is installed:* exact import by driving it (`OpenDoc6`, then `SaveAs` STEP) through the existing COM bridge in `bauyrcad/solidworks.py` | S |
| 3 | XT binary reader: tokenizer, base schema 13006 tables from the XT Format Reference, embedded schema delta, node graph | M |
| 4 | XT to OCC topology (BODY, REGION, SHELL, FACE, LOOP, FIN, EDGE, VERTEX) with analytic geometry (plane, cylinder, cone, sphere, torus, line, circle, ellipse) and NURBS (B_SURFACE, B_CURVE), pcurves (SP_CURVE), trimmed, offset, swept and spun surfaces | L |
| 5 | Implicit geometry: intersection curves (from their stored chart points) and rolling-ball blend surfaces (BLENDED_EDGE), approximated as NURBS | L |
| 6 | Assemblies: component transforms, then a STEP assembly via `exchange.write_step_assembly` | M |

**Built-in test oracle.** Every file that stores display lists carries its own reference mesh.
The XT translator (steps 3-5) can be checked automatically, per face, by the distance between
its B-rep and the stored tessellation, over the 1069-file corpus on this PC. Edge ids in the
face records are Parasolid node ids (per blussyya), so mesh faces map 1:1 onto B-rep faces.

**Before writing steps 3-5 from scratch, evaluate:**

* **parasolid-kit**: MIT and Apache-2.0. Rust core with Python bindings
  (`pip install parasolid-kit[occt]`). It reads X_T and X_B, has a built-in profile for the
  SolidWorks partition schema, and exports STEP through OCCT. It is young (7 stars, no release
  yet) and describes its coverage as a "verified_subset". It is the closest drop-in for step 3
  and part of 4. Licence-compatible with BauyrCAD (MIT, no copyleft).
* **blussyya/sldprt-format-research**: MIT, Node.js. The most complete byte-level
  documentation. Its full XT B-rep reader was written from the XT Format Reference. Its STEP
  output is exact for planar faces only (curved faces are triangulated). Use it as a
  reference, not a dependency.
* **Commercial readers** (paid): CAD Exchanger, HOOPS Exchange, ODA MCAD SDK for SolidWorks
  (since June 2025), Datakit, Spatial, CoreTechnologie.
* **SolidWorks Document Manager API** (`ISwDMConfiguration2::GetPartitionStream`): exports the
  same partition. It needs a subscription licence key that may not be redistributed, so it is
  not usable inside BauyrCAD.

## 10. Legal and licensing notes

These are facts gathered for this note, not legal advice.

* Only the *data files* were analysed. No SolidWorks or Parasolid program code was
  disassembled, and the reader uses no Dassault or Siemens code, SDKs or libraries.
* **EU:** the CJEU (*SAS Institute v World Programming*, C-406/10, 2012) held that the format
  of data files is not protected expression under the Software Directive 2009/24/EC. Art. 6 of
  that directive also permits decompilation for interoperability.
* **US:** 17 USC 1201(f) permits analysis needed for interoperability of an independently
  created program.
* **SolidWorks EULA:** it restricts reverse engineering of the *software*. The commercial
  wording was not verified (the page timed out). Reading files you lawfully hold is a different
  act, but re-check before any commercial distribution of the reader.
* **Siemens XT Format Reference:** it states that its audience is people writing translators
  to or from the transmit format, and IANA lists it as available on request. The copy found
  carries a copyright notice: use it as a reference and do **not** commit it to this repo.

## Sources

* PRONOM fmt/1967 (SolidWorks 2015+ signature): https://pronom.nationalarchives.gov.uk/fmt/1967.xml
* blussyya/sldprt-format-research (container, display lists, XT reader; MIT): https://github.com/blussyya/sldprt-format-research
* cadmpeg SLDPRT specification (clean-room; CC BY 4.0): https://github.com/cadmpeg/cadmpeg
* XRTC5/sldprt-export (Python, display mesh; MIT): https://github.com/XRTC5/sldprt-export
* schwitters/openswx (metadata, previews; MIT): https://github.com/schwitters/openswx
* parasolid-kit (XT reader, OCCT STEP; MIT/Apache-2.0): https://github.com/monozukuri-ai/parasolid-kit
* IANA, Parasolid transmit media type: https://www.iana.org/assignments/media-types/model/vnd.parasolid.transmit.binary
* HOOPS Exchange SolidWorks reader (Parasolid inside SLDPRT): https://docs.techsoft3d.com/hoops/exchange/start/format/solidworks_reader.html
* OCCT Parasolid reader is commercial: https://github.com/Open-Cascade-SAS/OCCT/discussions/1210
* CJEU SAS v WPL summary: https://www.scl.org/2436-sas-v-wpl-no-copyright-says-ecj/
* 17 USC 1201: https://www.law.cornell.edu/uscode/text/17/1201
