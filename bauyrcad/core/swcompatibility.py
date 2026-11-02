"""Offline export preflight using the production writer on an isolated document."""
from pathlib import Path
import tempfile

from .document import Document
from . import swfeatures


NATIVE_TYPES = {'sketch', 'extrude', 'revolve', 'ref_plane', 'fillet', 'chamfer', 'shell'}


def _advice(reason):
    text = reason.lower()
    if 'shell' in text or 'end-cap' in text:
        return ('Supported Shell export uses one polygonal forward blind extrusion, followed by '
                'a constant-thickness closed shell or a single end-cap opening. Other histories '
                'need export using installed SolidWorks, or explicit solid-body export.')
    if 'finishing' in text or 'chamfer' in text or 'fillet' in text:
        return ('Use one forward blind polygonal extrusion followed by a constant-radius fillet '
                'or equal-distance chamfer. Other finishing histories need installed SolidWorks '
                'or explicit solid-body export.')
    if 'plane' in text or 'face' in text:
        return ('Use a standard Front/Top/Right plane or a supported offset/coincident/midpoint '
                'plane. Face supports depend on the preceding extrusion and feature mode.')
    if 'contour' in text:
        return 'Use a separate sketch containing only the intended closed profile.'
    if 'suppressed' in text or 'failed' in text:
        return 'Resolve the failed feature or restore the suppressed feature before exporting.'
    if 'parameter' in text or 'equation' in text:
        return 'Offline export cannot yet preserve equations; use the installed SolidWorks route or export a solid body.'
    if 'name' in text:
        return 'Use unique feature names (ignoring case), without @ or reserved SolidWorks names.'
    if 'sketch' in text or 'constraint' in text or 'relation' in text:
        return 'Review the named sketch and its relations. Unsupported design intent is not silently removed.'
    return 'Use export with installed SolidWorks, or explicitly choose solid-body export (no editable history).'


def check(document):
    """Return JSON-friendly readiness, feature rows and actionable blocking issues.

    This is an offline writer check, not a SolidWorks rebuild guarantee. No user file
    is written and the live document is never regenerated or changed. Rows are marked
    ready only when the complete history passes the production writer.
    """
    rows = [dict(id=f.id, name=f.name, type=f.TYPE, status='not_checked') for f in document.features]
    issues = []

    def issue(reason, feature=None):
        if any(item['reason'] == reason for item in issues):
            return
        issues.append(dict(feature_id=feature.id if feature else None,
                           feature=feature.name if feature else None,
                           reason=reason, suggestion=_advice(reason)))
        if feature:
            next(row for row in rows if row['id'] == feature.id)['status'] = 'blocked'

    for feature in document.features:
        if feature.TYPE not in NATIVE_TYPES:
            issue(f'{feature.name}: {feature.TYPE} is not yet supported by the offline native writer', feature)
        if feature.suppressed or feature.error:
            issue(f'{feature.name}: suppressed or failed feature', feature)
    if document.parameters or document.parameter_bindings:
        issue('Named parameters and equations are not yet preserved by the offline writer')
    try:
        swfeatures._validate_names(document.features)
    except swfeatures.UnsupportedFeature as error:
        issue(str(error))
    if not issues:
        # Some topology-selection writers execute source features. A fresh document
        # protects live sketch planes, caches and error states from those operations.
        clone = Document.from_dict(document.to_dict())
        clone.regenerate()
        for feature in clone.features:
            if feature.error:
                issue(f'{feature.name}: failed feature: {feature.error}', feature)
        if not clone.has_body():
            issue('There is no solid body to export')
        if not issues:
            try:
                with tempfile.TemporaryDirectory(prefix='bauyrcad-preflight-') as folder:
                    swfeatures.write(clone, Path(folder) / 'check.SLDPRT')
            except swfeatures.UnsupportedFeature as error:
                reason = str(error)
                feature = next((f for f in clone.features if reason.startswith(f.name + ':')), None)
                issue(reason, feature)
    ready = not issues
    if ready:
        for row in rows:
            row['status'] = 'ready'
    return dict(ready=ready, mode='offline_native', features=rows, issues=issues,
                summary=('Ready for editable offline export.' if ready else
                         'Editable offline export is blocked. No file has been changed.'))
