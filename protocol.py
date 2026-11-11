"""Mensajes pequeños y colas acotadas. Nunca enviar imágenes por stdout."""

import queue

COMMANDS = {"STOP", "SET_INTENSITY", "TOGGLE_CORRECTION", "PREVIEW", "CALIBRATE", "RESET_CALIBRATION"}
REPLACEABLE_COMMANDS = {"SET_INTENSITY", "TOGGLE_CORRECTION", "PREVIEW"}


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


def offer_latest(channel, message):
    """Inserta reemplazando el elemento más antiguo si la cola acotada está llena."""
    if channel is None:
        return False
    if offer(channel, message):
        return True
    try:
        channel.get_nowait()
    except (queue.Empty, EOFError, OSError, ValueError):
        pass
    return offer(channel, message)


def drain(channel, limit=64):
    if channel is None:
        return
    for _ in range(limit):
        try:
            yield channel.get_nowait()
        except (queue.Empty, EOFError, OSError, ValueError):
            return


def coalesce_commands(messages):
    """Conserva eventos y comandos de acción; compacta estados repetidos al último valor."""
    ordered = []
    latest_index = {}
    for message in messages:
        if valid_command(message) and message["cmd"] in REPLACEABLE_COMMANDS:
            cmd = message["cmd"]
            if cmd in latest_index:
                ordered[latest_index[cmd]] = None
            latest_index[cmd] = len(ordered)
        ordered.append(message)
    return [item for item in ordered if item is not None]
