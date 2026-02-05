import math
import struct

import pyaudio
import speech_recognition as sr
from PySide6.QtCore import QObject, Signal


class AudioService(QObject):
    text_received = Signal(str)
    status_changed = Signal(bool)
    level_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.8
        self.recognizer.non_speaking_duration = 0.5
        self.microphone = None
        self._stop_listening = None
        self._listening = False
        self._pyaudio = None
        self._level_stream = None

    @property
    def is_listening(self):
        return self._listening

    def start_listening(self):
        if self._listening:
            return
        self.microphone = sr.Microphone()
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
        self._start_level_monitoring()

        def _callback(recognizer, audio):
            text = ""
            try:
                text = recognizer.recognize_google(audio)
            except sr.UnknownValueError:
                text = ""
            except sr.RequestError:
                text = ""
            if text:
                self.text_received.emit(text)

        self._stop_listening = self.recognizer.listen_in_background(
            self.microphone,
            _callback,
            phrase_time_limit=8,
        )
        self._listening = True
        self.status_changed.emit(True)

    def stop_listening(self):
        if not self._listening:
            return
        if self._stop_listening:
            self._stop_listening(wait_for_stop=False)
        self._stop_listening = None
        self._listening = False
        self._stop_level_monitoring()
        self.microphone = None
        self.status_changed.emit(False)

    def _start_level_monitoring(self):
        try:
            self._pyaudio = pyaudio.PyAudio()
            self._level_stream = self._pyaudio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024,
                stream_callback=self._level_callback,
            )
            self._level_stream.start_stream()
        except Exception:
            self._stop_level_monitoring()

    def _stop_level_monitoring(self):
        if self._level_stream is not None:
            try:
                self._level_stream.stop_stream()
                self._level_stream.close()
            except Exception:
                pass
        self._level_stream = None
        if self._pyaudio is not None:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
        self._pyaudio = None
        self.level_changed.emit(0.0)

    def _level_callback(self, in_data, frame_count, time_info, status):
        if not in_data:
            return (None, pyaudio.paContinue)
        try:
            count = len(in_data) // 2
            format_str = f"{count}h"
            samples = struct.unpack(format_str, in_data)
            rms = math.sqrt(sum(s * s for s in samples) / max(1, count))
            level = min(1.0, rms / 10000.0)
            self.level_changed.emit(level)
        except Exception:
            pass
        return (None, pyaudio.paContinue)

    def speak(self, text):
        # TODO: Implement TTS
        pass
