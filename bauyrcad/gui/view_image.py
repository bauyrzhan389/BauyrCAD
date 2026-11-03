"""Clean viewport PNG and clipboard capture, including displayed Evaluate overlays."""
import numpy as np
from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QLabel,
                               QMessageBox, QVBoxLayout)


def capture(viewer, *, scale=1, background='view'):
    if scale not in (1, 2, 3):
        raise ValueError('Image scale must be 1, 2 or 3')
    if background not in ('view', 'white', 'transparent'):
        raise ValueError('Choose view, white or transparent background')
    transparent = background == 'transparent'
    renderer = viewer.renderer
    gradient = renderer.GetGradientBackground()
    previous_background = renderer.GetBackground()
    previous_alpha = viewer.plotter.image_transparent_background
    control=viewer.section.control
    manipulator=control.manipulator if control is not None else None
    previous_handles=manipulator.capture_hidden if manipulator is not None else False
    if manipulator is not None:
        manipulator.capture_hidden=True
    try:
        # VTK renders gradient backgrounds opaque even in an RGBA capture.
        if background != 'view':
            renderer.GradientBackgroundOff()
        if background == 'white':
            renderer.SetBackground(1, 1, 1)
        viewer.render()
        pixels = np.ascontiguousarray(viewer.plotter.screenshot(
            return_img=True, transparent_background=transparent, scale=scale))
    finally:
        renderer.SetBackground(*previous_background)
        renderer.SetGradientBackground(gradient)
        viewer.plotter.image_transparent_background = previous_alpha
        if manipulator is not None:
            manipulator.capture_hidden=previous_handles
        viewer.render()
    h, w, channels = pixels.shape
    fmt = QImage.Format_RGBA8888 if channels == 4 else QImage.Format_RGB888
    return QImage(pixels.data, w, h, pixels.strides[0], fmt).copy()


def save_png(viewer, path, *, scale=1, background='view'):
    image = capture(viewer, scale=scale, background=background)
    output = QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly):
        raise OSError(output.errorString())
    if not image.save(output, 'PNG'):
        output.cancelWriting()
        raise OSError('Could not encode the viewport image')
    if not output.commit():
        raise OSError(output.errorString())
    return image.size()


def copy_view(main):
    try:
        QApplication.clipboard().setImage(capture(main.viewer))
        main.status('Viewport image copied to clipboard')
    except Exception as error:
        QMessageBox.warning(main, 'Copy View Image', str(error))


def export_view(main):
    dialog = QDialog(main)
    dialog.setWindowTitle('Save View Image')
    layout = QVBoxLayout(dialog)
    hint = QLabel('Save the visible model, section caps and measurement labels.\n'
                  'Toolbars and floating control windows are excluded.')
    hint.setWordWrap(True); layout.addWidget(hint)
    form = QFormLayout()
    scale = QComboBox()
    for factor in (1, 2, 3):
        scale.addItem(f'{factor}× viewport resolution', factor)
    scale.setCurrentIndex(1)
    form.addRow('Resolution', scale)
    background = QComboBox()
    for label, key in [('White', 'white'), ('Current view', 'view'), ('Transparent', 'transparent')]:
        background.addItem(label, key)
    form.addRow('Background', background)
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return
    path, _ = QFileDialog.getSaveFileName(main, 'Save View Image', 'BauyrCAD-view.png', 'PNG image (*.png)')
    if not path:
        return
    if not path.lower().endswith('.png'):
        path += '.png'
    try:
        size = save_png(main.viewer, path, scale=scale.currentData(), background=background.currentData())
        main.status(f'Saved {size.width()} × {size.height()} image: {path}')
    except Exception as error:
        QMessageBox.warning(main, 'Save View Image', str(error))
