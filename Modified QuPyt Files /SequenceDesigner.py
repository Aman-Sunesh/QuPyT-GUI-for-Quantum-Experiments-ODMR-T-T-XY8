# =============================================================================
# Original Code Author: Karl Berggren (https://github.com/KarDB/QuPyt)
# File Modified By: Aman Sunesh
# Purpose: This file is a modified version of the original QuPyt source code.
#          Modifications were made to ensure compatibility with a custom GUI.
#          All rights to the original code belong to the original author.
# =============================================================================

import logging
import pickle
from typing import Dict, Any, List, Tuple
import hashlib
import warnings 
from pathlib import Path
import numpy as np
from termcolor import colored
import yaml
from qupyt.set_up import get_seq_dir


class PulseSequenceYaml:
    def __init__(
        self,
        #  Channel list as you think about them
        #  marker 1, marker 2 and so on
        channel_mapping: Dict[str, Any],
        #  Source as the AWG thinks about them
        #  e.g. source1 might include analog 1, marker 1, ...
        #  There is one analog channel per source but mulitple makers etc.
        awg_sources: list[int],
        samprate: float = 5e9,
        yaml_file: Path = get_seq_dir() / "sequence.yaml",
    ) -> None:
        self.yaml_file = yaml_file
        self.awg_sources = awg_sources
        self.channel_mapping = channel_mapping
        self.samp_rate = float(samprate)  # samples per second

    def _sequence_didnt_change(self) -> bool:
        with open(self.yaml_file, "r", encoding="utf-8") as file:
            sequence_instructions = yaml.safe_load(file)
        try:
            with open(
                self.yaml_file.with_suffix(".aux"), "r", encoding="utf-8"
            ) as file:
                previous_sequence_instructions = yaml.safe_load(file)
            with open(
                self.yaml_file.with_suffix(".aux"), "w", encoding="utf-8"
            ) as file:
                yaml.dump(sequence_instructions, file)
            return previous_sequence_instructions == sequence_instructions
        except FileNotFoundError:
            with open(
                self.yaml_file.with_suffix(".aux"), "w", encoding="utf-8"
            ) as file:
                yaml.dump(sequence_instructions, file)
            return False

    def translate_yaml_to_numeric_instructions(self) -> None:
        if self._sequence_didnt_change():
            return
        with open(self.yaml_file, "r", encoding="utf-8") as file:
            sequence_instructions = yaml.safe_load(file)
        sequence_order = sequence_instructions["sequencing_order"]
        sequencing_repeats = sequence_instructions["sequencing_repeats"]
        duration = float(sequence_instructions["total_duration"])
        sorted_pulse_blocks = sorted(set(sequence_order))
        seq = PulseSequence(
            len(sorted_pulse_blocks),
            duration,
            self.awg_sources,
            samprate=self.samp_rate,
        )
        flag_channels: Dict[str, Any] = {}
        for i, block in enumerate(sorted_pulse_blocks):
            flag_channels[block] = []
            for channel, pulses in sequence_instructions[block].items():
                mapped_channel = self.channel_mapping[channel]
                if isinstance(mapped_channel, str):
                    flag_channels[block].append(mapped_channel)
                    continue
                for pulse in pulses.values():
                    seq.add_pulse(
                        i,
                        float(pulse["start"]),
                        float(pulse["duration"]),
                        channel=mapped_channel,
                        inputtype="time",
                        amplitude=float(pulse["amplitude"]),
                        freq=(float(pulse["frequency"]), float(pulse["phase"])),
                    )
        seq.flag_channels = pickle.dumps(flag_channels)
        seq.sequencer = sequencing_repeats
        seq.sequencernames = sequence_order
        seq.make("sequence.npz")


