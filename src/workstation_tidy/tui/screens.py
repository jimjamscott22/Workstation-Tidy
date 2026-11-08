from __future__ import annotations

import argparse
import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Select, Static

from ..common import collect, filter_candidates, human, preflight_selected, safe
from ..handlers import Handler
from ..models import Candidate, TidyError

if TYPE_CHECKING:
    from .app import TidyApp

CATEGORIES = ("packages", "build", "desktop", "review")
OUTCOME_STYLES = {
    "cleaned": "bold green",
    "partial": "bold yellow",
    "failed": "bold red",
    "skipped": "yellow",
    "interrupted": "bold red",
}


def plain(value: object, style: str = "") -> Text:
    """Render untrusted text literally: no Rich markup, no control characters."""
    return Text(safe(value), style=style)


def parse_size(value: str) -> int:
    from ..cli import size_argument

    if not value.strip():
        return 0
    try:
        return size_argument(value.strip())
    except argparse.ArgumentTypeError as exc:
        raise ValueError(str(exc)) from exc


class TidyScreen(Screen):
    @property
    def tidy(self) -> TidyApp:
        return cast("TidyApp", self.app)


class ScanScreen(TidyScreen):
    BINDINGS = [
        Binding("space", "toggle", "Select"),
        Binding("p", "preview", "Preview / clean"),
        Binding("r", "rescan", "Rescan"),
        Binding("d", "doctor", "Doctor"),
        Binding("h", "history", "History"),
        Binding("c", "config", "Config"),
        Binding("q", "app.quit", "Quit"),
    ]
    DEFAULT_CSS = """
    ScanScreen #banner { padding: 0 1; height: auto; }
    ScanScreen #banner.warning { background: $warning 25%; }
    ScanScreen #banner.ok { background: $success 15%; }
    ScanScreen #filters { height: auto; padding: 0 1; }
    ScanScreen #category { width: 24; }
    ScanScreen #min-size { width: 28; }
    ScanScreen #min-size.-invalid { border: tall $error; }
    ScanScreen #summary { padding: 1 2; width: 1fr; text-align: right; }
    ScanScreen DataTable { height: 1fr; }
    ScanScreen #detail { height: auto; max-height: 12; padding: 0 1; border-top: solid $primary; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Scanning…", id="banner")
        with Horizontal(id="filters"):
            yield Select([(c, c) for c in CATEGORIES], prompt="All categories", id="category")
            yield Input(placeholder="Min size e.g. 500MiB", id="min-size")
            yield Static("", id="summary")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield VerticalScroll(Static("", id="detail-text"), id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("", key="sel", width=1)
        table.add_column("Size", key="size", width=11)
        table.add_column("Category", key="category", width=9)
        table.add_column("Status", key="status", width=14)
        table.add_column("Target", key="id")
        self.min_size = 0
        self.category: str | None = None
        table.focus()
        self.rescan()

    # Scanning -----------------------------------------------------------------

    def rescan(self) -> None:
        self.query_one(DataTable).loading = True
        self.query_one("#banner", Static).update("Scanning…")
        self.scan_worker()

    @work(thread=True, exclusive=True, group="scan")
    def scan_worker(self) -> None:
        try:
            candidates, errors = collect(self.tidy.profile)
            view = self.tidy.processes()
        except (TidyError, OSError) as exc:
            self.app.call_from_thread(self.scan_failed, exc)
            return
        self.app.call_from_thread(self.scan_done, candidates, errors, view)

    def scan_failed(self, exc: Exception) -> None:
        self.query_one(DataTable).loading = False
        self.query_one("#banner", Static).update(plain(f"Scan failed: {exc}", "bold red"))

    def scan_done(self, candidates, errors, view) -> None:
        self.tidy.set_scan(candidates, errors, view)
        table = self.query_one(DataTable)
        table.loading = False
        self.update_banner()
        self.refresh_table()
        if not isinstance(self.focused, Input):
            table.focus()

    def update_banner(self) -> None:
        banner = self.query_one("#banner", Static)
        view, errors = self.tidy.view, self.tidy.errors
        text = Text()
        if view is not None and view.errors:
            text.append("⚠ Process visibility insufficient", "bold")
            text.append(" — preflight will skip cleanup until this is resolved (see Doctor).")
        else:
            text.append("✓ Process visibility verified", "bold")
        for error in errors:
            text.append("\n⚠ ")
            text.append(safe(error))
        warn = bool(errors or (view is not None and view.errors))
        banner.set_class(warn, "warning")
        banner.set_class(not warn, "ok")
        banner.update(text)

    # Table --------------------------------------------------------------------

    def visible(self) -> list[Candidate]:
        return filter_candidates(self.tidy.candidates, self.min_size, self.category)

    def mark(self, candidate: Candidate) -> str:
        if not candidate.eligible:
            return "–"
        return "☑" if candidate.target.id in self.tidy.selected else "☐"

    def refresh_table(self) -> None:
        table = self.query_one(DataTable)
        current = self.current()
        table.clear()
        for candidate in self.visible():
            status = (
                "candidate"
                if candidate.eligible
                else ("report-only" if candidate.target.kind == "review" else "blocked")
            )
            table.add_row(
                self.mark(candidate),
                human(candidate.size.allocated_bytes),
                plain(candidate.target.category),
                Text(status, style="" if candidate.eligible else "dim"),
                plain(candidate.target.id),
                key=candidate.target.id,
            )
        if current is not None and current.target.id in table.rows:
            table.move_cursor(row=table.get_row_index(current.target.id))
        self.update_summary()
        self.update_detail()

    def current(self) -> Candidate | None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return next((c for c in self.tidy.candidates if c.target.id == key), None)

    def update_summary(self) -> None:
        chosen = self.tidy.selected_candidates()
        total = sum(c.size.allocated_bytes for c in chosen)
        shown = len(self.visible())
        self.query_one("#summary", Static).update(
            f"{shown} shown · selected {len(chosen)} · {human(total)}"
        )

    def update_detail(self) -> None:
        candidate = self.current()
        detail = self.query_one("#detail-text", Static)
        if candidate is None:
            detail.update("No targets match the current filter.")
            return
        size = candidate.size
        target = candidate.target
        text = Text()
        text.append("Path: ", "bold")
        text.append(safe(target.path))
        text.append("\nSize: ", "bold")
        text.append(
            f"{human(size.allocated_bytes)} allocated · {human(size.logical_bytes)} logical · "
            f"{size.files:,} files"
        )
        text.append("\nStatus: ", "bold")
        text.append(safe(candidate.reason))
        if target.kind == "review":
            text.append("\nReport only: ", "bold")
            text.append("this app never deletes it.")
        else:
            text.append("\nIf cleaned: ", "bold")
            text.append(safe(target.consequence))
        if candidate.eligible:
            try:
                operation = Handler(self.tidy.profile).preview(target)
                text.append("\nOperation: ", "bold")
                text.append(safe(" ".join(str(part) for part in operation)))
            except (TidyError, OSError):
                pass
        for error in size.errors:
            text.append("\nMeasurement: ", "bold yellow")
            text.append(safe(error))
        detail.update(text)

    # Events and actions -------------------------------------------------------

    @on(DataTable.RowHighlighted)
    def row_highlighted(self) -> None:
        self.update_detail()

    @on(DataTable.RowSelected)
    def row_selected(self) -> None:
        self.action_toggle()

    @on(Select.Changed, "#category")
    def category_changed(self, event: Select.Changed) -> None:
        self.category = None if event.value is Select.NULL else str(event.value)
        self.refresh_table()

    @on(Input.Changed, "#min-size")
    def min_size_changed(self, event: Input.Changed) -> None:
        try:
            self.min_size = parse_size(event.value)
        except ValueError:
            event.input.add_class("-invalid")
            return
        event.input.remove_class("-invalid")
        self.refresh_table()

    def action_toggle(self) -> None:
        candidate = self.current()
        if candidate is None:
            return
        if not candidate.eligible:
            self.notify(
                f"Not selectable: {safe(candidate.reason)}", severity="warning", markup=False
            )
            return
        selected = self.tidy.selected
        selected.symmetric_difference_update({candidate.target.id})
        self.query_one(DataTable).update_cell(candidate.target.id, "sel", self.mark(candidate))
        self.update_summary()

    def action_rescan(self) -> None:
        self.rescan()

    def action_preview(self) -> None:
        chosen = self.tidy.selected_candidates()
        if not chosen:
            self.notify("Select at least one candidate with Space first.", severity="warning")
            return
        self.app.push_screen(PreviewScreen(chosen))

    def action_doctor(self) -> None:
        self.app.push_screen(InfoScreen("Doctor", doctor_report))

    def action_history(self) -> None:
        self.app.push_screen(InfoScreen("History", history_report))

    def action_config(self) -> None:
        self.app.push_screen(InfoScreen("Config", config_report))


class PreviewScreen(TidyScreen):
    BINDINGS = [Binding("escape", "back", "Back")]
    DEFAULT_CSS = """
    PreviewScreen #body { padding: 1 2; }
    PreviewScreen #buttons { height: auto; padding: 0 2; }
    PreviewScreen Button { margin-right: 2; }
    """

    def __init__(self, selected: list[Candidate]):
        super().__init__()
        self.selected = selected
        self.eligible: list[Candidate] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("Running safety preflight…", id="body")
        with Horizontal(id="buttons"):
            yield Button("Back", id="back")
            if not self.tidy.dry_run:
                yield Button("Clean…", id="clean", variant="error", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.preflight_worker()

    @work(thread=True, exclusive=True, group="preflight")
    def preflight_worker(self) -> None:
        try:
            checked = preflight_selected(self.selected, self.tidy.profile, self.tidy.processes())
        except (TidyError, OSError) as exc:
            self.app.call_from_thread(
                self.query_one("#body", Static).update, plain(f"Preflight failed: {exc}", "red")
            )
            return
        self.app.call_from_thread(self.show, checked)

    def show(self, checked: list[tuple[Candidate, str | None, str | None]]) -> None:
        text = Text()
        self.eligible = []
        for candidate, operation, skip in checked:
            target = candidate.target
            if operation is not None:
                self.eligible.append(candidate)
                text.append("PASS ", "bold green")
            else:
                text.append("SKIP ", "bold yellow")
            text.append(safe(target.id), "bold")
            text.append(f"  {human(candidate.size.allocated_bytes)}\n")
            text.append(f"     {safe(target.path)}\n")
            text.append(f"     {safe(target.consequence)}\n", "italic")
            if operation is not None:
                text.append(f"     Operation: {safe(operation)}\n\n")
            else:
                text.append(f"     {safe(skip)}\n\n", "yellow")
        total = sum(c.size.allocated_bytes for c in self.eligible)
        text.append(
            f"{len(self.eligible)} of {len(checked)} target(s) passed; "
            f"estimated allocated footprint {human(total)}.\n",
            "bold",
        )
        if self.tidy.dry_run:
            text.append("Dry run: no cleanup or history writes will be performed.", "bold cyan")
        elif self.eligible:
            text.append(
                "Permanent cleanup: no undo. Future installs/builds may download or "
                "rebuild files. Checks run again before each operation.",
                "bold red",
            )
        else:
            text.append("No selected targets passed safety checks; nothing can be cleaned.")
        self.query_one("#body", Static).update(text)
        if not self.tidy.dry_run:
            self.query_one("#clean", Button).disabled = not self.eligible

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back")
    def back_pressed(self) -> None:
        self.action_back()

    @on(Button.Pressed, "#clean")
    def clean_pressed(self) -> None:
        eligible = list(self.eligible)
        total = sum(c.size.allocated_bytes for c in eligible)

        def confirmed(ok: bool | None) -> None:
            if ok:
                self.app.switch_screen(RunScreen(eligible))
            else:
                self.notify("Cancelled; nothing changed.")

        self.app.push_screen(ConfirmModal(len(eligible), total), confirmed)


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal #dialog {
        width: 64; height: auto; padding: 1 2;
        border: thick $error; background: $surface;
    }
    ConfirmModal #buttons { height: auto; margin-top: 1; }
    ConfirmModal Button { margin-right: 2; }
    """

    def __init__(self, count: int, total: int):
        super().__init__()
        self.count = count
        self.total = total

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(
                f"Permanently clean {self.count} target(s), about {human(self.total)}?\n"
                "There is no undo. Type CLEAN to continue."
            )
            yield Input(placeholder="CLEAN", id="word")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Clean", id="confirm", variant="error", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#word", Input).focus()

    @on(Input.Changed, "#word")
    def word_changed(self, event: Input.Changed) -> None:
        self.query_one("#confirm", Button).disabled = event.value.strip() != "CLEAN"

    @on(Input.Submitted, "#word")
    def word_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip() == "CLEAN":
            self.dismiss(True)

    @on(Button.Pressed, "#confirm")
    def confirm_pressed(self) -> None:
        if self.query_one("#word", Input).value.strip() == "CLEAN":
            self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(False)


