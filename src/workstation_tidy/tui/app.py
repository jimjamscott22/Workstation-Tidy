from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from ..config import Profile, state_path
from ..models import Candidate
from ..safety import ProcessView, process_view
from ..service import clean
from ..storage import Store


class TidyApp(App[int]):
    """Interactive front end for the scan → preview → confirm → clean flow.

    All safety decisions stay in the core modules; this class only holds UI state.
    """

    TITLE = "Workstation Tidy"
    BINDINGS = [Binding("ctrl+q", "quit", "Quit", show=False, priority=True)]

    def __init__(
        self,
        profile: Profile,
        *,
        dry_run: bool = False,
        config_path: Path | None = None,
        store: Store | None = None,
        processes: Callable[[], ProcessView] = process_view,
        cleaner: Callable[..., dict] = clean,
    ):
        super().__init__()
        self.profile = profile
        self.dry_run = dry_run
        self.config_path = config_path
        self.store = store or Store(state_path())
        self.processes = processes
        self.cleaner = cleaner
        self.candidates: list[Candidate] = []
        self.errors: list[str] = []
        self.view: ProcessView | None = None
        self.selected: set[str] = set()
        self.running = False
        self.result_code = 0
        self.sub_title = f"profile: {profile.name}" + (" · DRY RUN" if dry_run else "")

    def on_mount(self) -> None:
        from .screens import ScanScreen

        self.push_screen(ScanScreen())

    def selected_candidates(self) -> list[Candidate]:
        return [c for c in self.candidates if c.target.id in self.selected and c.eligible]

    def set_scan(self, candidates: list[Candidate], errors: list[str], view: ProcessView) -> None:
        self.candidates = sorted(candidates, key=lambda c: c.size.allocated_bytes, reverse=True)
        self.errors = errors
        self.view = view
        eligible = {c.target.id for c in self.candidates if c.eligible}
        self.selected &= eligible

    def return_to_scan(self) -> None:
        from .screens import ScanScreen

        while len(self.screen_stack) > 1 and not isinstance(self.screen, ScanScreen):
            self.pop_screen()
        if isinstance(self.screen, ScanScreen):
            self.screen.rescan()

    async def action_quit(self) -> None:
        if self.running:
            self.notify(
                "Cleanup is running. Use 'Stop after current target' and wait for it to finish.",
                severity="warning",
            )
            return
        self.exit(self.result_code, return_code=self.result_code)