class PulseSequence:
    def __init__(
        self,
        numseqs: int,
        duration: float,
        awg_sources: list[int],
        samprate: float = 2.5e9,
    ) -> None:
        self.samp_rate = samprate  # samples per second
        self.min_time = 1 / samprate
        self.num_points = int(samprate * duration * 1e-6)
        self.time = np.linspace(0, duration, self.num_points)  # in microseconds
        self.numseqs = numseqs
        self.awg_sources = awg_sources

        if self.num_points != samprate * duration * 10**-6:
            print(
                colored(
                    "WARNING! the sequence duration is not an integer multiple of samples".ljust(
                        65, "."
                    )
                    + "! [WARNING]",
                    "red",
                )
            )
            logging.warning(
                "The sequence duration is not an integer multiple of samples".ljust(
                    65, "."
                )
                + "[WARNING]"
            )

        # 7 => 1 analog + 6 digital markers
        self.pulses = np.zeros((numseqs, 7 * len(self.awg_sources), self.num_points))
        self.sequencer = None
        self.sequencernames = None
        self.flag_channels: Any
        self.warning_counter = 0
        self.properties = {"Values": "None"}

    def time_to_index(self, time: float) -> int:
        points = self.samp_rate * time * 1e-6
        if not np.isclose(points, int(round(points))):
            print(
                colored(
                    "WARNING! the start or duration time is not an integer multiple of samples".ljust(
                        65, "."
                    )
                    + "! "
                    "[WARNING]",
                    "red",
                )
            )
            print(
                colored(
                    "WARNING! {} comp. to {}".format(points, int(round(points))).ljust(
                        65, "."
                    )
                    + "! [WARNING]",
                    "red",
                )
            )
            logging.warning(
                "The start of duration is not an integer mulitple of samples".ljust(
                    65, "."
                )
                + "[WARNING]"
            )
            self.warning_counter += 1
        return int(round(points))

    def add_pulse(
        self,
        numseq,
        start: float,
        duration: float,
        channel: int = 0,
        inputtype: str = "time",
        amplitude: float = 1.0,
        freq=None,
    ) -> None:
        """return       analog channel pulse.
        numseq:          which sequence to add the pulse to.
        start & duration:time in microseconds or points.
        inputtype:       'time'   => input in microseconds
                         'points' => points
        channel can be 0 or 5 and will write to AWG source 1 or 2.
        """
        if inputtype == "time":
            start = self.time_to_index(start)
            duration = self.time_to_index(duration)
        else:
            logging.info("Interpreting input as number of sampling points!")

        if freq is not None:
            frequency, phase = freq
            frequency *= 10**-6
            fr = np.cos(
                2 * np.pi * frequency * self.time[int(start) : int(start + duration)]
                + phase
            )
        else:
            fr = 1
        try:
            self.pulses[
                numseq[0] : numseq[1], channel, int(start) : int(start + duration)
            ] = (amplitude * fr)
        except Exception:
            self.pulses[numseq, channel, int(start) : int(start + duration)] = (
                amplitude * fr
            )

    def make(self, name: str) -> None:
        logging.info(
            f"There where {self.warning_counter} warnings in PS generation".ljust(
                65, "."
            )
            + "[done]"
        )
        final = np.zeros((self.numseqs, 2 * len(self.awg_sources), self.num_points))
        for si, src in enumerate(self.awg_sources):
            base = 7*si
            # analog channel
            final[:, 2*si, :] = self.pulses[:, base, :]
            # pack 6 markers into one byte (bits 7→2)
            packed = np.zeros_like(self.pulses[:, 0, :], dtype=np.uint8)
            for bit_offset in range(6):
                marker = self.pulses[:, base + 1 + bit_offset, :] > 0
                packed |= (marker.astype(np.uint8) << (7 - bit_offset))
            final[:, 2*si+1, :] = packed

        hash1 = hashlib.sha1(final.tobytes()).hexdigest()
        hash2 = hashlib.sha1(str(self.sequencer).encode("utf-8")).hexdigest()
        hash3 = hashlib.sha1(str(self.sequencernames).encode("utf-8")).hexdigest()
        hash4 = hash1 + hash2 + hash3
        finalhash = hashlib.sha1(hash4.encode("utf-8")).hexdigest()

        file_dir = get_seq_dir()
        np.savez(
            file_dir / name,
            final,
            self.sequencer,
            self.sequencernames,
            finalhash,
            self.properties,
            self.flag_channels,
        )
        logging.info(f"Pulse sequence written to {name}".ljust(65, ".") + "[done]")


