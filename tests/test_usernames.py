import contextlib
import io
import os
import random
import re
import unittest
from unittest.mock import patch

import main
from lib.lib import Main, UsernameGenerator, mix_username_digits
from lib.username_rules import validate_username


class UsernameTests(unittest.TestCase):
    def assert_valid(self, name):
        self.assertTrue(3 <= len(name) <= 20)
        self.assertRegex(name, r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)?$")
        self.assertEqual(validate_username(name), name)

    def test_syntax_boundaries_and_ascii_digits(self):
        for name in ("abc", "9cat", "a_0", "A" * 20, "Demo_123456"):
            with self.subTest(name=name):
                self.assert_valid(name)
        for name in ("", "ab", "A" * 21, "_cat", "cat_", "cat__fox", "a_b_c",
                     "two words", "cat-fox", "cat.fox", "แมว", "猫12", "Cat１２", "Cat\n"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_username(name)

    def test_all_generated_styles_obey_rules(self):
        with patch("lib.lib.random", random.Random(42)):
            for scrambled in (True, False):
                digit_counts, underscore_counts = set(), set()
                for _ in range(300):
                    name = Main().usernameCreator(scrambled=scrambled)
                    self.assert_valid(name)
                    digits = len(re.findall(r"[0-9]", name))
                    self.assertLessEqual(digits, 3)
                    digit_counts.add(digits)
                    underscore_counts.add(name.count("_"))
                    if scrambled:
                        self.assertTrue(10 <= len(name) <= 15)
                self.assertEqual(digit_counts, {0, 1, 2, 3})
                self.assertEqual(underscore_counts, {0, 1})

    def test_generated_length_edges(self):
        with patch("lib.lib.random", random.Random(13)):
            for length in (3, 4, 19, 20):
                for _ in range(100):
                    name = UsernameGenerator(length).generate()
                    self.assert_valid(name)
                    self.assertEqual(len(name), length)
            for count in range(4):
                name = mix_username_digits("VeryLongAlphabeticName" * 3, count)
                self.assert_valid(name)
                self.assertEqual(len(re.findall(r"[0-9]", name)), count)
        for bounds in ((2, 5), (10, 21), (15, 10), (3, 0)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                UsernameGenerator(*bounds)

    def test_prefix_keeps_one_underscore_after_truncation(self):
        with patch("lib.lib.secrets.randbelow", return_value=123):
            for prefix in ("x", "7Cat", "Demo", "Demo_", "Demo_A", "abcdefghijkl_foo", "abcdefghijk_foo"):
                name = Main().usernameCreator(prefix)
                self.assert_valid(name)
                self.assertTrue(name.startswith(prefix[:12]))
                self.assertTrue(name.endswith("000123"))
            self.assertEqual(Main().usernameCreator("Demo_"), "Demo_000123")
            self.assertEqual(Main().usernameCreator("Demo_A"), "Demo_A000123")
        for prefix in ("_Demo", "A__B", "a_b_c", "Two Words", "猫", "Cat１２"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValueError):
                    Main().usernameCreator(prefix)
                with patch.dict(os.environ, {"NAME_PREFIX": prefix}, clear=True), self.assertRaisesRegex(ValueError, "NAME_PREFIX"):
                    main.gather_config(1)

    def test_missing_word_files_use_valid_fallback(self):
        with patch("builtins.open", side_effect=FileNotFoundError), contextlib.redirect_stdout(io.StringIO()):
            self.assert_valid(Main().usernameCreator(scrambled=False))
