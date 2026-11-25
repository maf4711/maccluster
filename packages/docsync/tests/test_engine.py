import unittest

from docsync import engine


class PlanTests(unittest.TestCase):
    def test_first_sync_union_no_deletes(self):
        p = engine.plan({"a": (1, 1)}, {"b": (2, 2)}, None)
        self.assertEqual(p.push, ["a"])
        self.assertEqual(p.pull, ["b"])
        self.assertEqual(p.delete_local, [])
        self.assertEqual(p.delete_remote, [])

    def test_first_sync_same_file_both_sides_differs_is_conflict_newest_wins(self):
        p = engine.plan({"a": (10, 1)}, {"a": (20, 1)}, None)
        self.assertEqual(p.conflicts, [("a", "remote")])

    def test_unchanged(self):
        self.assertTrue(engine.plan({"a": (1, 1)}, {"a": (1, 1)}, {"a": (1, 1)}).empty)

    def test_local_edit_pushes(self):
        p = engine.plan({"a": (5, 9)}, {"a": (1, 1)}, {"a": (1, 1)})
        self.assertEqual(p.push, ["a"])

    def test_remote_edit_pulls(self):
        p = engine.plan({"a": (1, 1)}, {"a": (5, 9)}, {"a": (1, 1)})
        self.assertEqual(p.pull, ["a"])

    def test_both_edited_conflict_policy(self):
        for policy, winner in (("newest", "remote"), ("local", "local"), ("remote", "remote")):
            p = engine.plan({"a": (5, 9)}, {"a": (7, 3)}, {"a": (1, 1)}, policy)
            self.assertEqual(p.conflicts, [("a", winner)], policy)

    def test_deleted_locally_propagates(self):
        p = engine.plan({}, {"a": (1, 1)}, {"a": (1, 1)})
        self.assertEqual(p.delete_remote, ["a"])

    def test_deleted_remotely_propagates(self):
        p = engine.plan({"a": (1, 1)}, {}, {"a": (1, 1)})
        self.assertEqual(p.delete_local, ["a"])

    def test_deleted_remotely_but_edited_locally_keeps_local(self):
        p = engine.plan({"a": (9, 9)}, {}, {"a": (1, 1)})
        self.assertEqual(p.push, ["a"])
        self.assertEqual(p.delete_local, [])

    def test_deleted_locally_but_edited_remotely_pulls(self):
        p = engine.plan({}, {"a": (9, 9)}, {"a": (1, 1)})
        self.assertEqual(p.pull, ["a"])

    def test_deleted_both_sides_is_noop(self):
        self.assertTrue(engine.plan({}, {}, {"a": (1, 1)}).empty)

    def test_new_on_both_sides_after_base_is_conflict(self):
        p = engine.plan({"n": (1, 1)}, {"n": (2, 2)}, {"x": (0, 0)})
        self.assertEqual(p.conflicts, [("n", "remote")])


class ConflictNameTests(unittest.TestCase):
    def test_with_extension(self):
        self.assertEqual(engine.conflict_name("a/b/report.docx", "T", "mac"), "a/b/report.conflict-T-mac.docx")

    def test_without_extension(self):
        self.assertEqual(engine.conflict_name("notes", "T", "mac"), "notes.conflict-T-mac")

    def test_dotfile(self):
        self.assertEqual(engine.conflict_name(".env", "T", "mac"), ".env.conflict-T-mac")


if __name__ == "__main__":
    unittest.main()
