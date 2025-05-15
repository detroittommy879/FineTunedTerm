#!/usr/bin/env python
# filepath: t:\z-test1\termbuttons\term_enhanced.py

import os
import sys
import subprocess
import shutil
import logging
import time
import datetime
import re

from PyQt6.QtCore import Qt, QProcess, QSize, pyqtSignal, QProcessEnvironment, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QGroupBox, QLabel, QSplitter, QTextEdit, QScrollArea,
    QTabWidget, QSizePolicy, QFileDialog, QRadioButton, QStackedWidget
)
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat, QMouseEvent

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.FileHandler(f"termlog_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ANSI palette...
DEFAULT_FG_COLOR = QColor("white")
DEFAULT_BG_COLOR = QColor("black")
ANSI_SGR_CODES_TO_COLORS = {
    30: QColor("black"), 31: QColor("#CD0000"), 32: QColor("#00CD00"),
    33: QColor("#CDCD00"), 34: QColor("#0000EE"), 35: QColor("#CD00CD"),
    36: QColor("#00CDCD"), 37: QColor("#E5E5E5"), 39: DEFAULT_FG_COLOR,
    40: QColor("black"), 41: QColor("#CD0000"), 42: QColor("#00CD00"),
    43: QColor("#CDCD00"), 44: QColor("#0000EE"), 45: QColor("#CD00CD"),
    46: QColor("#00CDCD"), 47: QColor("#E5E5E5"), 49: DEFAULT_BG_COLOR,
    90: QColor("#7F7F7F"), 91: QColor("red"), 92: QColor("green"),
    93: QColor("yellow"), 94: QColor("blue"), 95: QColor("magenta"),
    96: QColor("cyan"), 97: QColor("white"),
    100: QColor("#7F7F7F"), 101: QColor("red"), 102: QColor("green"),
    103: QColor("yellow"), 104: QColor("blue"), 105: QColor("magenta"),
    106: QColor("cyan"), 107: QColor("white"),
}

class TerminalWidget(QTextEdit):
    """A minimal ANSI-aware terminal, with input-guarded backspace and cursor locked to bottom for input."""
    commandEntered = pyqtSignal(str)
    titleChanged = pyqtSignal(str)

    ansi_escape_regex = re.compile(
        r'\x1b(?:'
         r'\[([\d;?]*)m'  # SGR
        r'|\]([012]);([^\x07\x1b]*)(?:\x07|\x1b\\)'  # OSC
        r'|\[([\d;]*)([HJK])'  # CSI cursor/erase
        r'|([()][012AB])'  # Character set
        r'|([ENOM])'      # Other modes
        r'|=|>)'          # Keypad modes
    )

    def __init__(self, parent=None, terminal_id="Unknown"):
        super().__init__(parent)
        self.terminal_id = terminal_id
        self.setReadOnly(False)
        self.setAcceptRichText(False)
        self.setFont(QFont("Courier New", 10))
        self.setStyleSheet(f"background-color:{DEFAULT_BG_COLOR.name()};"
                           f"color:{DEFAULT_FG_COLOR.name()};")
        self.current_format = QTextCharFormat()
        self._reset_char_format_to_default()
        self.setCurrentCharFormat(self.current_format)
        
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(50) # Ensure it has some minimum height in splitter

        self.input_start_pos = 0
        self.process = QProcess(self)
        self.process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.handle_process_error)
        self.process_running = False
        self.restart_count = 0
        self.last_started = 0
        QTimer.singleShot(0, self.start_process) # Start process after event loop starts

    def _reset_char_format_to_default(self):
        f = self.current_format
        f.setForeground(DEFAULT_FG_COLOR)
        f.setBackground(DEFAULT_BG_COLOR)
        f.setFontWeight(QFont.Weight.Normal)
        f.setFontItalic(False)
        f.setFontUnderline(False)

    def _apply_sgr_code(self, code):
        if code == 0:
            self._reset_char_format_to_default()
        elif code == 1:
            self.current_format.setFontWeight(QFont.Weight.Bold)
        elif code == 3:
            self.current_format.setFontItalic(True)
        elif code == 4:
            self.current_format.setFontUnderline(True)
        elif code in ANSI_SGR_CODES_TO_COLORS:
            color = ANSI_SGR_CODES_TO_COLORS[code]
            if (30 <= code <= 37) or (90 <= code <= 97) or code == 39:
                self.current_format.setForeground(color)
            elif (40 <= code <= 47) or (100 <= code <= 107) or code == 49:
                self.current_format.setBackground(color)
        # Basic handling for 256 colors / truecolor (foreground)
        elif code == 38: 
            # Next parts of SGR would specify 256/truecolor, skip for now
            # Example: \x1b[38;5;208m (orange) or \x1b[38;2;255;165;0m (orange)
            pass # Placeholder, parser needs to handle multi-part SGR for this
        # Basic handling for 256 colors / truecolor (background)
        elif code == 48:
            pass # Placeholder


    def append_ansi_text(self, text: str):
        self.moveCursor(QTextCursor.MoveOperation.End)
        last = 0
        for m in self.ansi_escape_regex.finditer(text):
            s, e = m.span()
            if s > last:
                seg = text[last:s]
                self.setCurrentCharFormat(self.current_format)
                self.insertPlainText(seg)
            
            sgr, osc_t, osc_c, csi_p, csi_l, cs, sc = m.groups()

            if sgr is not None: # SGR codes (text attributes)
                parts = sgr.split(';') if sgr else ['0'] # if empty sgr (e.g. \x1b[m), treat as reset
                idx = 0
                while idx < len(parts):
                    part = parts[idx]
                    if not part: # Handle cases like CSI ; m
                        code = 0 
                    else:
                        try:
                            code = int(part)
                        except ValueError:
                            idx +=1
                            continue # Skip malformed part
                    
                    if code == 38 or code == 48: # Extended colors (256 or truecolor)
                        if idx + 1 < len(parts):
                            color_mode = int(parts[idx+1])
                            if color_mode == 5 and idx + 2 < len(parts): # 256-color mode
                                # color_index = int(parts[idx+2])
                                # Apply 256 color (omitted for brevity)
                                idx += 2 
                            elif color_mode == 2 and idx + 4 < len(parts): # Truecolor mode
                                # r, g, b = int(parts[idx+2]), int(parts[idx+3]), int(parts[idx+4])
                                # Apply truecolor (omitted for brevity)
                                idx += 4
                            else: # Malformed extended color
                                pass
                        else: # Malformed extended color
                            pass
                    else: # Standard SGR code
                        self._apply_sgr_code(code)
                    idx += 1
            elif osc_t and osc_c: # OSC codes (Operating System Command)
                if osc_t in ('0', '1', '2'): # 0, 1, 2 are for titles
                    self.titleChanged.emit(osc_c)
            # (We skip full CSI/other emulation for brevity)
            last = e
            
        if last < len(text):
            tail = text[last:]
            self.setCurrentCharFormat(self.current_format)
            self.insertPlainText(tail)

        self.moveCursor(QTextCursor.MoveOperation.End)
        self.ensureCursorVisible()
        self.input_start_pos = self.textCursor().position()

    def handle_process_error(self, err: QProcess.ProcessError):
        msg = f"⚠️ ProcessError ({err}): {self.process.errorString()}\n"
        logger.error(f"[{self.terminal_id}] Process error: {self.process.errorString()} (code: {err})")
        self.append_ansi_text(msg)

    def start_process(self):
        if self.process_running:
            logger.warning(f"[{self.terminal_id}] Process already running, not starting again.")
            return

        now = time.time()
        if 0 < (now - self.last_started) < 2: # Rapid restarts within 2 seconds
            self.restart_count += 1
            if self.restart_count > 3:
                logger.error(f"[{self.terminal_id}] Too many restarts. Stopping.")
                self.append_ansi_text(f"\x1b[31m⚠️ [{self.terminal_id}] Too many restarts. Shell disabled.\x1b[0m\n")
                return
        else:
            self.restart_count = 0
        self.last_started = now

        shell_executable = shutil.which("bash") or shutil.which("powershell.exe") or shutil.which("cmd.exe")
        if not shell_executable:
            logger.error(f"[{self.terminal_id}] No suitable shell found.")
            self.append_ansi_text(f"\x1b[31m⚠️ [{self.terminal_id}] No shell found (bash, powershell, cmd).\x1b[0m\n")
            return

        args = []
        if 'bash' in shell_executable.lower():
            # Check if it's WSL bash or native bash
            # A simple heuristic: if "wsl" is in path or it's .exe, it might be WSL launcher
            # For true interactivity with bash, --login -i is good.
            args = ["--login", "-i"]
        # For powershell or cmd, they are interactive by default.

        logger.info(f"[{self.terminal_id}] Starting shell: {shell_executable} {' '.join(args)}")
        self.append_ansi_text(f"[{self.terminal_id}] Starting {os.path.basename(shell_executable)} {' '.join(args)}...\n")
        
        self.process.start(shell_executable, args)
        if not self.process.waitForStarted(3000):
            logger.error(f"[{self.terminal_id}] Failed to start shell: {shell_executable}")
            self.append_ansi_text(f"\x1b[31m⚠️ Failed to start shell: {os.path.basename(shell_executable)}.\x1b[0m\n")
            self.process_running = False
        else:
            logger.info(f"[{self.terminal_id}] Shell started successfully.")
            self.process_running = True


    def read_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode(sys.stdout.encoding or 'utf-8', errors='replace')
        self.append_ansi_text(data)

    def read_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode(sys.stderr.encoding or 'utf-8', errors='replace')
        # Optionally color stderr differently, e.g., \x1b[31m{data}\x1b[0m
        self.append_ansi_text(data)

    def process_finished(self, exitCode, exitStatus: QProcess.ExitStatus):
        status_str = "crashed" if exitStatus == QProcess.ExitStatus.CrashExit else "finished"
        message = f"\n\x1b[33m[{self.terminal_id}] Shell process {status_str} with code {exitCode}.\x1b[0m\n"
        logger.info(f"[{self.terminal_id}] Process finished. ExitCode: {exitCode}, Status: {exitStatus}")
        self.append_ansi_text(message)
        self.process_running = False
        # Optional: auto-restart logic (be careful with loops)
        # self.start_process() 

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_C and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.process_running:
                self.process.write(b'\x03') # SIGINT
            return

        # For all other events, first ensure the cursor is where input should occur.
        # This is at the very end of the document for simple terminals like this one.
        self.moveCursor(QTextCursor.MoveOperation.End)
        current_cursor_pos = self.textCursor().position()

        if ev.key() == Qt.Key.Key_Backspace:
            if current_cursor_pos > self.input_start_pos:
                super().keyPressEvent(ev) # Default backspace (deletes char, moves cursor left)
            # If at or before input_start_pos, backspace is swallowed (no action).
            return

        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            doc = self.document()
            # Text to send is from input_start_pos to current end (which is document end)
            txt_to_send = doc.toPlainText()[self.input_start_pos:]
            cmd = txt_to_send.rstrip('\n')

            if self.process_running:
                self.process.write((cmd + "\n").encode(sys.stdout.encoding or 'utf-8'))
            else:
                self.append_ansi_text("\x1b[31mShell not running.\x1b[0m\n")
            
            super().keyPressEvent(ev) # Let QTextEdit insert the newline. Cursor moves.
            self.input_start_pos = self.textCursor().position() # Optimistically update
            return

        # For all other keys (printable characters, arrows, etc.):
        # Arrow keys, Home, End will operate relative to the entire document end,
        # not just the input line. This is a simplification.
        super().keyPressEvent(ev)

    def mousePressEvent(self, event: QMouseEvent):
        # Default behavior allows text selection.
        # The keyPressEvent ensures typing always happens at the end.
        super().mousePressEvent(event)
        # If you wanted to force the visual cursor to the end even on click (hinders selection):
        # self.moveCursor(QTextCursor.MoveOperation.End)

    def send_command(self, cmd, append_enter=True):
        if self.process_running:
            self.moveCursor(QTextCursor.MoveOperation.End) # Ensure local echo appears at end
            # Echo command locally (optional, shell usually does this)
            # self.insertPlainText(cmd + ('\n' if append_enter else ''))
            # self.input_start_pos = self.textCursor().position()

            data = cmd + ('\n' if append_enter else '')
            self.process.write(data.encode(sys.stdout.encoding or 'utf-8'))
        else:
            self.append_ansi_text(f"\x1b[31m[{self.terminal_id}] ❌ Not running.\x1b[0m\n")

    def closeEvent(self, event):
        logger.info(f"[{self.terminal_id}] Close event received. Killing process.")
        if self.process_running and self.process.state() == QProcess.ProcessState.Running:
            self.process.kill()
            self.process.waitForFinished(1000) # Wait a bit for clean exit
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Terminal Emulator")
        self.resize(900, 700)

        self.terminals = [] # Master list of TerminalWidget instances
        self.terminal_counter = 0

        topbar = QWidget()
        tlay = QHBoxLayout(topbar)
        tlay.setContentsMargins(0,0,0,0)
        tlay.addWidget(QLabel("View:"))
        self.rb_stack = QRadioButton("Stacked")
        self.rb_tabs = QRadioButton("Tabbed")
        tlay.addWidget(self.rb_stack)
        tlay.addWidget(self.rb_tabs)
        tlay.addStretch()
        self.btn_add = QPushButton("+")
        self.btn_add.setFixedSize(QSize(25, 25))
        tlay.addWidget(self.btn_add)

        self.stacker = QSplitter(Qt.Orientation.Vertical)
        self.tabber = QTabWidget()
        self.tabber.setTabsClosable(True)
        
        self.views = QStackedWidget()
        self.views.addWidget(self.stacker)  # index 0
        self.views.addWidget(self.tabber)   # index 1

        self.rb_stack.toggled.connect(self._on_view_mode_changed)
        self.rb_tabs.toggled.connect(self._on_view_mode_changed)
        self.btn_add.clicked.connect(self._on_add_terminal_clicked)
        self.tabber.tabCloseRequested.connect(self._on_tab_close_requested)

        main_widget = QWidget()
        mlay = QVBoxLayout(main_widget)
        mlay.addWidget(topbar)
        mlay.addWidget(self.views, 1)
        self.setCentralWidget(main_widget)

        # Initial setup
        self.rb_stack.setChecked(True) # Default to stacked view
        self._add_new_terminal_instance() # Add first terminal
        self._add_new_terminal_instance() # Add second terminal
        self._refresh_active_view_layout() # Populate the view

    def _on_view_mode_changed(self):
        # This slot is connected to both radio buttons' toggled signal
        # We only care when a button becomes checked
        sender = self.sender()
        if not sender.isChecked():
            return

        new_view_index = 0 if sender == self.rb_stack else 1
        if self.views.currentIndex() != new_view_index:
            self.views.setCurrentIndex(new_view_index)
            self._refresh_active_view_layout()
            
            # Focus the "current" terminal in the new view
            if new_view_index == 1 and self.tabber.count() > 0: # Tabbed
                current_tab_widget = self.tabber.currentWidget()
                if current_tab_widget:
                    current_tab_widget.setFocus()
            elif new_view_index == 0 and len(self.terminals) > 0: # Stacked
                # Focus the last terminal in the list for simplicity
                self.terminals[-1].setFocus()


    def _add_new_terminal_instance(self):
        self.terminal_counter += 1
        tid = f"Term{self.terminal_counter}"
        term = TerminalWidget(terminal_id=tid)
        term.titleChanged.connect(lambda title, widget=term: self._update_terminal_title(widget, title))
        term.setProperty("current_title", tid) # Store initial title
        self.terminals.append(term)
        return term

    def _on_add_terminal_clicked(self):
        new_term = self._add_new_terminal_instance()
        self._refresh_active_view_layout()
        
        # Focus the new terminal
        if self.views.currentIndex() == 0: # Stacked view
            new_term.setFocus()
        else: # Tabbed view
            idx = self.tabber.indexOf(new_term)
            if idx != -1:
                self.tabber.setCurrentIndex(idx)
            new_term.setFocus()


    def _on_tab_close_requested(self, index: int):
        if len(self.terminals) <= 1: # Don't close the last terminal
            return
        
        widget_to_close = self.tabber.widget(index)
        if widget_to_close and isinstance(widget_to_close, TerminalWidget):
            self._close_terminal_widget(widget_to_close)

    def _close_terminal_widget(self, term_widget: TerminalWidget):
        if term_widget in self.terminals:
            logger.info(f"Closing terminal: {term_widget.terminal_id}")
            term_widget.close() # This will trigger TerminalWidget.closeEvent for process cleanup
            self.terminals.remove(term_widget)
            
            # Widget is parented to None by close() or will be by deleteLater()
            # It will be removed from layouts in _refresh_active_view_layout
            term_widget.deleteLater() # Schedule for deletion
            
            self._refresh_active_view_layout()
        else:
            logger.warning(f"Attempted to close a widget not in self.terminals: {term_widget}")


    def _update_terminal_title(self, widget: TerminalWidget, title: str):
        name = title.strip() or widget.terminal_id
        widget.setProperty("current_title", name) # Update stored property

        # If in tabbed view and this widget is a tab, update its text
        if self.views.currentWidget() == self.tabber:
            idx = self.tabber.indexOf(widget)
            if idx != -1:
                self.tabber.setTabText(idx, name[:30]) # Max 30 chars for tab title

    def _refresh_active_view_layout(self):
        # Temporarily disconnect tabCloseRequested to avoid issues during repopulation
        try:
            self.tabber.tabCloseRequested.disconnect(self._on_tab_close_requested)
        except TypeError: # If not connected
            pass

        # Detach all terminal widgets from their current parents in managed views
        # This prepares them to be re-added to the correct container.
        for term_widget in self.terminals:
            if term_widget.parent() == self.stacker or \
               (self.tabber.indexOf(term_widget) != -1 and term_widget.parent() == self.tabber.widget(self.tabber.indexOf(term_widget))): # check if it is a page in tabber
                 term_widget.setParent(None)


        # Clear Tabber (removeTab reparents widget to None)
        while self.tabber.count() > 0:
            self.tabber.removeTab(0)

        # Clear Stacker (setting parent to None removes widget from splitter)
        while self.stacker.count() > 0:
            widget = self.stacker.widget(0)
            widget.setParent(None)

        # Re-populate based on the current view mode
        active_view_idx = self.views.currentIndex()

        if active_view_idx == 0: # Stacked view
            for term_widget in self.terminals:
                self.stacker.addWidget(term_widget)
                term_widget.show() # Ensure visible
            # Distribute space in splitter if multiple terminals
            if self.stacker.count() > 0:
                base_size = self.stacker.height() if self.stacker.orientation() == Qt.Orientation.Vertical else self.stacker.width()
                size_per_widget = base_size // self.stacker.count()
                sizes = [size_per_widget] * self.stacker.count()
                self.stacker.setSizes(sizes)

        else: # Tabbed view (active_view_idx == 1)
            for term_widget in self.terminals:
                title = term_widget.property("current_title") or term_widget.terminal_id
                self.tabber.addTab(term_widget, title[:30])
                term_widget.show() # Ensure visible

        # Reconnect tabCloseRequested
        self.tabber.tabCloseRequested.connect(self._on_tab_close_requested)
        
        # Ensure the main window has focus if no terminal does
        if not any(t.hasFocus() for t in self.terminals) and len(self.terminals) == 0:
            self.setFocus()


    def closeEvent(self, event):
        logger.info("MainWindow close event. Closing all terminals.")
        # Create a copy of the list for safe iteration while removing
        for term_widget in list(self.terminals):
            self._close_terminal_widget(term_widget)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # For better high DPI scaling if needed, though Courier New might not scale ideally
    # QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling) 
    # QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps) 
    
    main_window = MainWindow()
    main_window.show()
    sys.exit(app.exec())