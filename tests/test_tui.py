import asyncio
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from workstation_tidy.cli import main
from workstation_tidy.config import load_profile
from workstation_tidy.handlers import Handler
from workstation_tidy.safety import ProcessView
from workstation_tidy.service import clean
from workstation_tidy.storage import Store

HAS_TEXTUAL = importlib.util.find_spec("textual") is not None


class TuiFixture:
    def make_home(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = self.home / "config.toml"
        self.config.write_text(
            "version=1\n[profiles.usual]\ncargo_targets=[]\nproject_roots=[]\nreview=false\n"
            '[profiles.usual.caches]\nthumbnails="~/.cache/thumbnails"\n'
        )
        self.cache = self.home / ".cache/thumbnails/normal"
        self.cache.mkdir(parents=True)
        self.file = self.cache / "fixture.png"
        self.file.write_bytes(b"preview" * 1024)
        patcher = patch("pathlib.Path.home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = self.home / "state"


@unittest.skipUnless(HAS_TEXTUAL, "textual is not installed")
class TuiTests(TuiFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.make_home()

    def app(self, dry_run=False, processes=ProcessView, cleaner=clean, store=None):
        from workstation_tidy.tui import TidyApp

        profile = load_profile("usual", self.config)
        return TidyApp(
            profile,
            dry_run=dry_run,
            config_path=self.config,
            store=store or Store(self.state),
            processes=processes,
            cleaner=cleaner,
        )

    async def scanned(self, pilot):
        from workstation_tidy.tui.screens import ScanScreen

        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        screen = pilot.app.screen
        self.assertIsInstance(screen, ScanScreen)
        return screen

    async def settle(self, pilot):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

    async def start_clean(self, pilot):
        await self.scanned(pilot)
        await pilot.press("space", "p")
        await self.settle(pilot)
        await pilot.click("#clean")
        await pilot.pause()
        await pilot.press(*"CLEAN")
        self.assertFalse(pilot.app.screen.query_one("#confirm").disabled)
        await pilot.press("enter")

    async def test_scan_lists_fixture_with_nothing_selected(self):
        app = self.app()
        async with app.run_test() as pilot:
            await self.scanned(pilot)
            table = app.screen.query_one("DataTable")
            self.assertEqual(table.row_count, 1)
            self.assertEqual(app.selected, set())
            await pilot.press("space")
            self.assertEqual(app.selected, {"thumbnails"})
            await pilot.press("space")
            self.assertEqual(app.selected, set())

    async def test_blocked_rows_cannot_be_selected(self):
        app = self.app()
        async with app.run_test() as pilot:
            await self.scanned(pilot)
            app.candidates[0].eligible = False
            await pilot.press("space")
            self.assertEqual(app.selected, set())

    async def test_dry_run_preview_shows_operation_without_changes(self):
        from workstation_tidy.tui.screens import PreviewScreen

        app = self.app(dry_run=True)
        async with app.run_test() as pilot:
            await self.scanned(pilot)
            await pilot.press("space", "p")
            await self.settle(pilot)
            self.assertIsInstance(app.screen, PreviewScreen)
            body = str(app.screen.query_one("#body").render())
            self.assertIn("PASS", body)
            self.assertIn("Operation:", body)
            self.assertIn("Dry run", body)
            self.assertFalse(app.screen.query("#clean"))
        self.assertTrue(self.file.exists())
        self.assertFalse(self.state.exists())

    async def test_confirm_requires_exact_word_and_cancel_changes_nothing(self):
        from workstation_tidy.tui.screens import ConfirmModal, PreviewScreen

        app = self.app()
        async with app.run_test() as pilot:
            await self.scanned(pilot)
            await pilot.press("space", "p")
            await self.settle(pilot)
            await pilot.click("#clean")
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmModal)
            confirm = app.screen.query_one("#confirm")
            await pilot.press(*"clean")
            self.assertTrue(confirm.disabled)
            await pilot.press("enter")
            self.assertIsInstance(app.screen, ConfirmModal)
            await pilot.press("escape")
            await pilot.pause()
            self.assertIsInstance(app.screen, PreviewScreen)
        self.assertTrue(self.file.exists())
        self.assertFalse(self.state.exists())

    async def test_full_flow_cleans_and_records_history(self):
        from workstation_tidy.tui.screens import RunScreen, ScanScreen

        app = self.app()
        async with app.run_test() as pilot:
            await self.start_clean(pilot)
            await self.settle(pilot)
            self.assertIsInstance(app.screen, RunScreen)
            self.assertEqual(app.screen.result_code, 0)
            self.assertEqual(app.result_code, 0)
            self.assertFalse(app.running)
            await pilot.click("#done")
            await self.settle(pilot)
            self.assertIsInstance(app.screen, ScanScreen)
            await pilot.press("q")
        self.assertEqual(app.return_value, 0)
        self.assertEqual(app.return_code, 0)
        self.assertFalse(self.file.exists())
        records = Store(self.state).read()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["results"][0]["outcome"], "cleaned")

    async def test_exit_code_nonzero_after_failed_skipped_or_stopped_cleanup(self):
        from workstation_tidy.tui.screens import RunScreen

        with patch.object(Handler, "execute", return_value=2):
            failed_app = self.app()
            async with failed_app.run_test() as pilot:
                await self.start_clean(pilot)
                await self.settle(pilot)
                self.assertIsInstance(failed_app.screen, RunScreen)
                self.assertEqual(failed_app.screen.result_code, 1)
                await pilot.click("#done")
                await self.settle(pilot)
                await pilot.press("q")
            self.assertEqual(failed_app.return_value, 1)
            self.assertEqual(failed_app.return_code, 1)

        views = iter(
            [
                ProcessView(),
                ProcessView(),
                ProcessView(errors=["cannot inspect PID 1"]),
                ProcessView(),
            ]
        )
        skipped_app = self.app(processes=lambda: next(views))
        async with skipped_app.run_test() as pilot:
            await self.start_clean(pilot)
            await self.settle(pilot)
            self.assertIsInstance(skipped_app.screen, RunScreen)
            self.assertEqual(skipped_app.screen.result_code, 1)
            await pilot.click("#done")
            await self.settle(pilot)
            await pilot.press("q")
        self.assertEqual(skipped_app.return_value, 1)
        self.assertEqual(skipped_app.return_code, 1)

        def stopped_cleaner(candidates, profile, store, **kwargs):
            kwargs["should_stop"] = lambda: True
            return clean(candidates, profile, store, **kwargs)

        stopped_app = self.app(cleaner=stopped_cleaner)
        async with stopped_app.run_test() as pilot:
            await self.start_clean(pilot)
            await self.settle(pilot)
            self.assertIsInstance(stopped_app.screen, RunScreen)
            self.assertEqual(stopped_app.screen.result_code, 1)
            await pilot.click("#done")
            await self.settle(pilot)
            await pilot.press("q")
        self.assertEqual(stopped_app.return_value, 1)
        self.assertEqual(stopped_app.return_code, 1)
        self.assertTrue(self.file.exists())

    async def test_oserror_before_vs_after_execution_reports_accurate_status(self):
        from workstation_tidy.tui.screens import RunScreen

        store_before = Store(self.state)
        with patch.object(store_before, "save", side_effect=OSError("cannot write initial state")):
            app_before = self.app(store=store_before)
            async with app_before.run_test() as pilot:
                await self.start_clean(pilot)
                await self.settle(pilot)
                self.assertIsInstance(app_before.screen, RunScreen)
                status = str(app_before.screen.query_one("#status").render())
                self.assertIn("Cleanup did not start: cannot write initial state", status)
                self.assertEqual(app_before.screen.result_code, 1)
                await pilot.press("q")
            self.assertEqual(app_before.return_value, 1)
            self.assertEqual(app_before.return_code, 1)
        self.assertTrue(self.file.exists())

        store_after = Store(self.state)
        real_save = store_after.save
        calls = 0

        def fail_second_save(record):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full after target")
            real_save(record)

        with patch.object(store_after, "save", side_effect=fail_second_save):
            app_after = self.app(store=store_after)
            async with app_after.run_test() as pilot:
                await self.start_clean(pilot)
                await self.settle(pilot)
                self.assertIsInstance(app_after.screen, RunScreen)
                status = str(app_after.screen.query_one("#status").render())
                self.assertIn(
                    "Cleanup failed and may have partially completed: disk full after target",
                    status,
                )
                self.assertNotIn("Cleanup did not start", status)
                self.assertEqual(app_after.screen.result_code, 1)
                await pilot.press("q")
            self.assertEqual(app_after.return_value, 1)
            self.assertEqual(app_after.return_code, 1)
        self.assertFalse(self.file.exists())

    async def test_insufficient_process_visibility_is_shown_and_skips(self):
        app = self.app(processes=lambda: ProcessView(errors=["cannot inspect PID 1"]))
        async with app.run_test() as pilot:
            await self.scanned(pilot)
            banner = str(app.screen.query_one("#banner").render())
            self.assertIn("insufficient", banner)
            await pilot.press("space", "p")
            await self.settle(pilot)
            body = str(app.screen.query_one("#body").render())
            self.assertIn("SKIP", body)
            self.assertTrue(app.screen.query_one("#clean").disabled)
        self.assertTrue(self.file.exists())

    async def test_markup_in_paths_renders_literally(self):
        from workstation_tidy.tui.screens import plain

        self.assertEqual(plain("[bold]x[/bold]\x1b").plain, "[bold]x[/bold]\\u001b")

    async def test_info_screens_open(self):
        from workstation_tidy.tui.screens import InfoScreen

        app = self.app()
        async with app.run_test() as pilot:
            await self.scanned(pilot)
            for key, expected in (("h", "No cleanup history"), ("c", "thumbnails"), ("d", "Tools")):
                await pilot.press(key)
                await self.settle(pilot)
                self.assertIsInstance(app.screen, InfoScreen)
                self.assertIn(expected, str(app.screen.query_one("#body").render()))
                await pilot.press("escape")
                await pilot.pause()


class TuiCliTests(TuiFixture, unittest.TestCase):
    def setUp(self):
        self.make_home()

    def test_missing_extra_prints_install_hint(self):
        err = io.StringIO()
        with (
            redirect_stderr(err),
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch.dict("sys.modules", {"workstation_tidy.tui": None}),
        ):
            code = main(["tui", "--config", str(self.config)])
        self.assertEqual(code, 1)
        self.assertIn("uv sync --extra tui", err.getvalue())

    def test_requires_terminal(self):
        err = io.StringIO()
        with (
            redirect_stderr(err),
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
        ):
            code = main(["tui", "--config", str(self.config)])
        self.assertEqual(code, 1)
        self.assertIn("interactive terminal", err.getvalue())

    @unittest.skipUnless(HAS_TEXTUAL, "textual is not installed")
    def test_cli_tui_returns_cleanup_exit_code(self):
        from workstation_tidy.tui import TidyApp

        def drive_app(app: TidyApp) -> int | None:
            app.store = Store(self.state)
            app.processes = ProcessView

            async def run_pilot() -> int | None:
                async with app.run_test() as pilot:
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    await pilot.press("space", "p")
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    await pilot.click("#clean")
                    await pilot.pause()
                    await pilot.press(*"CLEAN", "enter")
                    await pilot.app.workers.wait_for_complete()
                    await pilot.pause()
                    await pilot.press("q")
                return app.return_value

            return asyncio.run(run_pilot())

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
            patch.object(TidyApp, "run", autospec=True, side_effect=drive_app),
        ):
            with patch.object(Handler, "execute", return_value=2):
                self.assertEqual(main(["tui", "--config", str(self.config)]), 1)
            self.assertTrue(self.file.exists())
            self.assertEqual(main(["tui", "--config", str(self.config)]), 0)
            self.assertFalse(self.file.exists())
