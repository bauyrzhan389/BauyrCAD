# SolidWorks native files (.sldprt / .sldasm), decoded

Decoded on 2026-10-11 (containers, geometry) and 2026-10-15 (feature tree, mates) from real
files on this PC, without SolidWorks running during parsing. Those initial reader milestones
used data-file analysis; later Shell research used targeted decompilation (section 28).
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
Wizard's), `hidden`, `dims` (dimension name -> shown value), `system`, `resolved_by`,
`unlinked` (absorbed children the file declares but the decoder could not identify: when it
is non-zero, `children` is incomplete and a top-level sketch may really be absorbed).
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
  introduced earlier. The archive numbers each class ONCE, in the order of the headers, so
  a k at or above the start count names a class whose header is in this stream before k is
  first used, and a smaller k a class of `Contents/Config-N` (header not in this stream).
  The candidate classes of k are the ones its records' KeyWords display types allow, from a
  static table built from the exact headers of 1,781 files (`('Feature','Fillet') ->
  Fillet_c`, `('Extrusion','Boss-Extrude') -> moExtrusion_c / moBoss_c / moICE_c`); how
  often a type went with a class elsewhere only breaks ties (a suppressed Instant3D boss is
  typed 'Boss-Extrude' too). This stream's indices are matched to the headers one-to-one
  and in stream order, choosing per k the candidate header whose simulated MFC count,
  corrected at the indices that have only one candidate, is nearest k. Counting the MFC map
  alone is the fallback for references without a KeyWords type: bytes that look like tags
  and objects the count misses make it drift (by hundreds in files with Hole Wizard
  sketches).
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
  inline children (exact), KeyWords `DissectableChildren` (exact; Instant3D and Hole Wizard,
  but empty for a feature suppressed in that configuration), and back references: the
  header of a class whose index is resolved has an exact object count (class index + 1),
  so those anchors bound where object k can be in the stream. A reference is linked when
  one free sketch lies within its bounds (in the order of the owner's other references:
  a loft's profiles, a sweep's path), or when the anchored count of one of several is less
  than half as far from k as the next one's. Otherwise `parent` stays None and the owner's
  `unlinked` counts the child.
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
* **Rework after the WP01 review (2026-10-19)**, 924 SolidWorks 2024 sample parts:
  * Class references: features whose 3DExperience document names their class, resolved by
    reference, disagree with it in 2 of 2,233 records (both in one part that holds an
    inserted part's tree with clashing ids), down from 7; boss extrudes after the base
    feature decode as `moBoss_c`, thin extrudes as `moBossThin_c` and sensors as
    `moSensor_c` where the old weighted vote said `moExtrusion_c` / `moICE_c` /
    `moSensorFolder_c`. Only that one part still has two indices on one class.
  * Back references alone (KeyWords children hidden) reproduce 1,436 of the 1,625 links
    KeyWords lists and contradict none (before: 266 found, 0 wrong); the rest stay to
    KeyWords.

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

* The initial reader used data-file comparisons. Later Shell research also used targeted
  static analysis of SolidWorks program code. This is not a claim of clean-room development;
  executables, DLLs, SDKs and decompiler output are not distributed in this repository.
* **Kazakhstan:** [Copyright Law, Article 24(2)-(3)](https://adilet.zan.kz/rus/docs/Z960000006_/compare/eng)
  provides a conditional interoperability exception for lawful possessors. It limits analysis
  to necessary parts and information unavailable from other sources, and restricts subsequent
  use and disclosure. It is not blanket permission to copy a product's protected expression.
* **EU/US background:** [SAS v World Programming](https://curia.europa.eu/jcms/upload/docs/application/pdf/2012-05/cp120053en.pdf)
  distinguishes functionality and data formats from protected expression.
  [17 USC 1201(f)](https://uscode.house.gov/view.xhtml?edition=prelim&num=0&req=granuleid%3AUSC-prelim-title17-section1201)
  contains a narrow interoperability exception to access-control restrictions. Neither source
  establishes permission for this project's particular development or distribution.
* **Licence and seed-file review:** the applicable agreement and redistribution rights for
  model seeds must be established separately. Published educational terms contain use and
  generated-file restrictions; the installed licence stub points to the CLOSA/transaction
  documents rather than establishing which agreement applies. This review is unresolved.
  The project owner authorized public publication on 2026-11-02. That decision is not a
  legal opinion or a representation that the unresolved review has been completed.
* **Siemens XT Format Reference:** it states that its audience is people writing translators
  to or from the transmit format, and IANA lists it as available on request. The copy found
  carries a copyright notice: use it as a reference and do **not** commit it to this repo.

## 11. Writing Parasolid XT: the .x_t export (2026-10-19)

`bauyrcad/core/xt_brep.py` (OCC solid -> Parasolid's model) and `bauyrcad/core/parasolid.py`
(the writer, no OCC); tests `tests/test_parasolid.py`, `tests/test_sw_parasolid.py`.
Everything below was checked against Parasolid's own output: the partitions in
`tests/data/sw/*.SLDPRT` and `.x_t` files SolidWorks 2024 saved from the same parts.

* **Schema** `SCH_1200000_12006` (the XT Format Reference: "applications writing XT files
  must use" it); SolidWorks 2024 reads it. Node layouts = the transmitted fields
  (`xmt_code` 1) of Parasolid's `sch_12006` schema file, which SolidWorks ships in
  `<install>\schema_18` (a test compares them when present; the file is not committed).
* **Text**: banner lines, `**PART1;` key=value (80 columns), `**PART2;SCH=...;USFLD_SIZE=0;`,
  `**PART3;`, `**END_OF_HEADER`, then one stream `T51 : TRANSMIT FILE created by modeller
  version 120000017 SCH_1200000_120060 ` + nodes + `1 0`, cut into records of at most 80
  characters. Numbers end with one space; chars (`+ - V S`) and nulls (`?`) do not; a record
  never ends in a space (readers drop trailing spaces; leading ones count). Node 1 is the BODY.
* **Units** metres; `res_size` 1000 (size box +-500 m), `res_linear` 1e-8 m.
* **Structure of a solid**: BODY (state 1, body_type 1, nom_geom_state 1); a void REGION (the
  body's first, infinite) and a solid one; a solid SHELL (body set, faces chained as back
  faces) and a void SHELL (body null, the same faces chained as front faces). Each face,
  edge and vertex has its own surface / curve / point, owned by it and chained from
  `boundary_surface` / `boundary_curve` / `boundary_point`. Every entity but fins and the body
  has a node id.
* **Fins** (`HALFEDGE`): two per edge, `other` pointing at each other; the edge points at its
  positive fin; a fin's vertex is the one at its forward end; `next_at_vx` chains the fins at
  a vertex. Loops run with the face on their left; edges run along their curve (curve sense
  `+`); a face's sense says whether its outward normal is the surface's natural normal
  (computed at a point, not from OCC frame handedness).
* **What OCC has and Parasolid does not**: seam edges (dropped: Parasolid's analytic surfaces
  are periodic; an edge used once along a seam line is kept), degenerate edges (dropped), and
  vertices left with one closed edge (dropped: a full circle is a vertex-less ring edge, as
  in Parasolid's own shaft). A cone's apex is an **isolated loop**: one edge-less fin on the
  apex vertex, sense unset. A full sphere or torus face has no loops at all.
* **Analytic conventions**: cylinder, sphere and torus natural normals point outward; the
  cone's axis points the way its radius grows (SolidWorks writes `(0, -1, 0)` for a cone
  narrowing along +Y), `radius` at `pvec`, positive half angle, natural normal outward.
* **Checks without Parasolid**: SolidWorks 2024 opens every test part as one solid with
  `IBody2.Check3` clean and Parasolid's face / edge counts, at the volume it measures for the
  same part through STEP (to 1e-7; it is up to ~2e-5 below OpenCASCADE's exact volume on
  faces bounded by ellipses). parasolid-kit (an independent Rust reader, dev extra) reads the
  simpler parts back to the same volume; it does not read vertex-trimmed arcs or apex fins.
* **B-splines** (checked against `.x_t` files SolidWorks 2024 wrote of the same parts):
  `B_CURVE` -> `NURBS_CURVE`, `B_SURFACE` -> `NURBS_SURF`, with `BSPLINE_VERTICES` (v varies
  fastest; rational vertices `x w, y w, z w, w`), `KNOT_SET` (distinct knots) and `KNOT_MULT`;
  the auxiliary `CURVE_DATA` / `SURFACE_DATA` are left null (allowed; SolidWorks writes them),
  knot type and form 1 (unset). Every OCC curve or surface that is not analytic is written
  as one: B-spline and Bezier as they are, extrusion and revolution surfaces converted over
  the face's span, offsets approximated to 1e-7 mm.
  * **Periodic** directions are stored unwrapped: OCC `[0, 2pi] x [6, 6]`, degree 6, 6 poles
    -> `[-2pi, 0, 2pi, 4pi] x [1, 6, 6, 1]`, 7 vertices (`xt_brep.periodic_form`; which pole
    comes first is found by evaluating). A closed direction is made periodic so that its seam
    can go: the closed sweep is one face with no edges and no loops, as SolidWorks writes it.
  * **G1**: Parasolid rejects a face B-surface whose parameter lines turn at a knot of full
    multiplicity (`IBody2.Check3` code 30, `swTopolNotG1Continuous`) even when its normal is
    continuous - a sweep along a spline path. Such knots are removed where the surface is C1
    (within 1e-6 mm), else split into multiplicities degree - 1 and 1 a hair apart, moving the
    surface < 1e-7 mm (SolidWorks' STEP import does the same, gaps 1e-11 to 5e-8). Knots
    where only the speed changes (a rational circle's) are left alone: splitting them made
    SolidWorks' volume of a revolved spline 3.5e-5 low.
  * Volumes: SolidWorks measures our file and the STEP of the same part alike (to 4e-7);
    OpenCASCADE's own volume of some B-spline solids is the uncertain one (the closed ring:
    391.59 against 390.79 mm³ in SolidWorks both ways).
* **Accuracy**: an edge or vertex is written accurate only if it really lies within half
  Parasolid's 1e-5 mm of its faces (measured: vertex to edge ends, 9 edge points projected
  on the faces; OCC's tolerances only bound this, often loosely - a chamfer's vertices carry
  1e-4 mm). Written accurate further off, SolidWorks reports "bad vertex" (code 16).
* **Tolerant edges** (layout copied from a Parasolid 30 file with 1 024 of them):
  `EDGE.tolerance` set, `EDGE.curve` null; each fin's `curve` is a `TRIMMED_CURVE` (owner the
  fin, sense +, running the fin's way: the positive fin along the edge, the negative one
  against it) whose basis is an `SP_CURVE` (owner the body, sense +) on the face's surface,
  holding a 2D `B_CURVE` (node id 0, no owner, not chained; non-rational, vertex_dim 2) in
  the surface's XT parameters (a plane's and the lengths of a cylinder's or cone's in
  metres; a cone's v along its axis = OCC's generatrix v x cos(half angle)). A ring that
  wraps around a periodic surface has an open 2D curve with equal 3D end points; a loop
  closed in the parameter plane, a periodic one. Dependents are registered on what they
  use by rings of `GEOMETRIC_OWNER` (`owner` = the dependent, `shared_geometry` = the used
  geometry, which points at the ring): trimmed curve -> SP-curve, SP-curve -> surface. The
  vertices of tolerant edges are tolerant; an accurate edge at a tolerant vertex gets a
  `TRIMMED_CURVE` over its curve (owner the edge, its basis owned by the body), as every one
  of the 2 151 in that file does. Tolerances: 2.5x how far the SP-curves stray from the
  edge's curve (so the fins' curves also agree), at least 3x the measured gap.
* **Offset surfaces**: GeomConvert approximates them loosely by itself (0.03 mm seen on a
  shell of a spline part); they are approximated on purpose (GeomConvert_ApproxSurface,
  1e-7 mm), and, with a 2 % margin around the face, that approximation went 0.14 mm wrong
  inside the face while reporting 1e-8: the margin is shrunk until a check by projection
  (both ways, 25 x 25 points) passes. Extrusion and revolution surfaces convert exactly
  (a revolution's B-spline has another angle parameterization: checked by projection too).
* **Regions and shells** (as SolidWorks writes them): the infinite void region first, its
  shells holding each lump's outer faces as front faces; per lump a solid region whose
  shells hold the outer faces and each cavity's faces as back faces; per cavity a finite
  void region whose shell holds the cavity's faces as front faces.
* **Several bodies**: SolidWorks writes a multi-body part as an `ASSEMBLY` root (state 1,
  type 1) with one `INSTANCE` (type 1, an identity `TRANSFORM`: rotation_matrix 9 doubles,
  flag 0, perspective null) per `BODY` (`ref_instance` = its instance), and opens such a file
  as an assembly - ours too. One body made of several lumps opens as a part with one body.
* **Attributes** (as SolidWorks writes them): one `ATTRIB_DEF` per kind in the file
  (`ATT_DEF_ID` its identifier), each `ATTRIBUTE` (owner, definition, a pointer to its
  values) chained on its owner (`attributes_features`, next / previous) and per part and
  kind (next_of_type / previous_of_type); each part (`BODY` / `ASSEMBLY`) lists the first
  attribute of each kind in `attribute_chains`: a `LIST` (type 4, block length 20, entry
  size 8, not transmitted, node id 0) over a 20-entry `POINTER_LIS_BLOCK`.
  * Name: `SDL/TYSA_NAME` (8017), one char field, actions 0, any owner: on the assembly,
    each body and each instance. Strings are written as they are: printable ASCII, no two
    spaces together (not allowed in a text file), no backslash (escaped only from 12.1).
  * Colour: `SDL/TYSA_COLOUR` (8001), one real field of three values 0-1, actions
    `0 0 0 0 3 5 0 0` (split: propagate, merge: keep if equal), on faces and edges. A part's
    colour (its first component's, 0-255) goes on all its faces; SolidWorks shows it as the
    part's colour, exactly (through STEP it shows them lightened).
* **Assemblies**: each part file is one `BODY`, placed by `INSTANCE`s (`part`, `assembly`,
  `next_in_part` chaining all instances of the assembly from its `sub_instance`,
  `next_of_part` chaining the instances of one part from its `ref_instance`). `TRANSFORM`:
  `rotation_matrix` = the pose's rotation row-major (x' = R x + t, checked against a
  30 deg instance SolidWorks wrote), `translation_vector` in m, scale 1, flag 1 for a
  translation + 2 for a rotation (+ 8 for a reflection). SolidWorks names the components
  after the parts (`peg-1`, `peg-2`), as it does for STEP.

## 13. Writing .SLDPRT without SolidWorks: exact bodies (2026-10-22)

`bauyrcad/core/swfile.py`; tests `tests/test_swfile.py`, `tests/test_sw_swfile.py`. Builds on
the template experiment on branch `claude/sldprt-patch-experiment` (container as a ZIP,
CRCs not checked, Biography / History mandatory, regeneration without the partition).

* **Seed**: `bauyrcad/data/sw_seeds/part.SLDPRT`, saved once by SolidWorks 2024 from a 10 mm
  cube `.x_t` (`Session.open_step` + save): tree = planes, origin, one Imported feature
  (`BaseBody`). Its body is replaced; nothing else in the tree changes.
* **Where an Imported body lives**: not in the Parasolid partition (307 bytes for a box) but in
  `Config-0-FeatureBodies/LocalBodies`: a 48-byte header of SolidWorks' (`u32 1`, CString
  `Imported1`, fixed words, then `u32 chunk + 5`, `00`, `u32 chunk`) and one ZLB chunk
  (tag, u32 uncompressed, u32 compressed, zlib, 8 zero bytes; chunk = 16 + 8 + compressed +
  8) holding a plain neutral binary `TRANSMIT FILE`. Wrong framing gives an empty part and
  BaseBody error 51 (`swSketchErrorExtRefFail`) - which looked like a rejected body.
* **The body stream**: Parasolid neutral binary (`parasolid.to_binary`): `PS\0\0`, the modeller
  string (`>h` length), the schema key (`>i` length), the user field size (`>i` 0); each node
  `>h` type, `>i` length if variable, its own index written like a pointer (`>h` index + 1,
  large ones as two shorts), the fields big-endian (int `>i`, short `>h`, byte, char,
  logical as 1 byte, double `>d`, vector 3 doubles, null int -32764, null double
  -3.14158e13); terminator `00 01 00 01`. SolidWorks opens such a stream as `.x_b` under our
  schema 12006, but an Imported body only under **its own key** `SCH_3501256_35102_13006`
  (modeller 3501256, 231 node types after the key): we keep the base schema 13006's layouts
  and flag each node type `255` ("same as base") at its first node (13006 = our 12006 tables
  except `ATTRIB_DEF.field_names`). SolidWorks' own attributes on the body (`FACE_ID_2001`,
  `BODY_MATCH`, ...) are not needed. Our encoder reproduces SolidWorks' own body stream byte
  for byte from its decoded nodes.
* **Caches dropped**: `Config-0-Partition`, `DisplayLists`, `Preview`, `PreviewPNG`,
  `Config-0-LWDATA`; SolidWorks rebuilds them from the Imported feature on opening.
* **Container**: re-packed through the central directory (`swfile.Container`): unchanged
  sections keep their bytes, the filler and decoy records are copied, the local headers,
  central directory and end record get the new sizes and offsets; with nothing replaced it
  rebuilds every test fixture byte for byte.
* **Checked in SolidWorks 2024** (only as the judge): 15 parts open with our body, keep it
  through ForceRebuild3, What's Wrong empty, `IBody2.Check3` clean, volumes as BauyrCAD, and
  SolidWorks saves them again.
* **Next**: the editable feature tree (the "add-a-feature" experiment). Assemblies: section 14.

## 14. Writing .SLDASM without SolidWorks (2026-10-23)

`swfile.write_assembly(parts, instances, path)` (`asm.export("a.SLDASM")`); tests
`tests/test_swfile.py`, `tests/test_sw_swfile.py`. Decoded by black-box comparison of seed
assemblies SolidWorks 2024 saved with one thing changed at a time (research scripts in
`projects/sldasm_diff/`, not in the repository).

* **Seeds**: `bauyrcad/data/sw_seeds/asm/seedNN.SLDASM`, N = 1..46, made once by SolidWorks
  2024 (`projects/sldasm_diff/make_asm_seeds.py`): N floating components, component i the
  dummy part `dII.SLDPRT` (a 10 mm cube) named `dII-1`, placed at t = (100 i + 12.3, 45.6,
  78.9) mm with no rotation; stored without the caches below (19-25 kB each, 1.0 MB in
  all). The hidden SolidWorks failed with "Internal application error" on NewDocument
  after a few dozen large assemblies (at N = 43, and at N = 47 in a fresh session), hence
  46. Records can't be cloned to add a component (SolidWorks rejects the file), hence one
  seed per N.
  The writer finds each component's records from these names in the seed's own bytes and
  checks the seed placement before patching it.
* **Strings**: MFC CStrings, `ff fe ff` + length in UTF-16 units (u8, or `ff` + u16) + UTF-16LE.
* **`Contents/Config-0-ModelHeader`** (mandatory) and its copy **`Header2`**: one
  `moExtObject_c` per component: CString absolute path of the referenced part, `44 80`, CString
  file stem, ...; and per component a "Created" stamp record (CString `Created`, u32 id,
  CString component name). SolidWorks shows the component name from here. Both copies are
  patched alike.
* **`Contents/Config-0`** (`moAssembly_c`): an 18-byte header, u32 L1, block 1, u32 L2,
  block 2; the component records are in block 1, so L1 changes with every size change (a
  longer name, a rotation: +72 bytes). Each component's name is there twice; 144 bytes after
  the end of the first comes its placement: u8 rotation flag, the rotation only when the flag
  is 1 (9 f64, R transposed row-major = `IMathTransform.ArrayData[:9]`), the translation (3
  f64, m) and the scale (f64 1). 86 bytes after the placement is the fixed flag (u8 1 =
  fixed).
* **`Contents/CMgr`**: the component name once more.
* **Component names**: SolidWorks names components after their files (`peg-1`, `peg-2`), so
  the writer uses `<part>-<k>` everywhere; a part used twice is two `moExtObject_c` records
  with the same path.
* **Paths**: the parts are written next to the assembly and the absolute path stored;
  SolidWorks prefers a same-named part next to the assembly over the stored path, so the
  folder can be moved as a whole.
* **Dropped** (rebuilt by SolidWorks): `DisplayLists`, `FaceTessellations/*` (incl.
  Directory), `Preview`, `PreviewPNG`, `Config-0-LWDATA`, `swXmlContents/COMPINSTANCETREE`,
  `SwDocContentMgrInfo`, `3DExperienceExchange2` + `_ConfigHeader`, `ThirdPtyStore/VisualStates`,
  `swXmlContents/KeyWords`. `Biography` / `History` stay (mandatory; they keep the seed's
  own save history).
* **Checked in SolidWorks 2024** (only as the judge): the pegboard (a plate + one peg used
  twice, one rotated 30 deg) opens as an assembly of 3 components, both pegs one part file,
  every component's transform the one SolidWorks gives the same assembly's STEP, the part
  volumes right, the plate fixed, ForceRebuild3 + What's Wrong clean, and SolidWorks saves it
  again.
* **Sub-assemblies (2026-10-26)**: in the model header (and Header2) the byte right after a
  referenced file's stem CString is its document type: 2 = part, 3 = assembly (u8, then 00);
  the assembly's own entry at the end of the header is 3 too. Read from Bauyrzhan's client v10
  assemblies (a top of 11 sub-assemblies: every sub-assembly reference 3; their part
  references 2). The component records in Config-0 and CMgr are the same for a part and a
  sub-assembly (`moUnitComponent_c`). `write_assembly` takes a part entry whose shape is the
  path of a `.SLDASM` written next to it, stores that path and stem and sets the type to 3;
  `api.export_solidworks_tree` builds the tree. Not yet checked in SolidWorks.
* **Not yet**: mates, colours (the parts are written without colour), more than 46
  components per assembly level (one more seed per count; a sub-assembly tree, STEP or `.x_t`
  have no limit).

## 15. Feature-tree feasibility: patching and grafting feature records (2026-10-24)

Question: can BauyrCAD write a part whose FeatureManager tree is real (sketches and features
with editable dimensions) without SolidWorks, by filling SolidWorks-made feature records into
a file? Method as in section 12: differential analysis of files SolidWorks saved (donors made
by the native COM export), SolidWorks 2024 only as the judge. Scripts and files:
`projects/sw_features/` (gitignored).

**Donors**: P1 = Sketch1 (rectangle 40 x 20 on Front) + Boss-Extrude1 10; P1w the same at
50 x 20 x 12; P1s = P1 + Sketch2 (circle); P2 = P1 + Sketch2 + Boss-Extrude2 20; P2b, P2t
(other values, Top plane); P3 = P2 + a cut; T3 / Q4 = triangle / square of lines + extrude;
T3B = T3 + the same Sketch2 + Boss-Extrude2 as P2.

| Experiment | Result |
|---|---|
| E0: depth 0.010 -> 0.025 and width 0.040 -> 0.050 doubles in P1's ResolvedFeatures, 23 optional sections dropped (section 12's list plus 3DExperience, PMI, KeyWords, Features, ThirdPty, VisualStates, ModelStamps) | **Pass**: opens 50 x 20 x 25 (25,000 mm³), rebuild clean, D1 edited to 30 rebuilds, saves |
| E1: what adding Sketch2 + Boss-Extrude2 changes (P1 -> P2) | ResolvedFeatures: a 4,321-byte tail appended (it overwrites the base's 4-byte end-of-list null); two 119-byte entries appended to the per-feature list near the start (offset 890, before `moSelectionSetFolder_c`); start count +1; counters at 4 (+2), 604 (+2) and one later (+1); Config-0: one 88-byte object inserted; ModelHeader / Header2: `Created` + name per feature (124 B); CMgr +102 B; feature ids continue (0x21, 0x28) |
| E1: P2's tail vs T3B's tail (same features on another base) | Same length; differ only in references, timestamps, persistent ids, coordinates and per-edge face-id counters: the records travel between parts |
| E1: computing the tail's references for the target | All 40 tag references (`0x8000 \| k`) predicted from the target file alone: a class by name through the simulated MFC tables, a tail object by the object-count difference (the simulator runs one object late) |
| E2: graft P2's records onto P1 (control), P1w and T3 | **Crash** on open (SolidWorks dies, RPC failed), first because the list entries went in the middle of the list (two entries with id 0x20), then because of untagged references |
| Bisection of the control graft against P2 (70 differing runs, applied onto P2) | One run is enough to crash SolidWorks: a plain u32 object index in a folder record (0x8d instead of 0x94), i.e. a reference written WITHOUT the MFC tag bit |
| P2 carrying the control graft's timestamps, persistent ids (`.. e5 db 6a`) and plane display sizes | **Pass**: 4 features, 9,696.46 mm³, rebuild clean. Those fields do not matter |
| E3: the same records on a base of another structure (T3) | Not judged: the base's own records hold untagged u16 / u32 object lists (Boss-Extrude1's face-id lists, `dd 00 e6 00 e8 00 ..`) whose length follows the sketch (4 entries for a rectangle, 3 for a triangle), so they cannot be found without the class layouts |
| E4: one more sketch line (T3 -> Q4) | Analysed, not judged: +464 bytes and +9 MFC objects, no strings in the line records, the line referenced from the chain and the extrude's per-face lists: the same untagged-reference problem |

**What a writer must get right.** SolidWorks crashes (no error, no message) on any wrong
integer reference, id or count. Timestamps, persistent ids and cached plane sizes may be stale.
MFC *tag* references (`0x8000 | k`) are recognisable and can be renumbered mechanically. Many
references are not tagged: plain u16 and u32 object indices inside class-specific records
(folder records, an extrude's face-id lists, sketch chains). Finding them needs the field layout
of every class involved (`moExtrusion_c`, `moICE_c`, `moProfileFeature_c`, `sgSketch`,
`sgLineHandle`, `sgArcHandle`, `moSketchChain_c`, the face-id `*IntSurfIdRep_c` classes,
`moEndSpec_c` / `moFromEndSpec_c`, ...). Each layout can be decoded by the same pairwise donors,
but the error signal is a silent crash, so each one is slow to pin down.

**Verdict.** Patching values in a SolidWorks-made tree works (E0, section 12). Grafting works
only once every reference field is known: the control graft differs from SolidWorks' own file
in integer fields only, and one untagged index is enough to crash. A general native
feature-tree writer therefore means a field-level MFC object model for every class it writes:
sketches (lines, arcs, circles, relations, dimensions) and each feature type. That is the
"XL, open-ended" level C of section 12, now with the mechanism confirmed and the blocker named.
The practical routes stay:
(a) template parts re-dimensioned by patching (level A, proven), and
(b) a generated SolidWorks macro that rebuilds the tree in the user's SolidWorks.

## 16. The feature archive, object by object (M1, 2026-10-25 / 10-28)

`bauyrcad/core/mfc.py`, tests `tests/test_mfc.py` (donors in `tests/data/sw/mfc`). Decoded from
files SolidWorks 2024 saved (the Phase 0 donor family, section 15) and checked against the
1,449 SolidWorks 2024 parts on this PC (`_MO_VERSION_17000/Biography`: `moBiography_c`, u32 2,
u32 17000, then the save date code at byte 27, `2024xxx`). No decompiling.

**The archive.** `Contents/Config-N` and `Contents/Config-N-ResolvedFeatures` are one MFC
`CArchive` cut in two: the second section starts with the u32 load-map count the first ended
at. Tags as in MFC: `FF FF <u16 schema> <u16 length> <class name>` (new class: the class takes
a map slot, then the object), `0x8000 | k` (an object of class k), `k` (a back reference to
object k, 0 = NULL), `7F FF` + u32 for big indices. All schema numbers are 1 (moFeatColorTab_c
0). The map of `Config-N` starts at **2** (`moPart_c` is class 2, `moNodeName_c` class 4, the
tag `04 80` before every feature name). Nothing says where an object ends, so every class
needs its field layout (`mfc.LAYOUTS`); a wrong one desynchronises at once (an invalid tag, a
string that is not a string, a walk that does not end with the stream) and the Config-0 map
count must equal the next section's start, which pins every object inside Config-0.

**Config-0** (P1: 25,190 bytes, 111 map entries): `moPart_c` [u32, u32, version 17000, name,
visual properties, units table, unit component, curvature, transferred plane data, hatch,
material (density parameter), model data, relation manager, environment, `suObList`, 10 views,
u8, u32, u32, colour table, sketch-block manager, `suObList` of annotation views, PMark,
thread manager, exploded-view manager].
* Units: `moUnitsTable_c` = u16 16 + 16 unit objects; each starts with 48 bytes (u32 2, u32 1,
  f64, u32 unit, u32 1, f64, u32 decimals, u32, u32, u32) and a class-sized tail; length units
  end with u16 + an object (the dual units, NULL when off); density units hold a length-units
  object behind a u8 flag.
* `uoModelData_c`: 48 bytes, u32 n + n `moAtom_c` (one per feature-ish item: P2's one extra
  Config-0 object is a second atom), u32 17000, u32 10000, u32. An atom starts with a name
  object: NULL in parts made in SolidWorks, then the name fields follow inline (7 bytes, u8
  flag, i32 id, u32, string, u32); a `moNodeName_c` in imported parts (the translator, e.g.
  `Part1 1[1]` or `Open CASCADE STEP translator 7.8 1[1]`), which holds those same fields.
* `moTransRefPlaneData_c`: 24 bytes, u8 flag, 10 f64 (the model's box) when it is 1, 146
  bytes. A part without a solid has flag 0 and a NULL curvature object (`gcCurvatureObject_c`).
* `moRelMgr_c` (the drafting standard: dimension and note styles, fonts, 17 kB) and
  `moCThreadRefMgr_c` are **not decoded**: kept as bytes up to the next class header, the
  length-units objects inside (9 in P1) counted by their class tag; the map count check covers
  them.
* Environment: name, u16, u16 n lights (`moAmbientLight_c`, 3 `moDirectionLight_c`), 47 bytes,
  string, 74 bytes. Views: `moView_c` = name, u8 rotation flag, 9 f64 when 1, 121 bytes;
  `*Normal To`, `*Front` (no rotation), `*Back` ... `*Dimetric`.
* `moPMarkRecord_c`: u32 type; type 3 (Config-0) = u32, then the data: u32 n + n sequence
  numbers, 36 bytes, a `suObList`, 4 bytes, two strings; types 7 / 10 (in extrudes) = 62 bytes.
  A part whose only feature is an Imported body (BauyrCAD's seed) has no PMark record: the
  part writes a NULL and then the data itself.
* Before the PMark, `moPart_c` holds u32, u32, u32 n + n u32 (199 in most parts), 30 bytes.

**ResolvedFeatures** = u32 start, u16 n top-level features, the features, u32 0.
* Every feature: name object (`moNodeName_c`: string, 7 bytes, u8 flag, u32 id, u32, string,
  u32), u16, **children** (u16 n + objects: an extrude's profile sketch is a back reference),
  then the common block (u16, u32, u32, u16? ... sequence number, versions and date codes
  17000 / 2024282, display flags, an 8-byte stamp, u16 2, u32 350108, u32 1).
* Folders add u32, u32 and their own tail (favourites / selection sets: an object list + u32;
  history: u16 n pairs [`moHistoryFeatItemData_c`, `moCompFeature_c`]; body folders: 12-14
  bytes, the solid one ending with a plain u16 reference). The Notes folder holds an end-tag
  folder of its own class, which points back at it: the layout branches on whether that field
  was a back reference.
* `moCompFeature_c` / `moCompRefPlane_c` (a component of another feature, e.g. the plane a sketch
  lies on): a `moUnitComponent_c`, 67 bytes, u32 17000, **u32 feature id**, u32 stamp; the plane
  adds 70 bytes.
* Planes: 154 bytes + `moDefaultRefPlnData_c` (origin, normal, u8 rotation flag + 9 f64,
  display box).
* **`sgSketch`**: u32 n points, each [u16, u32, u32 id, 64-byte entity block, u16 kind (0x1e;
  0x1f the origin point), f64 x, f64 y (m), u16 a, u16 b, two segment slots (handles or NULL:
  the segments that meet there), u16, i32 -2, 36 bytes]; three segment lists [u32 n, u16, n x
  (u32, u32 id, 64-byte block, u16 p0, u16 p1 (point indices), u32, f64 -1, u32 arc flag; arcs
  add u16 centre point, 4 x i32 -2, u16)]: lines in the first, arcs in the third; 38 bytes;
  **relations in 8 groups** by arity [u16 n, n x (28-byte prefix, u32 type, 10 bytes, arity
  handles, a group trailer: none / 40 / 6 bytes)] (types seen: 4 horizontal, 5 vertical, 17
  fixed (origin), 6-14, 16, 25-32, 47, 56, 57 two-entity, 11 and 13 three-entity); then two
  `suObList`s with an 18-word table between. Handles (`sgPointHandle`, `sgLineHandle`,
  `sgArcHandle`) are u16 index, i32 -1, u32 0.
* Sketch features (`moOriginProfileFeature_c`, `moProfileFeature_c`): the block, 14 bytes, the
  sketch, 30 bytes, u32 n + u32 + n `moSketchChain_c` (u16 n segment ids + 59 bytes), then u32
  3, u32 1, 7 bytes, u32 100000, u32 0 and the plane component.
* **`moExtrusion_c`**: the block, 48 bytes, two `moPerBodyChooserData_c` (u16 n face refs) each
  followed by a `moBBoxCenterData_c`, a PMark, then the end condition chain `moEndSpec_c` ->
  `moDisplayDistanceDim_c` -> `moFeatureDimHandle_c` -> `ParallelPlaneDistanceDim_c` ->
  `moLengthParameter_c` (**D1**: the depth f64) -> `moFavoriteHandle_c` -> `moFromEndSpec_c`.
  A face ref (`moFaceRef_c`: 36 bytes incl. the body's face count, u16, a NULL-terminated object
  list, 8 bytes, u32 17000, 8 bytes) lists the end-face id record and one
  `moFromSktEntSurfIdRep_c` per sketch segment (u16, a `moFR_c`, u32 segment id); the second
  chooser repeats them as back references. `moFR_c` = the part's `moExtObject_c` (a back
  reference after its first use: these are the "untagged" references of section 15), u32
  feature id, u32 stamp.

* The first `moBBoxCenterData_c` ends with u16 `more`, u16, u16 (+ u16 when its flag `b` is
  1): with `more` = 1 the second chooser and box follow (everything SolidWorks 2024 saved
  here); with 0, three u32 instead (parts saved by other tools: a hinge, a download).
* **`moBaseBody_c`** (an Imported body; its B-rep is in `Config-0-FeatureBodies`): the
  block, the extrude's 48 bytes, 22 bytes, a PMark, an empty string, u32 1, the body's name
  (`Imported1`), u32.

* **`moICE_c`** (Instant3D boss and cut): as the extrude, but 46 bytes (two u32-counted
  lists of (u16, u16)) and first a `moCompSolidBody_c` (the body it merges into: unit
  component, 67 bytes, u32 17000, a face ref, u32 n + n sequence numbers, the target feature's
  id, ...). Its face ids use `moEndFace3IntSurfIdRep_c` / `moFromSktEnt3IntSurfIdRep_c` (three
  ints after the face record).
* Rotations: a u8 flag + 9 f64 appears wherever a plane-like orientation is stored (default
  planes, views, annotation views, the sketch-plane component, the dimension parameter); the
  flag is 0 when the matrix is the identity of that context, so the same class has two sizes.
* `moPMarkRecord_c`: u32 sequence, u32 form; form 1 (Config-0) = 40 bytes, a `suObList`, 4
  bytes, two strings; form 0 (features) = 58 bytes.
* `moAnnotationView_c`: name, rotation, 169 bytes; Config-0 lists them in a `suObList` (`*Top`;
  a part with a Top-plane sketch adds `*Front`), followed by 4 u32 and 30 bytes of the part.

**Gates (2026-10-25, widened 2026-10-28)**
1. Byte-identical parse -> write of both sections of all 13 donors (P1, P1w, P1s, P2, P2b,
   P2t, P3, Q4, T3, T3B, H1 (a rectangle with a hole), S0 (a sketch only), I1 (an Imported
   body)) and of BauyrCAD's seed part; 11 donors are test fixtures.
2. Corpus: of the distinct SolidWorks 2024 parts on this PC, 106 use only decoded classes
   (53 before `moBaseBody_c`); **80 walk and write back byte for byte, none desynchronises
   silently**. Open, all in Config-0: 15 STEP-imported vendor parts (one folder) without the
   model-data object where every other part has it, 9 whose PMark data holds further objects,
   and parts with a material and appearance in the model data (`Steel`, `defaultplastic`).
   The writer (M4) writes Config-0 from its own template, so these do not block it. Next
   classes by corpus use: `moSimpleSurfIdRep_c` (alone would add 82 parts), `moCompFace_c`,
   `moSketchRegion_c`, sketch dimensions `sgPntPntDist` / `moSkDimHandleValG2_c`; the sketch
   and feature classes are decoded from SolidWorks-made donors in M3.
3. Differential references: P1 -> Q4 (same objects, other values) differ in no reference
   field; P1 -> P2 and T3 -> T3B, feature by feature, differ in references only by uniform
   object-count shifts (+6 after the two new history entries, +1). Value fields that move with
   the number of features are counters, not references, and a writer must update them: every
   feature's sequence word (+2 for two new features), the PMark sequence (+3 per feature) and
   one body counter in the extrude's 48 bytes (+1).

Still opaque (kept as bytes, extent proven by the next class header): `moRelMgr_c` (drafting
standard) and `moCThreadRefMgr_c` (cosmetic threads). Bench scripts: `projects/sw_features/`
(`m1.py`, `rt.py`, `corpus_walk.py`, `diffgate2.py`, `linear.py`, `pf.py` (walk any file),
`missing.py` (classes ranked by the parts they unlock), `make_m1b.py` (H1, S0, I1)).
The layout language gained `("ifobj", obj, field, values, a, b)`: a branch on a field of a
nested object read earlier.

## 17. Adding features to a tree (M2, 2026-10-28)

`bauyrcad/core/swtree.py`, tests `tests/test_swtree.py` (offline) and `tests/test_sw_swtree.py`
(SolidWorks as the judge). `transplant(base, donor, names)` copies the features `names` that
a SolidWorks-made donor has on top of the base's tree into the base archive (section 16):
objects the new features share with the donor's older features (the planes, the part's
external object, the body a boss merges into...) are paired with the base's by walking both
the same way, atoms by their feature id and history entries by the feature they name;
everything else is copied, classes shared by name. `mfc` numbers the archive afresh when it
writes, so tags and the untagged references of section 15 follow by themselves.

**Counters** (each read off the donor family and checked on P1, P2, P3, T3, T3B): with n new
features of which m are bodies,
* every renumbered sequence word (not 101) moves up by n, the new features continue the
  sequence; the environment folder's word (its data, byte 12) too, and the inline end tag of
  the Notes folder;
* atoms (newest first in the model data) are numbered 100 + k, oldest first (head byte 8);
  every atom but the newest has its u32 after the head set to 1; their next-id words take the
  donor's (a high-water mark: a larger value is safe);
* model data: byte 44 = 100 + atoms, byte 34 grows by 88 per atom (an atom's size);
  sketch-block manager byte 84 and transferred plane data byte 140: +1 per atom;
* body features (the extrude's 48 / Instant3D's 46 bytes): u16 at 16 = 58 + the body
  features from this one to the end of the tree; the atom number 100 + k at byte 28 / 32;
  PMark sequence 4 + 3 (atoms + k - 1).

**Other sections need not follow.** P2 with P1's `Config-0-ModelHeader` + `Header2`, P1's
`CMgr`, P1's `CMgrHdr2`, or all four: SolidWorks opens it with the four features (names from
the feature archive), the right volume, a clean rebuild, an editable D1, and saves it.
Without `CMgr` at all it crashes. So the writer keeps the base's headers and configuration
manager and drops the geometry caches and indexes (`swtree.REGENERATED`).

**Gate (SolidWorks 2024, private hidden session)**: Sketch2 + Boss-Extrude2 of P2 transplanted
onto P1 (G0), onto P1w (50 x 20 x 12, G1) and onto the triangle T3 with the circle moved to
the corner (30, 0) (G2, a combination SolidWorks never saved): all open with errors 0, the
four features, volumes 9,696.46 / 12,904.78 (BauyrCAD's own value) / **5,120.575 mm³ (=
SolidWorks' T3B)**, rebuild clean, D1 of Boss-Extrude2 edits (G2 at 30 mm: +π·6²·10), SaveAs
works. G0's archive equals SolidWorks' P2 except timestamps, persistent ids (`.. e5 db 6a`,
`moExtObject_c`) and plane display sizes; G2's equals T3B except those and the new boss's
face-naming records (which sketch segment of the base a face came from; stale values are
recomputed on rebuild).

**Limits**: the donor must hold the base's features under the same names; new features
that need other donor features (a boss without its sketch) are refused; feature ids must not
clash (the donor's are kept). Next (M3): sketch entities, relations and dimensions written
from BauyrCAD's sketches rather than moved in a donor.

## 18. Sketch profiles written from geometry (M3, part 1, 2026-10-28)

`swtree.set_profile(arc, sketch_feature, loops, hv=True)` replaces a sketch's entities by
closed loops (outer first, then holes) of `("line", p, q)`, `("arc", centre, p, q)` (counter-
clockwise p -> q) and `("circle", centre, r)`, in mm on the sketch plane. Read off SolidWorks'
own sketches of a triangle, square, pentagon, hexagon, a D shape (line + arc), a circle and a
rectangle with a round hole:

* **Points** (`sgSketch` point list), ids 1..n: a vertex where two segments meet is kind 0x1e,
  `a` 0, `b` 2, `c` 0 and names both segments (handles, lines before arcs, lower index
  first); a circle's one point has `a` 2 and names only the arc; an arc centre has `b` 1,
  `c` 1 and no segments. x, y in metres.
* **Segments**: lines in the first list, arcs (and circles: start = end point) in the third,
  each list numbered from 1; p0 / p1 index the point list, an arc adds its centre point. The
  first word is the segment's id in the profile chain (1.. across lines and arcs; 0 when no
  feature uses the sketch); byte 17 of the 64-byte block counts the relations on it.
* **Counters**: the sketch's u32 after its relation-free block (`_29`) = segments + relations
  + 1; the 18-word table starts with the next chain id, point id, line id and arc id.
* **Chains** (`moSketchChain_c`, one per loop, holes first): the loop's segment chain ids and a
  flag word 1 (outer) or 0x10001 (hole); SolidWorks' order inside a chain is its own and need
  not be followed.
* **Relations**: horizontal (4) and vertical (5) on one line each, in group 1 (28-byte prefix
  with -1 at byte 8, type, 2, 0, 0, 0xfffe, 0, the line handle) - what SolidWorks infers for
  axis-aligned lines.

Checks: P1's rectangle written again from its lines equals SolidWorks' archive but for the
chain order; the hexagon and D shape match SolidWorks' sketches entity by entity, counters and
relations included. In SolidWorks 2024, P1 with its rectangle replaced by a hexagon, a slot (2
lines + 2 arcs), a D shape, a triangle and a rectangle with a d8 hole opens with errors 0,
volumes exactly BauyrCAD's (5,845.6715 / 3,785.3982 / 1,570.7963 / 3,000 / 7,497.3452 mm³),
a clean rebuild, D1 editable, and saves. The extrude's face-naming records may stay stale
(they name four segments for a hexagon): SolidWorks recomputes them.

Open (M3, part 2): sketch dimensions. A dimensioned line adds an `sgPntPntDist` (a
`moLengthParameter_c` "D1" in a shorter sketch form, the two point handles) counted by a u16
inside what section 16 calls the sketch's 38 bytes, and a `moDisplayDistanceDim_c` (with a
`moSkDimHandleValG2_c`) in the sketch's first `suObList`; a circle's diameter brings
`sgCircleDim`, `sgEntHandle`, `moDisplayRadialDim_c`, `moSkDimHandleRadial_c`. SolidWorks'
slot donor (tangent relations) does not walk yet.

## 19. Driving sketch dimensions (M3, part 2, 2026-11-01)

`swtree.add_dimension(arc, feature, "length" | "diameter", index)` adds a driving dimension
at the entity's current size. `set_profile(..., dims=[("length", 0), ...])` replaces geometry
and dimensions together. Indices are zero-based within the line or arc list; diameters
require full circles. R1/R2/C0/C1 are now regression fixtures; R2 and C1 also supply bundled
annotation templates (`data/sw_seeds/sketch_linear.SLDPRT`, `sketch_radial.SLDPRT`).

Corrections to the initial M3 handoff, established from the donors:

* The sketch's old 38-byte block is 30 bytes, a u16-counted radial dimension list, a
  u16-counted point-to-point dimension list, then 4 bytes. The 30-byte prefix is still
  undecoded; only the two demonstrated dimension groups are writable.
* `sgPntPntDist`: u32, length parameter, u32 type (1), two u32, two point handles,
  34 bytes. `sgCircleDim`: u32, length parameter, type (15 for diameter), two u32,
  arc handle, `sgEntHandle` (10 bytes), 36 bytes.
* `moLengthParameter_c` is short in **both** cases: name object, f64 value in metres,
  3 bytes, u32 parameter id, 16 bytes. The apparent long feature-parameter form included
  its containing display handle's fields and the extrude end-spec's tail. These have been
  moved into their containing layouts. Parameter names have id -1, seven zero prefix
  bytes and a u16 suffix; normal node names have a u32 suffix. The Imported atom also has
  id -1 but a nonzero prefix. The layout language now supports `ifall` for that distinction.
* Display dimensions live in `sgSketch.list1`. The sketch handle's 107-byte prefix is
  followed by a **ref16 to the sketch plus a u16 dimension index**, not ref32 to a dimension.
  R2 proves the split: interpreting the second handle as ref32 yields 65723, an unresolved
  reference. The display then has style/value data and a favourite; radial handles add
  52 bytes, a string, and 18 bytes.
* Dimensions increment the referenced points' (length) or arc's (diameter) byte 17 in its
  entity block. They do **not** change the sketch's segment/relation counter or 18-word
  table. Each sketch's display dimensions share an annotation id (u32 at display byte 155).
  New ids are allocated above existing ids; existing feature ids need not move. Adding an
  annotation view, renumbering the colour table and copying saved annotation positions
  exactly are unnecessary for the demonstrated open/rebuild gates.

Gates: all 19 bench donors round-trip both streams byte for byte; the corpus retains
80 byte-identical successes out of 106 files whose classes are decoded (same known failures
as before). Offline tests compare the dimensions, entity references, values and counters
against R1/R2/C1 and check reference renumbering, duplicate rejection and profile replacement.
SolidWorks 2024 opened generated rectangle and circle parts with errors/warnings 0 and clean
rebuilds. Editing `D1@Sketch1` from 40 to 50 mm changed the rectangle's volume from 8,000
to 10,000 mm³; editing diameter 12 to 50 mm changed the circle's volume from 1,130.9734
to 19,634.9541 mm³. Follow-up automated checks cover the second length, mixed dimensions,
and save/reopen, but were initially skipped because the process guard detected an open
SolidWorks window; that session was left untouched.
Full suite: **504 passed, 97 skipped** (332.74 seconds); the skips include the guarded
SolidWorks tests. No override of the running-session guard was used.

Limits: only line length and full-circle diameter, at current geometry size; annotation
positions remain those of the donor style template. The pending M3 integration checks
subsequently passed during M4 validation, including second-length edits, mixed dimensions
and save/reopen.

## 20. Offline feature writer v1 (M4, 2026-11-01)

`core/swfeatures.py` maps a BauyrCAD document to native sketch/extrude pairs. The API is
`Part.export_solidworks_offline`, core `swfile.write_part(..., features=True)`, CLI
`export --features [--require-native]`, and the GUI feature-tree export format. The normal
shape-only writer remains available. Unsupported design intent produces a reported
whole-part Imported fallback; strict mode raises without overwriting the destination.

The 19 SolidWorks-saved templates in `data/sw_seeds/native` cover an empty part, first
bosses on Front/Top/Right (blind, reversed, mid-plane), added bosses in those modes, and
cuts (blind, reversed, mid-plane, through-all in either direction, through-all both).
`projects/sw_features/make_m4.py` creates them through the existing COM exporter, only
while SolidWorks is idle. All 19 archives round-trip byte for byte with existing layouts.
Through-all's `moEndSpec_c.dim` is NULL; it has no depth dimension.

The empty template supplies the outer container. The first-boss archive initializes the
document settings and counters that SolidWorks changes on creating the first body; this
is deliberate rather than applying the incremental M2 counter rules to an empty document
whose feature sequences are all 101. The output drops geometry caches as in M3. Subsequent
pairs copy a donor's final sketch and extrusion, add their history entries and model atom,
allocate feature/parameter ids above all existing ids, remap feature-id references and
the shared external owner, and update M2 counters. Plane components come from the matching
standard-plane donor; geometry and dimensions come from the BauyrCAD sketch. Depth updates
the parameter and display values. Symmetric modes normalize reverse to false.

**Important failure found and fixed:** `moUnitComponent_c` is context-specific. Collapsing
every occurrence to the part's units object corrupts the added body's merge component and
crashes SolidWorks. Preserve the plane/body component data; only the external owner is
globally shared. There is a regression test for this distinction.

The heuristic reader misclassified a repeated cut in a generated tree. `feature_tree`
now takes exact class identities from `mfc.load_archive` whenever supported, retaining
the previous heuristic path for other archives.

Validation: 106 focused offline tests passed. All 27 private-SolidWorks integration checks
(M4 + M3) passed: standard-plane orientations, reverse and symmetric extrusion modes,
through-all cuts, cross-plane bosses, volume and bounding-box agreement, dimension edits,
clean rebuilds and save/reopen. For a generated 50 x 30 x 12 base, overlapping d20 boss to
25 mm, and d8 cut, volume was 22,217.9065 mm³ in both applications.

Supported v1 scope is one connected solid, independent sketch/extrude pairs, one outer
profile with optional holes, lines/CCW arcs/circles, H/V relations and M3 dimensions.
Model-face/custom planes, standalone/shared sketches, construction geometry, arbitrary
constraints, selected contours and non-extrude features fall back explicitly. Annotation
positions still use donor templates. Future families belong to M5.

Final M4 full-suite validation (2026-11-01): **647 passed, no skips**, in 1095.43 s,
including the guarded SolidWorks integration tests.

## 21. Automatic annotation positions and M5 revolve (2026-11-01)

Moving one annotation in SW-saved R2/C1 donors isolates the position fields. In both
display classes, `_2[0:24]` contains XYZ doubles. The sketch handle's `_7[0:24]` holds
the leader/extension direction. For a length, doubles at 24 and 32 are the fraction along
the line and perpendicular offset. For a diameter they are distance/radius and
distance-minus-radius; the double at 40 is the diameter. Coordinates are in metres in the
sketch frame; SolidWorks maps them to the plane. `place_dimensions` places labels outside
the profile bounds and separates nearby labels. Positions were read back correctly on
Front, Top and Right for a translated rectangle with length/height/hole diameter. Offline
checks also cover scaling, diagonals, deterministic recomputation and round-trips. A
preview of stored positions was inspected: `out/automatic_dimensions.png` on the bench.
Automated position read-back and save/reopen placement gates pass on all three planes.

M5 first family is revolve, not all remaining feature families. Fifteen SW-saved donors
cover first bosses on three standard planes, added bosses and cuts, each blind/reverse/
mid-plane. `moRevolution_c` and `moRevCut_c` share the layout, with a merge flag after the
first 18 body-counter bytes: first bodies have a 26-byte tail, added bodies have 24 bytes
and a merge component. The axis is `moLineRef_w` -> `moCompSketchEntHandle_c` -> line handle.
The word after the axis's 12-byte block is the **sketch sequence**, not an MFC reference:
118 for the first sketch and 124 for a second. Renumber it with the target sketch.

`moRevEndSpec_c` holds `moDisplayAngularDim_c` -> `moFeatureDimHandle_c` -> `AngleDim_c` ->
`moAngleParameter_c`. Its parameter is radians. The feature handle has shorter geometry and
different placement bytes for AngleDim; the layout branches on the nested object's runtime
class (`ifobj` with `$class`). The sketch's old final zero u32 was actually a u16 followed
by a nullable axis handle. Construction axes make that distinction observable.

The writer appends a construction centerline outside the closed profile chains, allocates
angle-parameter IDs with length-parameter IDs, remaps the axis's feature ID and sequence,
and uses mode donors for the angle end condition. Current scope is one centerline in the
profile sketch; other axis forms still fall back. All 15 donors round-trip exactly;
146 focused offline tests pass, including exact body-donor model-header round-trips.
The corpus retains all 80 baseline round-trips and adds one more; the same 26 previously
unsupported variants fail, with no new failures.

Two failures found by the SolidWorks gates are fixed:

* The construction marker is in the preceding serialized record: high word `2` in the
  prior line's final `arc` word, or `2` in the line-list prefix if the centerline is the
  first line. The centerline's style alone is insufficient. Omitting the marker gives
  a cleanly opened file but revolve error 51; restoring it fixes the same generated sketch.
* The empty-part model header cannot resolve revolve axes. Decode `moHeader_c`,
  `su_CStringArray`, `moLogs_c` and `moStamp_c`; initialize both `Header2` and
  `Config-0-ModelHeader` from the first-body donor and maintain feature ID/name logs.
  Crucially, the referenced sketch's log timestamp must equal the axis component's
  creation stamp. Merely adding ID/name records still gives error 51 for an added revolve.
  Header-only substitutions and stamp synchronization isolate this requirement. This
  qualifies the earlier M4 finding that only the two feature archive streams must change.

**56 targeted SolidWorks checks pass**: 26 revolve cases, 24 extrusion/annotation cases,
and six M2/M3 checks. Coverage includes standard planes, full/partial/reverse/mid-plane,
added bosses/cuts, diagonal/translated/reversed axes, named sketches, circular profiles
with driving diameters, mixed revolve/extrude stacks, angle edits and save/reopen.

The curved-intersection mixed-stack gate uses independently integrated exported STEP
geometry at the same `1e-6` relative volume tolerance. SW's reported mass differs between
the offline-generated and independently COM-created reference despite matching independent
measurements of their exported geometry (49,203.4582 vs 49,203.6652 mm³). Both STEP solids integrate to the same
49,203.6540 mm³; independent analytical quadrature and accurate source integration give
49,203.6649 mm³. The source kernel's default fixed-order volume is also inaccurate here
(49,204.1204). The test checks analytical source volume and the regenerated B-rep before
and after save/reopen; it does not loosen tolerance to hide a construction error. Circular
revolves use analytic torus bounds because OCC's conservative box overestimates the surface.

Final normal full-suite gate: **739 passed, no skips**, in 1013.22 s (16:53), including
all private SolidWorks integration tests.

## Sources

* PRONOM fmt/1967 (SolidWorks 2015+ signature): https://pronom.nationalarchives.gov.uk/fmt/1967.xml
* blussyya/sldprt-format-research (container, display lists, XT reader; MIT): https://github.com/blussyya/sldprt-format-research
* cadmpeg SLDPRT specification (clean-room; CC BY 4.0): https://github.com/cadmpeg/cadmpeg
* XRTC5/sldprt-export (Python, display mesh; MIT): https://github.com/XRTC5/sldprt-export
* schwitters/openswx (metadata, previews; MIT): https://github.com/schwitters/openswx
* parasolid-kit (XT reader, OCCT STEP; MIT/Apache-2.0): https://github.com/monozukuri-ai/parasolid-kit
* Parasolid XT Format Reference (Oct 2006; schema 12006), public mirror: https://web.archive.org/web/20230806012108/http://www.13thmonkey.org/documentation/CAD/Parasolid-XT-format-reference.pdf
* IANA, Parasolid transmit media type: https://www.iana.org/assignments/media-types/model/vnd.parasolid.transmit.binary
* HOOPS Exchange SolidWorks reader (Parasolid inside SLDPRT): https://docs.techsoft3d.com/hoops/exchange/start/format/solidworks_reader.html
* OCCT Parasolid reader is commercial: https://github.com/Open-Cascade-SAS/OCCT/discussions/1210
* CJEU SAS v WPL summary: https://www.scl.org/2436-sas-v-wpl-no-copyright-says-ecj/
* 17 USC 1201: https://www.law.cornell.edu/uscode/text/17/1201

## 22. M5 finishing features: fillet and chamfer

Sixteen SW2024 reference parts cover one vertical/top/bottom edge, two edges, oblique
profiles, and equal-, two-distance and angle-distance chamfers. Their Config-0 and ResolvedFeatures streams
round-trip byte for byte. The production writer (`core/swedges.py`) gates constant-radius
fillets and equal-distance chamfers after one forward blind polygonal extrusion on a
standard plane. Multiple edges and face selections are supported. Other histories,
curved profiles, holes, variable radii and asymmetric chamfers explicitly fall back.
The latter two chamfer donors establish decoding only, not an export support claim.

An edge reference contains two incident generating surfaces plus its two endpoint
surfaces. A vertical extrusion edge names the outgoing and preceding sketch segments
and the top/bottom faces. A cap edge names its cap and sketch segment, then the predecessor
and successor segments. The writer resolves the selected OCC edges on the pre-finish body,
projects their endpoints into the sketch frame, and maps them to those generating identities.
It never exports an OCC edge index as a native reference. Face selections first expand to
edges using the ordinary feature resolver. A multi-edge feature combines one surface and
selection/component record per edge; fillets also have face/edge pairs and per-edge values,
with a single driving radius dimension.

Critical layout details:

- `moSurfaceIdRep_c` and `moFilletSurfIdRep_c` begin with a **plain ref16 to the finishing
  feature**, not a scalar. Their following `moFR_c` carries the feature ID and creation
  stamp. Treating ref16 as an integer happens to work for unchanged rectangle archives,
  but crashes SolidWorks when relation/dimension objects shift the map. Copied per-edge
  records must point to the actual output feature, not the discarded copy owner.
- `moEdgeRef_c` has the face-reference layout with a **4-byte final tail**, versus 8 bytes
  for `moFaceRef_c`. Single-edge zero padding hides this; multi-edge donors disambiguate it.
- `moCompEdge_c` has component, 67 bytes, version, byte, edge object, and 62 bytes.
  `moCompFace_c` ends with its face object and a u16. Fillet face lists contain pairs of
  component-face and component-edge objects.
- `ThreeDRadiusDim_c` contains the driving length parameter. Its dimension handle has
  76 bytes of geometry and 41 of placement (angular handles use 76/43, linear 124/41).
- Finishing-feature prefixes have 62 bytes and differ from extrusion/revolve counters.
  This implementation reuses the full three-feature donor; it does not claim arbitrary
  finishing-feature appends through `swtree.renumber`.

The empty outer container and fresh feature streams omit cached donor solids. Body-donor
model headers are updated with the output names. SolidWorks is used only for reference
creation and validation, never by the writer itself. Gates cover native tree/type identity,
forced rebuilds, volume, bounds and centre of mass, driving size/depth edits, renamed and
dimensioned profiles, face selections, nonrectangular profiles, and save/reopen.

Equal-distance chamfers must use the distance-distance mode with its equal-distance bit
(`2 | 16 = 18`). A distance/45-degree donor has equal setbacks only on perpendicular
faces; it fails volume and first-moment checks on oblique polygon corners. The COM exporter
now uses this combination too. See the official [chamfer type enumeration](https://help.solidworks.com/2024/english/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swChamferType_e.html)
and [InsertFeatureChamfer parameters](https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IFeatureManager~InsertFeatureChamfer.html).
The `FC_chamfer_equal_*` donors carry one driving distance, no angular dimension, and a
null second-distance slot. The original `FC_chamfer_vertical/top/bottom/two` files are
45-degree donors retained to test decoding; they are not used for equal-distance output.

## 23. Face-attached sketch planes (SW2024)

A model-face sketch has a variable-length geometry cache and boundary-edge references
between its chains and its plane. The standard-plane form is the empty-cache variant.
The writer emits the empty cache; SolidWorks reconstructs it from the parent feature.

`moCompRefPlane_c` with id `0xffffffff` contains an anonymous `moRefPlane_c`, followed
by a shorter component transform. The anonymous plane contains the NodeName payload
without the name string/object. Its data class is `moFaceRefPlnData_c`: the default-plane
frame prefix, a `moCompFace_c`, a 220-byte selection record, and a second optional frame.
The selection record's u32 at offset 8 is the generating feature sequence number.

The side-face reference names the generating sketch segment, the two end faces and the
adjacent segments. `swfaceplanes.py` maps these onto the CCW-normalized base profile,
sets both frames in metres, and shares the destination archive's external owner object.
Copying an unrelated owner produces sketch error 51 even when the visible geometry is
correct. The parent feature creation stamp must also match the model-header registry.
No cached donor B-rep is used.

The second sketch can lie on an unambiguous planar side or end cap of a polygonal,
hole-free base extrusion on Front/Top/Right. Blind/reversed/mid-plane bases, forward
blind/mid-plane face bosses and blind/through-all/both-directions face cuts are supported.
Longer histories, reverse blind face bosses, mid-plane face cuts and face revolves remain
explicit rejection. The
bundled `FP_side` reference archive round-trips byte-identically. Integration gates cover
rectangular and slanted faces on all three base planes, parent dimension edits, geometry,
clean rebuild and save/reopen. The malformed-layout test truncates two chain fields:
removing only the final field can now be reinterpreted as a different cache variant.


End-cap references start with `moEndFaceSurfIdRep_c`, followed by one generating segment
per boundary edge and NULL. Its `_2` is 0 at the extrusion start and 1 at the end, not
necessarily physical bottom/top: a reversed extrusion's negative-side cap is 1. Mid-plane
bases use 0 for the negative half and 1 for the positive half. Cap boundary arrays are
rebuilt for the actual polygon size; cloned boundary records must retain the shared
external owner. Optional through-all depth is zero in BauyrCAD and must not be validated
as a positive driving depth, since no depth dimension is emitted for that end condition.

## 24. Sketch construction, relations and radius dimensions

`swsketch.py` maps source entity identities to native line/arc/point handles after profile
ordering. Shared source points are merged; merely coincident coordinates are not. Explicit
profile point-point coincidence uses shared native points. Remaining relations are written
from saved `SI_*` templates: H/V 4/5, tangent 6, parallel 7, perpendicular 8, coincidence 9,
concentric 10, midpoint 12 and equal 14. Tangency orders arc then line; midpoint orders line
then point. All source relations are preflighted before output, including in finishing parts.

Construction lines use marker 2 in the preceding segment's high arc word, or the line-list
prefix when first. Construction circles use chain 0 and their saved style. Standalone
points use flag `a=1`, circumference seam points `a=2`. Point incidence counters include
construction segments and arc centers. Sketch table counters and relation counts are
rebuilt after remapping; source H/V intent replaces automatic geometry-derived relations.

Radius and diameter share `sgCircleDim`, with type 3 and 15 respectively. Radius uses an
arc or circle handle and a length parameter equal to r; diameter uses 2r and requires a
full circle. The radius annotation template is `SI_radius_arc.SLDPRT`. Automatic placement
uses geometric r for either type. Dimensions are appended after point remapping, so line
endpoint and construction-circle references remain correct.

Gates include exact donor archive round trips, native relation type/count checks, geometry
on Front/Top/Right, body-depth edits, circle/semicircle/slot radius edits, construction-circle
diameter edits and save/reopen. Fixed points, external origin anchoring and equations are
explicitly unsupported rather than silently baked into coordinates.

## 25. Native reference-plane dependencies

`moCompRefPlane_c` ends after its component reference (and optional inline anonymous
plane). The sketch transform/display tail belongs to `moProfileFeature_c` or
`moOriginProfileFeature_c`, now named `plane_tail`. Moving that tail out of the component
preserves all existing templates byte-for-byte and lets other features reference planes.

An offset `moRefPlane_c` uses `moFaceRefPlnData_c` with a `moCompRefPlane_c` parent. Its
attachment differs from a model-face attachment: a 98-byte selection prefix, parent
origin/normal, optional rotation matrix, parent translation, display suffix, native
`ParallelPlaneDistanceDim_c` through its display object, and 16 trailing bytes. Prefix
u32 at offset 2 is 3/2/1 for Front/Top/Right or 0 for a custom plane; offset 14 stores the
preceding feature sequence. Parent-frame data is essential: patching only the resulting
plane frame initially produced a Front-oriented body for a Top-plane source.

Midpoint/coincident planes use `moConstraintMidPlaneRefplaneData_c` and
`moConstraintCoincPlaneRefplaneData_c`, sharing the default frame prefix and a constraint
interface object. The interface holds three geometry slots. Midplane has two
`moRefPlaneMidPlaneGeom_c` references and an empty coincidence slot; coincident planes
have one `moRefPlaneCoincidentGeom_c` reference. `moPlaneRef_w` wraps the parent component
and its selection/frame data. These preserve parent constraints rather than fixed positions.

`swreferenceplanes.py` first builds supported body pairs into a temporary archive, inserts
reference features/history with fresh IDs, restores source feature order, remaps sequence
numbers and creation stamps, and reconnects each sketch with its own frame. Both model
header registries are updated. Unsupported input is rejected before replacing the user's
output. Production never uses SolidWorks. Tests check native parent names, plane position
and normal, distance edits, chained/midpoint/coincident dependency updates, body geometry
and save/reopen, including mixed extrusion/revolve histories and unused planes.

## 26. Shared sketches and additional relation semantics

`swsharedsketches.py` normalizes consuming features into temporary independent pairs,
then replaces duplicate profile objects with the original object in both archive graphs.
It removes duplicate history entries, restores source tree order, reconnects entity-handle
feature IDs and updates revolve sketch sequence numbers. All axes referring to a shared
sketch must use one creation stamp; conflicting donor stamps cause rebuild error 51.
Header registries are rebuilt after the graph is complete. Unsupported standalone sketches
and shared face sketches are rejected before the destination is replaced.

Additional native relation codes: point-pair horizontal 25, vertical 26, collinear 27,
point symmetry 11 and Fix 17. Circle equality uses the same code 14 and arc handles as
arc equality; point-on-line uses code 9 with line then point. Symmetry uses two point
handles then its axis line. Fix on a supporting line/circle does not fix a trimmed
segment's endpoints: preserve BauyrCAD line/arc fixation with two additional point Fix
relations. Stored point.fixed flags also become point Fix relations. Gates check fully
defined state after save/reopen, not just relation counts or initial geometry.

The sketch's former raw36 `_29` block includes a nullable object after its first 12 bytes,
followed by 22 bytes. Collinear reference files can hold `moExtensionLineIndicator_c`
there (entity object plus four bytes). Splitting the field preserves exact round-trips
without swallowing a class definition into opaque bytes.


## 27. Origin anchors and clockwise sketch arcs

Native sketch points can contain an external-entity object after byte 35 of the common
point block; the remaining 27 bytes follow it. A null reference accounts for the old
64-byte block. Origin anchors use sgExtEnt_c, moSketchExtRef_w and a component sketch
entity handle pointing to the origin feature. The model-data point-record count uses the
low 28 bits of its word; origin references add a 32-byte persistent record with a unique ID.
Creation stamps must agree with the model headers. Shared-sketch collapsing removes only
orphaned records belonging to collapsed duplicates. See sworigin.py and the origin tests.
Origin projection on a model-face sketch is deliberately unsupported until its dependency
semantics are validated.

Arc segment flags are 1 for counter-clockwise and 0xffffffff for clockwise, both followed
by the same arc payload. Contour traversal orientation and the source sketch's normalized
arc endpoints are distinct: matching must account for orientation, without confusing two
complementary arcs sharing the same center and radius. Concave-notch radius edits pass
on all three standard planes, including save/reopen.

## 28. Shell research: decompilation checked against native files

Targeted static analysis is now authorized for format research. REA/Ghidra inspection of
the shell serializer supports the following Shell-specific order after the body-feature
prefix: removed-face list, thickness display dimension, one-byte outward flag, per-face
thickness selections, their dimension list, component-edge reference and component-body
reference. The last reference was previously mistaken for an unconditional two-byte zero
because all initial samples were null. Older internal file versions omit some fields;
a reference-history helper can handle face serialization through another path. These
variants remain research findings, not general production support.

The thickness-list initializer creates display dimensions numbered from D2 in face-list
order. Post-load handling trims unmatched tail selections when the dimension list is
shorter, so keeping both lists consistent is essential. Controlled references with one
alternate wall, two alternate walls and reversed selection order confirm these identities.
Creating valid multi-thickness reference features uses marked removed/alternate faces,
InsertFeatureShellAddThickness for each alternate face and then InsertFeatureShell.
Assigning a face list alone did not create usable thickness dimensions in the tested API path.

Research prototypes use new 72 x 44 x 25 geometry, 2.5 mm default thickness, and alternate
3.5/4.5 mm walls. Nine files (three standard planes, one/two/reordered selections) passed
clean rebuilds, alternate-thickness edits, parent-width edits, default-thickness edits,
analytical volume and centroid checks, and save/reopen. A fixed lower-left sketch point
makes width edits deterministic. These files are written without COM or cached solids;
SolidWorks only created the reference files and judged the results. Variable-thickness
shells are not yet exposed in BauyrCAD's production modelling API or offline exporter.

### Constant-thickness production subset (2026-11-02)

`core/swshell.py` writes one polygonal forward extrusion followed by a constant-thickness
Shell. Bundled `SH_open` and `SH_closed` archives supply records only; cached donor solids
are dropped. Standard planes, profile geometry/relations/dimensions, extrusion depth,
thickness, direction and names come from the BauyrCAD document. One removed end cap uses
`moEndFaceSurfIdRep_c` cap 0/1 and a boundary reference for each generating sketch segment.
Each copied boundary owns a separate `moFR_c`; sharing those objects produced a file that
rebuilt initially but crashed after SolidWorks saved and reopened it. The external owner
remains shared. Both initial and edited save/reopen are therefore mandatory gates.

The non-angle `moFeatureDimHandle_c.geometry` block consists of 72 bytes, a u16 vector
flag, optional 48 vector bytes and a trailing u16. This decodes both the previous compact
radius form and Shell dimensions without changing the angle layout. The final Shell field
is a nullable object, not an unconditionally zero u16. Both bundled references round-trip
exactly. Variable-thickness lists are decoded but not populated by the production writer.

Planar BauyrCAD shells now prefer intersection joins so outward corners match SolidWorks;
previous arc joins rounded them and failed the independent volume gate. Curved-body shell
modelling retains its existing arc-first behavior. The native subset excludes curved or
holed profiles, side openings, multiple removed faces and longer histories.
