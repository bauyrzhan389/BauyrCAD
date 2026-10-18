"""Equation editing attached to existing feature controls, staged until command OK."""
import math
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QMenu
from ..core.parameters import FIELDS, ParameterError, feature_fields, feature_value, parameter_value


class FeatureEquations(QObject):
    def __init__(self, command, kind):
        super().__init__(command.win)
        self.command, self.kind = command, kind
        self.editor = None
        self.widgets, self.objects, self.precision = {}, {}, {}
        feature = command.feature
        self.expressions = {b['field']: b['expression'] for b in command.doc.parameter_bindings
                            if feature is not None and b['feature'] == feature.id}
        aliases = dict(radius='r', radius2='r2', distance='d', distance2='d2', thickness='t')
        if kind == 'linear_pattern':
            aliases.update(count='n1', count2='n2', spacing='sp1', spacing2='sp2')
        elif kind == 'circular_pattern':
            aliases.update(count='n')
        elif kind == 'ref_plane':
            aliases.update(distance='dist')
        elif kind == 'hole':
            aliases.update(cbore_d='cb_d', cbore_depth='cb_h', csk_angle='csk_a')
        elif kind == 'helix':
            aliases.update(diameter='dia', revolutions='revs', start_angle='start_a')
        for field in FIELDS.get(kind, {}):
            widget = getattr(command, aliases.get(field, field), None)
            if widget is None or not hasattr(widget, 'lineEdit'):
                continue
            self.widgets[field] = widget
            if hasattr(widget, 'decimals'):
                self.precision[field] = widget.decimals()
            for obj in (widget, widget.lineEdit()):
                obj.installEventFilter(self)
                self.objects[obj] = field
            self.refresh(field)

    def refresh(self, field):
        widget = self.widgets[field]
        expression = self.expressions.get(field)
        widget.setReadOnly(expression is not None)
        if field in self.precision:
            widget.setDecimals(max(8, self.precision[field]) if expression is not None else self.precision[field])
        if expression is not None:
            value = self.value(field, expression)
            widget.setRange(min(widget.minimum(), value), max(widget.maximum(), value))
            widget.setValue(value)
        widget.setPrefix('= ' if expression is not None else '')
        instruction = 'Press = or right-click to edit an equation; enter a number there to unlink.'
        widget.setToolTip((f'Controlled by {expression}. ' if expression is not None else '') + instruction)

    def eventFilter(self, obj, event):
        field = self.objects.get(obj)
        if field is None or not self.widgets[field].isEnabled():
            return False
        if event.type() == QEvent.KeyPress and event.text() == '=':
            self.open(field)
            return True
        if event.type() == QEvent.MouseButtonDblClick and field in self.expressions:
            self.open(field)
            return True
        if event.type() == QEvent.ContextMenu:
            menu = QMenu(self.widgets[field])
            menu.addAction('Edit equation / value...', lambda: self.open(field))
            menu.popup(event.globalPos())
            self.menu = menu
            return True
        return False

    def value(self, field, expression):
        kind = FIELDS[self.kind][field]
        raw = parameter_value(self.command.doc.parameters, expression, kind)
        return feature_value(field, kind, raw, self.kind)

    def open(self, field):
        from .sketcher import ValueEditor
        from .command_line import evaluate
        widget = self.widgets[field]
        if self.editor is not None:
            self.editor.close()
        def accept(text):
            text = text.strip()
            expression = text[1:].strip() if text.startswith('=') else None
            try:
                if expression is not None:
                    value = self.value(field, expression)
                else:
                    value = evaluate(text)
                    if not math.isfinite(value):
                        raise ParameterError('The value must be finite')
                    # Calculator angles are degrees; feature_value expects radians.
                    kind = FIELDS[self.kind][field]
                    value = feature_value(field, kind, math.radians(value) if kind == 'angle' else value, self.kind)
                if not widget.minimum() <= value <= widget.maximum():
                    raise ParameterError(f'Enter a value from {widget.minimum():g} to {widget.maximum():g}')
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
                self.editor.error.setText(str(exc))
                return False
            if expression is None:
                self.expressions.pop(field, None)
            else:
                self.expressions[field] = expression
            widget.setValue(value)
            self.refresh(field)
            callback = getattr(self.command, 'changed', self.command.update_preview)
            callback()
            return True
        initial = '=' + self.expressions[field] if field in self.expressions else '='
        label = {'radius2': 'End radius', 'distance2': 'Second distance', 'thickness': 'Wall thickness',
                 'spacing2': 'Second spacing', 'count': 'Instances', 'count2': 'Second-direction instances'}.get(field, field.title())
        self.editor = ValueEditor(self.command.win, f'{label}: =name links a parameter; a number unlinks', initial, accept)
        self.editor.setMinimumWidth(440)
        self.editor.popup()

    def apply(self, feature):
        for field, expression in self.expressions.items():
            if field not in feature_fields(feature):
                raise ParameterError(f'{field} is unavailable in this mode; unlink its equation first')
            setattr(feature, field, self.value(field, expression))

    def bindings(self, fid):
        return [b for b in self.command.doc.parameter_bindings if b['feature'] != fid] + [
            {'feature': fid, 'field': field, 'expression': expression}
            for field, expression in self.expressions.items()]

    def close(self):
        if getattr(self, 'menu', None) is not None:
            self.menu.close()
        if self.editor is not None:
            self.editor.close()
        for obj in self.objects:
            obj.removeEventFilter(self)
        self.deleteLater()
