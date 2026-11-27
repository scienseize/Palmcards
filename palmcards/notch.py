"""The teleprompter in the MacBook's notch (as Textream does): a take's notes on a
black panel that grows down out of the notch, above the menu bar and every window,
centred on the camera, so reading them looks like looking into the lens.

A native AppKit panel (pyobjc, already installed for the speech engine): borderless,
never takes the focus or the mouse, on every Space including PalmCards' full screen.
Its picture is render.Teleprompter drawn at the screen's own pixels, the notch's
height of black above the first row (the camera sits there). OpenCV's waitKey runs
the AppKit event loop, so the panel updates with the frames. On a screen with no
notch (an external display, an older Mac) there is none: the window's own
teleprompter is used instead (main.frame_loop)."""

from __future__ import annotations

import time

import cv2
import numpy as np

from palmcards import render
from palmcards.motion import Spring
from palmcards.style import MOTION
from palmcards.render import Sentence, Teleprompter, ViewState


def notch_rect() -> tuple[float, float, float, float, float] | None:
    """The notch on the screen that has one: (x0, x1 across the screen, its height, the screen's
    top in AppKit's coordinates, pixels per point), in points; None without one."""
    try:
        from AppKit import NSScreen
        for screen in NSScreen.screens():
            top = screen.safeAreaInsets().top
            if top <= 0:
                continue
            frame, left, right = screen.frame(), screen.auxiliaryTopLeftArea(), screen.auxiliaryTopRightArea()
            x0, x1 = frame.origin.x + left.origin.x + left.size.width, frame.origin.x + right.origin.x
            return x0, x1, float(top), frame.origin.y + frame.size.height, float(screen.backingScaleFactor())
    except Exception:
        return None
    return None


