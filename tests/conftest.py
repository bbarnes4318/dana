import sys
from unittest.mock import MagicMock

# Dummy base classes to allow clean subclassing in tests
class DummyTTS:
    def __init__(self, *args, **kwargs):
        pass

class DummyTTSStream:
    def __init__(self, *args, **kwargs):
        pass

class DummySTT:
    def __init__(self, *args, **kwargs):
        pass

class DummySTTStream:
    def __init__(self, *args, **kwargs):
        pass

# Create mock module containers as MagicMocks
rtc_mock = MagicMock()
sys.modules['livekit.rtc'] = rtc_mock

# Create agents.tts module
agents_tts = MagicMock()
agents_tts.TTS = DummyTTS
agents_tts.TTSStream = DummyTTSStream
sys.modules['livekit.agents.tts'] = agents_tts

# Create agents.stt module
agents_stt = MagicMock()
agents_stt.STT = DummySTT
agents_stt.STTStream = DummySTTStream
sys.modules['livekit.agents.stt'] = agents_stt

# Create agents.voice module
agents_voice = MagicMock()
sys.modules['livekit.agents.voice'] = agents_voice

# Create agents.utils module
agents_utils = MagicMock()
sys.modules['livekit.agents.utils'] = agents_utils

# Create top-level livekit.agents
livekit_agents = MagicMock()
livekit_agents.tts = agents_tts
livekit_agents.stt = agents_stt
livekit_agents.voice = agents_voice
livekit_agents.utils = agents_utils
sys.modules['livekit.agents'] = livekit_agents

# Create top-level livekit
livekit_mock = MagicMock()
livekit_mock.rtc = rtc_mock
livekit_mock.agents = livekit_agents
sys.modules['livekit'] = livekit_mock

# Other plugins
sys.modules['livekit.plugins'] = MagicMock()
sys.modules['livekit.plugins.openai'] = MagicMock()
sys.modules['livekit.plugins.silero'] = MagicMock()
sys.modules['livekit.plugins.deepgram'] = MagicMock()

# Mock model packages
sys.modules['kokoro_onnx'] = MagicMock()
sys.modules['faster_whisper'] = MagicMock()

torch_mock = MagicMock()
torch_mock.hub.load.return_value = (MagicMock(), MagicMock())
sys.modules['torch'] = torch_mock

