import unittest

from host.fd2_v1_presets import PRESETS, load_preset


class Variable:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class Tabs:
    def select(self, index):
        self.selected = index


class App:
    def __init__(self):
        self.swing_values = {(profile, field): Variable()
                             for preset in PRESETS.values()
                             for profile, fields in [preset]
                             for field in fields}
        self.swing_tabs = Tabs()


class V1PresetTests(unittest.TestCase):
    def test_each_preset_only_populates_its_own_bank_and_selects_tab(self):
        for name, (profile, fields) in PRESETS.items():
            with self.subTest(name=name):
                app = App()
                load_preset(app, name)
                self.assertEqual(app.swing_tabs.selected, profile)
                for key, variable in app.swing_values.items():
                    expected = str(fields[key[1]]) if key[0] == profile else None
                    self.assertEqual(variable.value, expected)

    def test_missing_field_does_not_partially_update_drafts(self):
        app = App()
        del app.swing_values[(0, 9)]
        with self.assertRaises(ValueError):
            load_preset(app, 'A')
        self.assertTrue(all(v.value is None for v in app.swing_values.values()))
        self.assertFalse(hasattr(app.swing_tabs, 'selected'))

    def test_preset_values_match_the_handoff(self):
        self.assertEqual(PRESETS['A'], (0, {0: 70, 1: 60, 2: 248, 3: 1, 4: 3000, 5: 230, 6: 1800, 8: 1, 9: 10}))
        self.assertEqual(PRESETS['B'], (2, {0: 71, 64: 90, 65: 97, 66: 97}))
        self.assertEqual(PRESETS['C'], (0, {0: 64, 1: 60, 2: 232, 3: 1, 4: 3000, 5: 240, 6: 1800, 8: 1, 9: 10}))


if __name__ == '__main__':
    unittest.main()
