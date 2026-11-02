"""Stable spawn target, independent of backend fixture module reloads."""
import signal
from .model import Lab


def killed_mutation(root, witness_root, point, connection):
    def barrier(reached):
        if reached == point:
            connection.send(reached)
            signal.pause()
    Lab(root, witness_root, barrier).change('policy', 'required')
