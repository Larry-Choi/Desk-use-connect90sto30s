"""A second browser surface inside the Desk window.

Claude artifacts refuse to be framed, so this is another WebView2 control
laid over the right-hand pane, sharing the same login store as Desk.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


def _rounded(width: int, height: int, radius: int):
    from System.Drawing import Region
    from System.Drawing.Drawing2D import GraphicsPath

    curve = max(2, min(radius * 2, width - 1, height - 1))
    path = GraphicsPath()
    path.AddLine(0, 0, width, 0)
    path.AddLine(width, 0, width, height - curve)
    path.AddArc(width - curve, height - curve, curve, curve, 0, 90)
    path.AddArc(0, height - curve, curve, curve, 90, 90)
    path.AddLine(0, height - curve, 0, 0)
    path.CloseFigure()
    return Region(path)


class PagePane:
    def __init__(self) -> None:
        self._view = None
        self._ready = threading.Event()
        self._failed = ""
        self._url = ""
        self._lock = threading.Lock()

    def sync(self, form, url: str, x: float, y: float, width: float, height: float, scale: float) -> str:
        if width < 40 or height < 40:
            self.hide(form)
            return ""
        error = self._ensure(form)
        if error:
            return error
        self._place(form, x, y, width, height, scale, url)
        return ""

    def hide(self, form) -> None:
        view = self._view
        if view is None or form is None:
            return
        self._ui(form, lambda: view.Hide())

    def _ensure(self, form) -> str:
        with self._lock:
            if self._view is not None:
                return self._failed
            if form is None or getattr(form, "webview", None) is None:
                return "페이지 칸을 아직 열 수 없습니다."
            done = threading.Event()

            def create() -> None:
                try:
                    from System import Uri
                    from System.Drawing import Color
                    from Microsoft.Web.WebView2.WinForms import WebView2

                    view = WebView2()
                    view.Visible = False
                    view.DefaultBackgroundColor = Color.FromArgb(255, 12, 12, 12)
                    form.Controls.Add(view)
                    view.BringToFront()
                    environment = form.webview.CoreWebView2.Environment

                    def ready(sender, args) -> None:
                        try:
                            if not args.IsSuccess:
                                self._failed = "페이지 칸을 열지 못했습니다."
                                return
                            settings = view.CoreWebView2.Settings
                            settings.AreDefaultContextMenusEnabled = True
                            settings.AreDevToolsEnabled = False
                            settings.IsStatusBarEnabled = False
                            settings.IsZoomControlEnabled = True
                            settings.IsSwipeNavigationEnabled = True
                        finally:
                            done.set()

                    view.CoreWebView2InitializationCompleted += ready
                    view.EnsureCoreWebView2Async(environment)
                    self._view = view
                    self._uri = Uri
                except Exception as exc:
                    logger.exception("page pane")
                    self._failed = "페이지 칸을 열지 못했습니다."
                    done.set()
                    raise exc

            try:
                self._ui(form, create)
            except Exception:
                self._failed = self._failed or "페이지 칸을 열지 못했습니다."
                return self._failed
        if not done.wait(8):
            return "페이지 칸이 아직 준비되지 않았습니다."
        return self._failed

    def _place(self, form, x: float, y: float, width: float, height: float, scale: float, url: str) -> None:
        view = self._view
        if view is None:
            return
        factor = scale if scale and scale > 0 else 1
        left = max(0, int(x * factor))
        top = max(0, int(y * factor))
        box_w = max(1, int(width * factor))
        box_h = max(1, int(height * factor))
        target = url

        def move() -> None:
            from System.Drawing import Point, Size

            view.Location = Point(left, top)
            view.Size = Size(box_w, box_h)
            view.Region = _rounded(box_w, box_h, int(22 * factor))
            view.BringToFront()
            view.Show()
            if target and target != self._url and view.CoreWebView2 is not None:
                view.CoreWebView2.Navigate(target)
                self._url = target

        self._ui(form, move)

    def _ui(self, form, action) -> None:
        from System import Action

        if form.InvokeRequired:
            form.Invoke(Action(action))
        else:
            action()
