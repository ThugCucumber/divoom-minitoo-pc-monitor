import unittest
from unittest.mock import Mock, patch
import queue
import pc_monitor as m

class RecoveryTests(unittest.TestCase):
    def test_stale_packets_are_discarded_and_silent_device_times_out(self):
        reader = Mock(error=None, packets=queue.Queue())
        reader.packets.put(b'stale')
        dev = Mock()
        with patch.object(m, 'encode_image_payload', return_value=b'x'), patch.object(m, 'build_chunks', return_value=[b'chunk']), patch.object(m, 'read_packet', return_value=None), patch.object(m.time, 'monotonic', side_effect=range(100)):
            self.assertEqual(m.transfer(dev, None, reader), (0, 1))
        self.assertTrue(reader.packets.empty())
        dev.write.assert_called_once()
        dev.flush.assert_not_called()

    def test_incomplete_image_closes_port_and_reconnects_with_backoff(self):
        dev = Mock()
        context = Mock()
        context.__enter__ = Mock(return_value=dev)
        context.__exit__ = Mock(return_value=False)
        joystick = Mock()
        joystick.page.return_value = 0
        reader = Mock()
        statuses = []
        with patch.object(m.serial, 'Serial', return_value=context) as serial_open, patch.object(m, 'BluetoothReader', return_value=reader), patch.object(m, 'read_sensors', return_value={}), patch.object(m, 'draw_screen'), patch.object(m, 'transfer', side_effect=[(0, 1), (0, 1), KeyboardInterrupt]), patch.object(m, 'write_status', side_effect=lambda **x: statuses.append(x)), patch.object(m.time, 'sleep') as sleep:
            m.monitor(joystick)
        self.assertEqual(serial_open.call_count, 3)
        self.assertEqual(context.__exit__.call_count, 3)
        self.assertEqual(reader.close.call_count, 3)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [5, 10])
        self.assertEqual(statuses[-1]['state'], 'reconnecting')

if __name__ == '__main__':
    unittest.main()