class PulseBlasterSequence:
    def __init__(
        self,
        channel_mapping: Dict[str, Any],
        yaml_file: Path = get_seq_dir() / "sequence.yaml",
    ) -> None:
        self.event_times: List[float] = []
        self.event_durations: List[float] = []
        self.event_channel: List[str] = []
        self.events: List[str] = []
        self.channel_bits: List[int] = []
        self.segment_durations: List[float] = []
        self.channel_mapping = channel_mapping
        self.ps: Dict[str, Any] = {}
        self.yaml_sequence: Dict[str, Any] = self._load_yaml_sequence(yaml_file)
        self.total_duration = self.yaml_sequence["total_duration"]

    def _load_yaml_sequence(self, path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as file:
            yaml_sequence = yaml.safe_load(file)
        return yaml_sequence

    def parse_pulse_sequence_file(self) -> None:
        for block in self.yaml_sequence["sequencing_order"]:
            self._parse_block(self.yaml_sequence[block])
            self._sort_pulses()
            self._get_event_durations()
            self._compute_channel_bits()
            self.ps[block] = {
                "channel_bits": self.channel_bits,
                "durations": self.event_durations,
            }
            self._reset_attributes()

    def compile(self) -> Tuple[List[int], List[float]]:
        """
        combines the individual sub seqeunces into one long sequence that
        can be uploaded to the pulse blaster card in one go.
        """
        channel_bits = []
        bits_duration = []
        sequencing_info = zip(
            self.yaml_sequence["sequencing_order"],
            self.yaml_sequence["sequencing_repeats"],
        )
        for sequence_block, block_repeats in sequencing_info:
            channel_bits += self.ps[sequence_block]["channel_bits"] * block_repeats
            bits_duration += self.ps[sequence_block]["durations"] * block_repeats

        return channel_bits, bits_duration

    def _reset_attributes(self) -> None:
        self.event_times = []
        self.event_durations = []
        self.event_channel = []
        self.events = []
        self.channel_bits = []
        self.segment_durations = []

    def _append_event(self, channel: str, pulse: Dict[str, float]) -> None:
        self.event_times.append(pulse["start"])
        self.event_times.append(pulse["start"] + pulse["duration"])
        self.event_channel.append(channel)
        self.event_channel.append(channel)
        self.events.append("up")
        self.events.append("down")

    def _parse_channel(self, channel: str, channel_pulses: Dict[str, Any]) -> None:
        for pulse in channel_pulses.values():
            self._append_event(channel, pulse)

    def _parse_block(self, ps_block: Dict[str, Any]) -> None:
        for channel, channel_pulses in ps_block.items():
            self._parse_channel(channel, channel_pulses)

    def _sort_pulses(self) -> None:
        self.event_times, self.event_channel, self.events = zip(
            *sorted(zip(self.event_times, self.event_channel, self.events))
        )

    def _get_event_durations(self) -> None:
        self.event_durations = [
            i - j for i, j in zip(self.event_times[1:], self.event_times[:-1])
        ]
        if self.total_duration == "ignore":
            pass
        elif self.event_times[-1] != self.total_duration:
            self.event_durations.append(self.total_duration - self.event_times[-1])

    def _event_to_sign(self, event: str) -> int:
        if event == "up":
            return 1
        if event == "down":
            return -1
        raise ValueError(f'No event {event}, options are "up" or "down"')

    def _compute_channel_bits(self) -> None:
        for i, _ in enumerate(self.event_durations):
            prev_val = self.channel_bits[-1] if self.channel_bits else 0
            self.channel_bits.append(
                prev_val
                + 2 ** (self.channel_mapping[self.event_channel[i]])
                * self._event_to_sign(self.events[i])
            )
        if self.event_times[0] != 0:
            self.channel_bits.insert(0, 0)
            self.event_durations.insert(0, self.event_times[0])
        self._pop_uncessary_entries()

    def _pop_uncessary_entries(self) -> None:
        # find all zero‐length segments
        indices_to_pop = [
            idx
            for idx, dur in enumerate(self.event_durations)
            if dur == 0
        ]
        # remove in reverse order so earlier pops don't shift later ones
        for pop_index in sorted(indices_to_pop, reverse=True):
            if 0 <= pop_index < len(self.event_durations):
                self.event_durations.pop(pop_index)
            else:
                warnings.warn(f"Skipping bad event_durations index {pop_index} (len={len(self.event_durations)})")
            # guard channel_bits as well
            if 0 <= pop_index < len(self.channel_bits):
                self.channel_bits.pop(pop_index)
            else:
                warnings.warn(f"Skipping bad channel_bits index {pop_index} (len={len(self.channel_bits)})")
