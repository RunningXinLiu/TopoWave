"""Navigation toolbar shared by the workbench's Qt plots."""
from contextlib import nullcontext
import sys

from matplotlib.backends.backend_qtagg import NavigationToolbar2QT


class PlotToolbar(NavigationToolbar2QT):
    def _wait_cursor_for_draw_cm(self):
        # On Cocoa, registering a transient wait cursor during a timed redraw
        # can crash in NSCursor.set. Pan and zoom still use their normal cursors.
        if sys.platform == "darwin":
            return nullcontext()
        return super()._wait_cursor_for_draw_cm()
