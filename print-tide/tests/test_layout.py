"""Layout validation, one-to-one mapping, atomic persistence, undo, conflicts."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from light_studio.layout import (Conflict, DEFAULTS, LayoutStore, SCHEMA,
                                 atomic_write, default_layout, validate)
from test_core import MAP


def base():
    return default_layout(MAP)


class ValidationTests(unittest.TestCase):
    def test_the_default_layout_validates_and_is_one_to_one(self):
        clean = validate(base(), MAP)
        self.assertEqual(len(clean['slots']), 7)
        self.assertEqual({s['printer'] for s in clean['slots']}, set(MAP))
        self.assertEqual({s['node'] for s in clean['slots']},
                         {t['node'] for t in MAP.values()})

    def test_node01_is_rejected_however_it_is_spelled_in(self):
        config = base()
        config['slots'][0]['node'] = 'node01'
        with self.assertRaises(ValueError):
            validate(config, MAP)

    def test_duplicate_printer_or_duplicate_rope_is_rejected(self):
        config = base()
        config['slots'][0]['printer'] = config['slots'][1]['printer']
        with self.assertRaises(ValueError):
            validate(config, MAP)
        config = base()
        config['slots'][0]['node'] = config['slots'][1]['node']
        with self.assertRaises(ValueError):
            validate(config, MAP)

    def test_missing_or_extra_bays_are_rejected(self):
        config = base()
        config['slots'] = config['slots'][:6]
        with self.assertRaises(ValueError):
            validate(config, MAP)
        config = base()
        config['slots'].append(copy.deepcopy(config['slots'][0]))
        with self.assertRaises(ValueError):
            validate(config, MAP)

    def test_slot_shape_and_types_are_strict(self):
        for key, value in [('reverse', 'yes'), ('reverse', 1), ('label', ''),
                           ('label', ' ' * 4), ('label', 'x' * 41), ('label', 7),
                           ('printer', 'printer99'), ('printer', None),
                           ('node', 'node99'), ('node', 42)]:
            config = base()
            config['slots'][0][key] = value
            with self.assertRaises(ValueError, msg=(key, value)):
                validate(config, MAP)
        config = base()
        config['slots'][0]['extra'] = 1
        with self.assertRaises(ValueError):
            validate(config, MAP)

    def test_revision_must_be_a_non_negative_plain_integer(self):
        for value in (-1, 'x', 1.5, True, None):
            config = base()
            config['revision'] = value
            with self.assertRaises(ValueError, msg=value):
                validate(config, MAP)

    def test_settings_ranges_flags_and_unknown_keys(self):
        for key, value in [('brightness', 101), ('brightness', -1),
                           ('brightness', float('nan')), ('brightness', 'hi'),
                           ('speed', 0.1), ('speed', 9), ('ripples', 'on'),
                           ('quiet', 1), ('reduced_motion', None)]:
            config = base()
            config['settings'][key] = value
            with self.assertRaises(ValueError, msg=(key, value)):
                validate(config, MAP)
        config = base()
        config['settings']['fireworks'] = True
        with self.assertRaises(ValueError):
            validate(config, MAP)

    def test_missing_settings_are_filled_from_defaults_not_rejected(self):
        config = base()
        config['settings'] = {'brightness': 10}
        clean = validate(config, MAP)
        self.assertEqual(clean['settings']['brightness'], 10)
        self.assertEqual(clean['settings']['ripples'], DEFAULTS['ripples'])

    def test_schema_1_is_migrated_and_unknown_schemas_refused(self):
        config = base()
        config['schema'] = 1
        self.assertEqual(validate(config, MAP)['schema'], SCHEMA)
        config['schema'] = 99
        with self.assertRaises(ValueError):
            validate(config, MAP)

    def test_labels_are_trimmed_but_content_is_preserved_verbatim(self):
        config = base()
        config['slots'][0]['label'] = '  <b>Bay & Co</b>  '
        self.assertEqual(validate(config, MAP)['slots'][0]['label'], '<b>Bay & Co</b>')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = LayoutStore(self.root, MAP)

    def test_save_bumps_revision_and_survives_a_reload(self):
        config = self.store.snapshot()
        config['slots'][0]['printer'], config['slots'][1]['printer'] = \
            config['slots'][1]['printer'], config['slots'][0]['printer']
        saved = self.store.save(config)
        self.assertEqual(saved['revision'], 1)
        reloaded = LayoutStore(self.root, MAP)
        self.assertEqual(reloaded.snapshot()['slots'][0]['printer'], 'printer2')
        self.assertEqual(reloaded.snapshot()['revision'], 1)

    def test_a_stale_revision_conflicts_instead_of_overwriting(self):
        config = self.store.snapshot()
        self.store.save(copy.deepcopy(config))
        with self.assertRaises(Conflict):
            self.store.save(config)

    def test_undo_restores_the_previous_layout_and_is_itself_a_save(self):
        config = self.store.snapshot()
        config['slots'][0]['label'] = 'Left corner'
        self.store.save(config)
        undone = self.store.undo(self.store.snapshot()['revision'])
        self.assertEqual(undone['slots'][0]['label'], 'Printer 1')
        self.assertEqual(undone['revision'], 2)
        self.assertTrue(LayoutStore(self.root, MAP).can_undo)

    def test_undo_without_history_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.undo(0)

    def test_reset_returns_to_the_supplied_initial_map(self):
        config = self.store.snapshot()
        config['slots'].reverse()
        config['settings']['brightness'] = 3
        self.store.save(config)
        after = self.store.reset(self.store.snapshot()['revision'])
        self.assertEqual([s['node'] for s in after['slots']],
                         [s['node'] for s in default_layout(MAP)['slots']])
        self.assertEqual(after['settings'], DEFAULTS)

    def test_a_corrupt_layout_file_raises_rather_than_silently_resetting(self):
        (self.root / 'layout.json').write_text('{not json')
        with self.assertRaises(ValueError):
            LayoutStore(self.root, MAP)

    def test_a_layout_file_that_violates_the_allowlist_is_refused_on_load(self):
        bad = default_layout(MAP)
        bad['slots'][0]['node'] = 'node01'
        (self.root / 'layout.json').write_text(json.dumps({'current': bad}))
        with self.assertRaises(ValueError):
            LayoutStore(self.root, MAP)

    def test_atomic_write_leaves_no_partial_file_and_no_temp_behind(self):
        path = self.root / 'thing.json'
        atomic_write(path, {'a': [1, 2, 3]})
        self.assertEqual(json.loads(path.read_text()), {'a': [1, 2, 3]})
        self.assertEqual([p.name for p in self.root.iterdir() if p.suffix == '.tmp'], [])

    def test_atomic_write_refuses_nan_rather_than_writing_invalid_json(self):
        with self.assertRaises(ValueError):
            atomic_write(self.root / 'nan.json', {'a': float('nan')})

    def test_a_failed_save_leaves_the_previous_file_intact(self):
        self.store.save(self.store.snapshot())
        before = (self.root / 'layout.json').read_text()
        bad = self.store.snapshot()
        bad['slots'][0]['node'] = 'node01'
        with self.assertRaises(ValueError):
            self.store.save(bad)
        self.assertEqual((self.root / 'layout.json').read_text(), before)

    def test_store_creates_its_directory(self):
        nested = self.root / 'deep' / 'deeper'
        LayoutStore(nested, MAP)
        self.assertTrue(nested.is_dir())
        self.assertTrue(os.access(nested, os.W_OK))


if __name__ == '__main__':
    unittest.main()
