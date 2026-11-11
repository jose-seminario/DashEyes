import multiprocessing as mp
import queue
import unittest

from protocol import drain, offer, valid_command


class ProtocolTests(unittest.TestCase):
    def test_only_known_commands(self):
        for message in (None, [], "STOP", {}, {"cmd": []}, {"cmd": "INSTALL"}):
            self.assertFalse(valid_command(message))
        self.assertTrue(valid_command({"cmd": "STOP"}))
        self.assertTrue(valid_command({"cmd": "CALIBRATE"}))

    def test_full_queue_does_not_block(self):
        channel = queue.Queue(maxsize=1)
        self.assertTrue(offer(channel, {"cmd": "PREVIEW"}))
        self.assertFalse(offer(channel, {"cmd": "STOP"}))

    def test_drain_is_bounded(self):
        channel = queue.Queue()
        for number in range(10):
            channel.put(number)
        self.assertEqual(list(drain(channel, limit=3)), [0, 1, 2])
        self.assertEqual(channel.qsize(), 7)

    def test_stop_is_independent_of_saturated_queue(self):
        context = mp.get_context("spawn")
        stop = context.Event()
        channel = context.Queue(maxsize=1)
        try:
            self.assertTrue(offer(channel, {"cmd": "PREVIEW"}))
            stop.set()
            self.assertTrue(stop.is_set())
        finally:
            channel.cancel_join_thread()
            channel.close()


if __name__ == "__main__":
    unittest.main()
