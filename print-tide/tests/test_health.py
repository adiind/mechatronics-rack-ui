"""Rope health: acks and retained status become delivery evidence, honestly."""
import tempfile
import unittest

from test_core import MAP, NOW, report, studio

from light_studio.studio import ACK_FRESH_SECONDS, SILENT_SECONDS, TICK_HZ


class Clock:
    def __init__(self, t=NOW):
        self.t = t
    def __call__(self):
        return self.t


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.sent = []
        self.studio = studio(self.tmp.name, rows=[report()], clock=self.clock,
                             publish=lambda node, payload: self.sent.append(node) or True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unverified_until_the_host_feeds_anything(self):
        self.studio.tick()
        view = self.studio.view()
        self.assertEqual(view['transport']['node_receipt'], 'unverified')
        self.assertEqual(view['ropes']['node02']['receipt'], 'unverified')

    def test_ghost_nodes_are_ignored(self):
        self.assertFalse(self.studio.observe_node('node004', 'status', 'offline'))
        self.assertFalse(self.studio.observe_node('node01', 'ack', {'ok': True}))
        self.assertEqual(self.studio.view()['transport']['node_receipt'], 'unverified')

    def test_ack_after_send_confirms_and_counts(self):
        self.studio.tick()
        self.assertIn('node02', self.sent)
        self.assertTrue(self.studio.observe_node('node02', 'ack', {'ok': True, 'op': 'range'}))
        rope = self.studio.view()['ropes']['node02']
        self.assertEqual(rope['receipt'], 'confirmed')
        self.assertEqual(rope['status'], 'online')
        self.assertEqual(rope['acks_ok'], 1)
        self.assertIn('confirmed 1/7', self.studio.view()['transport']['node_receipt'])

    def test_unanswered_sends_go_silent(self):
        # Wired (another rope reported in), but node02 never acks its sends.
        self.studio.observe_node('node03', 'status', 'online')
        self.studio.tick()
        self.clock.t += ACK_FRESH_SECONDS + 0.5
        self.assertEqual(self.studio.view()['ropes']['node02']['receipt'], 'silent')

    def test_recent_send_without_ack_is_pending_when_acks_existed(self):
        self.studio.tick()
        self.studio.observe_node('node02', 'ack', {'ok': True})
        self.clock.t += ACK_FRESH_SECONDS + 1
        self.studio.tick()                      # a new send, ack not yet back
        rope = self.studio.view()['ropes']['node02']
        self.assertEqual(rope['receipt'], 'pending')
        self.clock.t += SILENT_SECONDS + 0.1
        self.assertEqual(self.studio.view()['ropes']['node02']['receipt'], 'silent')

    def test_offline_status_wins_and_online_return_forces_repaint(self):
        # A first paint of water + ink bands + droplet can spill into a second
        # tick under the per-tick message cap; two ticks always complete it.
        self.studio.tick()
        self.clock.t += 1 / TICK_HZ
        self.studio.tick()
        self.studio.observe_node('node02', 'ack', {'ok': True})
        self.studio.observe_node('node02', 'status', 'offline')
        self.assertEqual(self.studio.view()['ropes']['node02']['receipt'], 'offline')
        self.assertIn('1 offline', self.studio.view()['transport']['node_receipt'])
        self.assertTrue(all(p is not None for p in self.studio.applied['node02']))
        self.studio.observe_node('node02', 'status', 'online')
        self.assertTrue(all(p is None for p in self.studio.applied['node02']),
                        'a rebooted controller boots dark and must be repainted')

    def test_error_acks_are_counted_but_never_confirm(self):
        self.studio.tick()
        self.studio.observe_node('node02', 'ack', {'ok': False, 'error': 'invalid_index'})
        rope = self.studio.view()['ropes']['node02']
        self.assertEqual(rope['acks_error'], 1)
        self.assertEqual(rope['last_error'], 'invalid_index')
        self.assertNotEqual(rope['receipt'], 'confirmed')

    def test_identify_reports_a_controller_answer(self):
        self.studio.identify('node02')
        self.assertFalse(self.studio.view()['identify']['acked'])
        self.studio.observe_node('node02', 'ack', {'ok': True, 'op': 'range'})
        self.assertTrue(self.studio.view()['identify']['acked'])

    def test_bad_payloads_are_rejected_quietly(self):
        self.assertFalse(self.studio.observe_node('node02', 'status', 'rebooting'))
        self.assertFalse(self.studio.observe_node('node02', 'ack', 'not json'))
        self.assertFalse(self.studio.observe_node('node02', 'telemetry', {}))


if __name__ == '__main__':
    unittest.main()
