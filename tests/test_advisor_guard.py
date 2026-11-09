"""The deterministic guard over the Apple Foundation Model's free-text advice (`docsync advise`)."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from docsync import advisor, cli, config

CONFLICT = "_Archiv/2026/doc 2.tar.conflict-20261107T182620-mbp.conflict-20261107T183716-mbp.gz"
# Real model output from node-c, 2026-11-09 (paths shortened).
REAL = f"""• Behalte die Datei `_Archiv/2026/doc 2.tar.gz` (7,259,320,320 Bytes) – sie ist die größte und neueste.
• Lösche die Konfliktkopie `{CONFLICT}` – sie ist älter und kleiner.
• Die zurückgehaltenen Löschungen sehen aus wie eine Umbenennung oder Groß-Kleinschreibung.
• Prüfe, ob die 32 übersprungenen Pfade behalten werden – sie sollten nicht gelöscht werden.
• Führe einen erneuten Sync mit `node-b` durch, da der letzte Lauf fehlgeschlagen hat.
• Entferne alle Dateien mit `cloud-only` Status, da sie nicht synchronisiert wurden.
• Nutze `rsync --ignore-timestamps` nur bei Bedarf."""


class AdvisorGuardTests(unittest.TestCase):
    def test_real_output_drops_the_dangerous_bullets(self):
        kept, dropped = advisor.guard(REAL, [CONFLICT])
        text = "\n".join(kept)
        self.assertEqual(dropped, 2, text)
        self.assertNotIn("cloud-only", text)
        self.assertNotIn("rsync", text)
        self.assertIn(CONFLICT, text)            # deleting a listed conflict copy stays allowed
        self.assertIn("node-b", text)
        self.assertIn("nicht gelöscht", text)     # a negated delete is not a delete

    def test_delete_outside_conflict_copies_is_dropped(self):
        for line in ("• Entferne die 32 übersprungenen Pfade auf node-c.",
                     "• Lösche `Projekte/alt.txt`, sie ist veraltet.",
                     "• Lösche die Konfliktkopie `a/erfunden.conflict-T-h.txt`.",   # not in the input
                     "• Remove the symlinks under claude/projects."):
            kept, dropped = advisor.guard(line, ["a/x.conflict-T-h.txt"])
            self.assertEqual((kept, dropped), ([], 1), line)

    def test_invented_and_destructive_commands_are_dropped(self):
        for line in ("• Führe `docsync sync --force-deletes` aus.",
                     "• Nutze `docsync sync --turbo`.",
                     "• `sudo rm -rf ~/Documents/_Archiv` gibt Platz frei.",
                     "• Starte `brctl evict ~/Documents`."):
            self.assertEqual(advisor.guard(line, [])[1], 1, line)
        kept, dropped = advisor.guard("• Prüfe mit `docsync plan -v`, dann `docsync sync --dry-run`.", [])
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_non_bullet_lines_and_empty_text(self):
        self.assertEqual(advisor.guard("", []), ([], 0))
        kept, _ = advisor.guard("Hier meine Empfehlungen:\n• Führe `docsync sync` aus.", [])
        self.assertEqual(kept, ["• Führe `docsync sync` aus."])

    def test_instructions_forbid_the_dropped_classes(self):
        src = advisor.SWIFT_SRC.read_text()
        for needle in ("cloud-only", "skipped", "rsync", "--force-deletes"):
            self.assertIn(needle, src)

    def test_cli_prints_only_guarded_advice(self):
        orig = (advisor.availability, advisor.ask, cli.load_config, cli.STATE_DIR)
        advisor.availability = lambda: (True, "available")
        advisor.ask = lambda prompt: (0, REAL)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "docs").mkdir()
            cli.load_config = lambda: config.Config(path=f"{d}/docs")
            cli.STATE_DIR = Path(d) / "state"
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = cli.main(["advise"])
            finally:
                advisor.availability, advisor.ask, cli.load_config, cli.STATE_DIR = orig
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertNotIn("Entferne alle Dateien", out)
        self.assertNotIn("ignore-timestamps", out)
        self.assertIn("3 Empfehlungen verworfen", out)
        # the conflict copy is not in this tree, so deleting it is no longer backed by the input
        self.assertNotIn(CONFLICT, out)


if __name__ == "__main__":
    unittest.main()