class RunScreen(TidyScreen):
    BINDINGS = [Binding("escape", "done", "Back to scan")]
    DEFAULT_CSS = """
    RunScreen #status { padding: 0 1; height: auto; }
    RunScreen RichLog { height: 1fr; }
    RunScreen #buttons { height: auto; padding: 0 1; }
    RunScreen Button { margin-right: 2; }
    """

    def __init__(self, eligible: list[Candidate]):
        super().__init__()
        self.eligible = eligible
        self.stop_requested = False
        self.result_code: int | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Cleaning {len(self.eligible)} target(s)…", id="status")
        yield RichLog(id="log", wrap=True)
        with Horizontal(id="buttons"):
            yield Button("Stop after current target", id="stop", variant="warning")
            yield Button("Done", id="done", variant="primary", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.tidy.running = True
        self.clean_worker()

    @work(thread=True, exclusive=True, group="clean")
    def clean_worker(self) -> None:
        try:
            run = self.tidy.cleaner(
                self.eligible,
                self.tidy.profile,
                self.tidy.store,
                processes=self.tidy.processes,
                on_result=lambda r: self.app.call_from_thread(self.add_result, r),
                should_stop=lambda: self.stop_requested,
            )
        except (TidyError, OSError) as exc:
            self.app.call_from_thread(self.failed, exc)
        else:
            self.app.call_from_thread(self.finished, run)

    def add_result(self, result: dict) -> None:
        line = Text()
        line.append(f"{result['outcome'].upper():<11}", OUTCOME_STYLES.get(result["outcome"], ""))
        line.append(safe(result["target_id"]), "bold")
        line.append(f"  {safe(result['path'])}\n           {safe(result['reason'])}")
        self.query_one(RichLog).write(line)

    def finish_ui(self, status: Text) -> None:
        self.tidy.running = False
        self.query_one("#status", Static).update(status)
        self.query_one("#stop", Button).disabled = True
        done = self.query_one("#done", Button)
        done.disabled = False
        done.focus()

    def failed(self, exc: Exception) -> None:
        self.result_code = 1
        self.finish_ui(plain(f"Cleanup did not start: {exc}", "bold red"))

    def finished(self, run: dict) -> None:
        log = self.query_one(RichLog)
        for fs in run["filesystems"].values():
            delta = fs["available_change_bytes"]
            after = fs["after_available_bytes"]
            log.write(
                plain(
                    f"Filesystem {fs['path']}: free-space change "
                    f"{human(delta) if delta is not None else 'unverified'}; available now "
                    f"{human(after) if after is not None else 'unverified'}"
                )
            )
        log.write(
            Text(
                "Free-space changes may include concurrent activity and differ from estimates.",
                "dim",
            )
        )
        cleaned = all(r["outcome"] == "cleaned" for r in run["results"])
        self.result_code = 0 if cleaned else 1
        if run["status"] == "interrupted":
            status = Text("Stopped. Completed cleanup is kept in history.", "bold yellow")
        elif cleaned:
            status = Text("All selected targets cleaned and verified.", "bold green")
        else:
            status = Text("Finished; some targets were not fully cleaned.", "bold yellow")
        self.finish_ui(status)

    @on(Button.Pressed, "#stop")
    def stop_pressed(self) -> None:
        self.stop_requested = True
        self.query_one("#stop", Button).disabled = True
        self.query_one("#status", Static).update(
            "Stopping after the current target. The running operation is not interrupted."
        )

    @on(Button.Pressed, "#done")
    def action_done(self) -> None:
        if self.tidy.running:
            self.notify("Cleanup is still running.", severity="warning")
            return
        self.tidy.return_to_scan()


class InfoScreen(TidyScreen):
    """Read-only report loaded in a background thread."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("q", "back", "Back")]
    DEFAULT_CSS = "InfoScreen #body { padding: 1 2; }"

    def __init__(self, title: str, loader: Callable[[TidyApp], Text]):
        super().__init__()
        self.report_title = title
        self.loader = loader

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static(f"Loading {self.report_title.lower()}…", id="body")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.report_title
        self.load()

    @work(thread=True)
    def load(self) -> None:
        try:
            text = self.loader(self.tidy)
        except (TidyError, OSError) as exc:
            text = plain(f"Error: {exc}", "bold red")
        self.app.call_from_thread(self.query_one("#body", Static).update, text)

    def action_back(self) -> None:
        self.app.pop_screen()


def history_report(app: TidyApp) -> Text:
    records = app.store.read()
    if not records:
        return Text("No cleanup history yet.")
    text = Text()
    for run in reversed(records):
        text.append(safe(f"{run.get('started_at')}  {run.get('status')}"), "bold")
        text.append(safe(f"  profile={run.get('profile')}\n"))
        for result in run.get("results", []):
            outcome = str(result.get("outcome"))
            text.append(f"  {outcome:<11}", OUTCOME_STYLES.get(outcome, ""))
            text.append(safe(f"{result.get('path')} — {result.get('reason')}\n"))
        text.append("\n")
    return text


def doctor_report(app: TidyApp) -> Text:
    view = app.processes()
    candidates, errors = collect(app.profile)
    text = Text()
    text.append("Tools\n", "bold underline")
    for name in ("npm", "uv", "pip3", "cargo", "git"):
        path = shutil.which(name)
        text.append(f"  {name:<6} ")
        text.append(safe(path) if path else "missing", "" if path else "yellow")
        text.append("\n")
    text.append("\nProcess visibility: ", "bold underline")
    if view.errors:
        text.append("insufficient\n", "bold red")
        for error in view.errors:
            text.append(f"  {safe(error)}\n")
        text.append(
            "  Cleanup is deferred while processes cannot be fully inspected. Run from your "
            "normal host terminal without sudo.\n"
        )
    else:
        text.append("verified\n", "bold green")
    text.append("\nDiscovery\n", "bold underline")
    for error in errors or ["No discovery errors."]:
        text.append(f"  {safe(error)}\n")
    text.append("\nTargets (preliminary)\n", "bold underline")
    for candidate in candidates:
        text.append(
            "  ✓ " if candidate.eligible else "  – ", "green" if candidate.eligible else "dim"
        )
        text.append(safe(candidate.target.id), "bold")
        text.append(f": {safe(candidate.reason)}\n")
    return text


def config_report(app: TidyApp) -> Text:
    profile = app.profile
    path = app.config_path
    text = Text()
    text.append("Config file: ", "bold")
    if path is None:
        text.append("default location\n")
    else:
        text.append(safe(path))
        text.append(" (missing; using defaults)\n" if not path.exists() else "\n")
    text.append("Profile: ", "bold")
    text.append(f"{safe(profile.name)}\n")
    text.append("Discover Cargo: ", "bold")
    text.append(f"{profile.discover_cargo}\n")
    text.append("\nProject roots\n", "bold underline")
    for root in profile.project_roots or ["(none)"]:
        text.append(f"  {safe(root)}\n")
    text.append("\nExclusions\n", "bold underline")
    for excluded in profile.exclude or ["(none)"]:
        text.append(f"  {safe(excluded)}\n")
    text.append("\nTargets\n", "bold underline")
    for target in profile.targets:
        text.append(f"  {safe(target.id)}", "bold")
        text.append(f"  [{safe(target.kind)}]  {safe(target.path)}\n")
    text.append("\nEdit the TOML file to customize; the app never writes configuration.", "dim")
    return text
