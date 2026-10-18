"""Portable assembly packages containing saved part files and current assembly state."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from .assembly import AssemblyError


def pack_and_go(assembly, path):
    """Write a ZIP with relative references, without saving or changing the source.

    Includes all directly referenced saved parts (including repeated/hidden parts).
    Assembly poses, mates and flags use current in-memory state. Unsaved edits in
    separately open part windows must be saved first. Scripts/drawings are not included.
    """
    path = Path(path).resolve()
    if path.suffix.lower() != ".zip":
        raise AssemblyError("Pack and Go destination must be a .zip file")
    if not assembly.components:
        raise AssemblyError("The assembly is empty")
    data = assembly.to_dict()
    sources, members = {}, {}
    for component, item in zip(assembly.components, data['components']):
        source = component.resolve_path(assembly.base_dir).resolve()
        if not source.is_file():
            raise AssemblyError(f"{component.name}: referenced part is missing: {source.name}. Restore it before packaging.")
        if source.suffix.lower() not in ('.bcad', '.step', '.stp', '.iges', '.igs', '.brep'):
            raise AssemblyError(f"Unsupported component file: {source.name}")
        if source == path:
            raise AssemblyError("The package cannot replace a referenced part")
        key = os.path.normcase(str(source))
        if key not in sources:
            member = f"parts/part_{len(sources) + 1:04d}{source.suffix.lower()}"
            sources[key] = (source, member)
        item['source'] = sources[key][1]
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as f:
            temp = f.name
        with zipfile.ZipFile(temp, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for source, member in sources.values():
                # The checksum describes the exact bytes included, even if a file
                # is replaced by another application during packaging.
                content = source.read_bytes()
                archive.writestr(member, content)
                members[member] = {'original_name': source.name, 'sha256': hashlib.sha256(content).hexdigest()}
            archive.writestr('assembly.bcasm', json.dumps(data, indent=1))
            archive.writestr('manifest.json', json.dumps({'format': 'bauyrcad-package', 'version': 1,
                              'assembly': 'assembly.bcasm', 'parts': members}, indent=2))
            archive.writestr('README.txt', 'Extract the whole ZIP into a folder, then open assembly.bcasm in BauyrCAD.\n'
                             'Keep the parts folder beside it. Component positions and mates are preserved.\n'
                             'Parts are saved files; save separately open part edits before packaging.\n'
                             'Scripts, drawing files and native SolidWorks files are not included.\n')
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            os.unlink(temp)
    return {'path': str(path), 'components': len(assembly.components), 'parts': len(sources),
            'assembly': 'assembly.bcasm'}
