import unittest

from recorder import todos


class TodosTests(unittest.TestCase):
    def test_people_returns_only_the_current_user(self):
        cfg = {"my_name": "Jordan", "todo_people": "Tyler, Luwam"}

        self.assertEqual(todos.people(cfg), ["Jordan"])


if __name__ == "__main__":
    unittest.main()
