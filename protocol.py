"""Mensajes pequeños y colas acotadas. Nunca enviar imágenes por stdout."""

import queue

COMMANDS = {"STOP", "SET_INTENSITY", "TOGGLE_CORRECTION", "PREVIEW", "CALIBRATE", "RESET_CALIBRATION"}


def valid_command(message):
    return isinstance(message, dict) and isinstance(message.get("cmd"), str) and message["cmd"] in COMMANDS


def offer(channel, message):
    if channel is None:
        return False
    try:
        channel.put_nowait(message)
        return True
    except (queue.Full, OSError, ValueError):
        return False


def drain(channel, limit=64):
    if channel is None:
        return
    for _ in range(limit):
        try:
            yield channel.get_nowait()
        except (queue.Empty, OSError, ValueError):
            return