class NotchPanel:
    """The notch teleprompter: show(state) in a take with written notes, hide() otherwise."""

    def __init__(self, sentences: list[Sentence], notch: tuple[float, float, float, float, float]):
        self.notch = notch
        self._panel = self._view = None
        self._reveal = Spring()
        self._geometry = None
        self._last: np.ndarray | None = None
        self.rebuild(sentences)

    @classmethod
    def create(cls, sentences: list[Sentence]) -> "NotchPanel | None":
        notch = notch_rect()
        if notch is None:
            return None
        try:
            return cls(sentences, notch)
        except Exception as exc:  # AppKit refused: the window's teleprompter instead
            print(f"notch teleprompter off: {exc}")
            return None

    def rebuild(self, sentences: list[Sentence]) -> None:
        """Lay the notes out again (an edit, high contrast); the panel keeps its place."""
        x0, x1, notch_h, _, scale = self.notch
        # The teleprompter at the screen's pixels: a virtual 1280 x 720 frame (the app's) times the
        # pixels per point, its rows starting under the notch.
        self.prompter = Teleprompter(sentences, (round(1280 * scale), round(720 * scale)), top=round(notch_h * scale),
                                     notch_w=round((x1 - x0) * scale))
        gx0, _, gx1, gy1 = self.prompter.notes_glass()
        self._crop = (gx0, gx1, gy1)
        self._canvas = np.zeros((gy1, round(1280 * scale), 3), np.uint8)
        self._mask = self._shape(gx1 - gx0, gy1)
        self._last = None
        self._geometry = None

    def _shape(self, w: int, h: int) -> np.ndarray:
        """The panel's outline as alpha: straight across the top (the notch and the menu bar),
        its bottom corners rounded, as the teleprompter draws its black."""
        a = np.zeros((h, w), np.uint8)
        r = round(self.prompter.line_h * 0.75)
        cv2.rectangle(a, (0, 0), (w - 1, h - 1 - r), 255, -1)
        cv2.rectangle(a, (r, 0), (w - 1 - r, h - 1), 255, -1)
        for cx in (r, w - 1 - r):
            cv2.circle(a, (cx, h - 1 - r), r, 255, -1, cv2.LINE_AA)
        return a

    def _place(self, fraction: float) -> None:
        from AppKit import NSMakeRect
        x0, x1, notch_h, screen_top, scale = self.notch
        gx0, gx1, gy1 = self._crop
        full_w, full_h = (gx1 - gx0) / scale, gy1 / scale
        w = round(((x1 - x0) * (1 - fraction) + full_w * fraction) * scale) / scale
        h = round((notch_h * (1 - fraction) + full_h * fraction) * scale) / scale
        if self._geometry == (w, h):
            return
        self._geometry = (w, h)
        self._panel.setFrame_display_(NSMakeRect((x0 + x1 - w) / 2, screen_top - h, w, h), False)
        # Clip a full-size image anchored at the lens. Resizing the image itself
        # would squeeze the glyphs on every frame of the reveal.
        self._view.setFrame_(NSMakeRect((w - full_w) / 2, h - full_h, full_w, full_h))

    def _visibility(self, visible: bool) -> None:
        now = time.perf_counter()
        response = MOTION.focus_in if visible else MOTION.focus_out
        self._reveal.retarget(float(visible), now, 0.0 if render.REDUCED else response)
        fraction = self._reveal.at(now)
        if not visible and fraction < 0.001:
            self._panel.orderOut_(None)
            return
        self._place(fraction)
        if visible and not self._panel.isVisible():
            self._panel.orderFrontRegardless()

    def _make(self) -> None:
        import AppKit
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 10, 10),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        panel.setLevel_(AppKit.NSPopUpMenuWindowLevel)  # above the menu bar, over full-screen apps
        panel.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                                     | AppKit.NSWindowCollectionBehaviorStationary
                                     | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
        panel.setIgnoresMouseEvents_(True)
        panel.setOpaque_(False)
        panel.setHasShadow_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        view = AppKit.NSImageView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 10, 10))
        view.setImageScaling_(AppKit.NSImageScaleAxesIndependently)
        panel.setReleasedWhenClosed_(False)
        host = panel.contentView()
        host.setWantsLayer_(True)
        host.layer().setMasksToBounds_(True)
        host.addSubview_(view)
        self._panel, self._view = panel, view

    def show(self, state: ViewState, snap: bool = False) -> None:
        """Draw the take's notes (`state`: the teleprompter's view of it) and show the panel."""
        gx0, gx1, gy1 = self._crop
        self._canvas[:] = 0
        self.prompter.follow(state, snap=snap)
        self.prompter.draw(self._canvas, state)
        crop = self._canvas[:, gx0:gx1]
        if self._last is not None and np.array_equal(crop, self._last):
            self._visibility(True)
            return
        self._last = crop.copy()
        import AppKit
        import Quartz
        # Raw pixels through Quartz, not a PNG: 0.3 ms against 7 at this size, and the sound
        # wave changes the picture every frame. Outside the outline the colour is black, so
        # straight alpha is already premultiplied.
        rgba = cv2.cvtColor(np.dstack((crop, self._mask)), cv2.COLOR_BGRA2RGBA)
        h, w = rgba.shape[:2]
        provider = Quartz.CGDataProviderCreateWithCFData(AppKit.NSData.dataWithBytes_length_(rgba.tobytes(), rgba.nbytes))
        cg = Quartz.CGImageCreate(w, h, 8, 32, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
                                  Quartz.kCGImageAlphaPremultipliedLast, provider, None, False,
                                  Quartz.kCGRenderingIntentDefault)
        scale = self.notch[4]
        image = AppKit.NSImage.alloc().initWithCGImage_size_(cg, (w / scale, h / scale))  # points: the screen's pixels
        if self._panel is None:
            self._make()
        self._view.setImage_(image)
        self._visibility(True)

    def scroll_for(self, state: ViewState) -> float:
        return self.prompter.scroll_for(state)

    def hide(self) -> None:
        if self._panel is not None and self._panel.isVisible():
            self._visibility(False)

    def close(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
            self._panel.close()
            self._panel = None
