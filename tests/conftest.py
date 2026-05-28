import sys
from unittest.mock import MagicMock

# Dummy base classes to allow clean subclassing in tests
class DummyTTS:
    def __init__(self, *args, **kwargs):
        pass

class DummySynthesizeStream:
    def __init__(self, *args, **kwargs):
        self._input_ch = MagicMock()
        self._FlushSentinel = MagicMock

    def push_text(self, text: str) -> None:
        pass

    def flush(self) -> None:
        pass

    def end_input(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

class DummyChunkedStream:
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
class MockAgentsTTS:
    def __init__(self):
        self.TTS = DummyTTS
        self.SynthesizeStream = DummySynthesizeStream
        self.ChunkedStream = DummyChunkedStream
        self.TTSCapabilities = MagicMock
        self.APIConnectOptions = MagicMock

    def __getattr__(self, name):
        if name == "TTSStream":
            raise AttributeError(f"module 'livekit.agents.tts' has no attribute '{name}'")
        return MagicMock()

agents_tts = MockAgentsTTS()
sys.modules['livekit.agents.tts'] = agents_tts

# Create agents.stt module
agents_stt = MagicMock()
agents_stt.STT = DummySTT
agents_stt.STTStream = DummySTTStream
agents_stt.SpeechStream = DummySTTStream
sys.modules['livekit.agents.stt'] = agents_stt

# Create agents.voice module
agents_voice = MagicMock()
sys.modules['livekit.agents.voice'] = agents_voice

# Create agents.utils module
agents_utils = MagicMock()
sys.modules['livekit.agents.utils'] = agents_utils

# Create DummyAgent, function_tool, and RunContext to allow proper subclassing and introspection
class DummyAgent:
    def __init__(self, instructions: str = "", **kwargs):
        self.instructions = instructions
        self.tools = []
        for name in dir(self):
            try:
                member = getattr(self, name)
                if hasattr(member, "_is_tool") or getattr(member, "_is_tool", False):
                    self.tools.append(member)
            except AttributeError:
                pass

def function_tool(*args, **kwargs):
    def decorator(func):
        func._is_tool = True
        name = kwargs.get("name", func.__name__)
        class Info:
            def __init__(self, n):
                self.name = n
        func.info = Info(name)
        func.id = name
        return func
    return decorator

class DummyRunContext:
    pass

class MockDescriptor:
    def __init__(self, fields):
        self.fields_by_name = {f: MagicMock() for f in fields}

class MockCreateSIPParticipantRequest:
    DESCRIPTOR = MockDescriptor([
        "sip_trunk_id",
        "sip_call_to",
        "room_name",
        "participant_identity",
        "participant_metadata",
        "wait_until_answered",
        "display_name"
    ])
    def __init__(self, **kwargs):
        self.kwargs = kwargs

# Create top-level livekit.agents
livekit_agents = MagicMock()
livekit_agents.tts = agents_tts
livekit_agents.stt = agents_stt
livekit_agents.voice = agents_voice
livekit_agents.utils = agents_utils
livekit_agents.Agent = DummyAgent
livekit_agents.function_tool = function_tool
livekit_agents.RunContext = DummyRunContext
sys.modules['livekit.agents'] = livekit_agents

# Create livekit.api mock module
api_mock = MagicMock()
api_mock.CreateSIPParticipantRequest = MockCreateSIPParticipantRequest
sys.modules['livekit.api'] = api_mock

# Create top-level livekit
livekit_mock = MagicMock()
livekit_mock.rtc = rtc_mock
livekit_mock.agents = livekit_agents
livekit_mock.api = api_mock
sys.modules['livekit'] = livekit_mock


# Other plugins
sys.modules['livekit.plugins'] = MagicMock()
sys.modules['livekit.plugins.openai'] = MagicMock()
sys.modules['livekit.plugins.silero'] = MagicMock()
sys.modules['livekit.plugins.deepgram'] = MagicMock()

# Mock model packages
sys.modules['kokoro_onnx'] = MagicMock()
sys.modules['faster_whisper'] = MagicMock()

class DummyTensor:
    pass

torch_mock = MagicMock()
torch_mock.Tensor = DummyTensor
torch_mock.hub.load.return_value = (MagicMock(), MagicMock())
sys.modules['torch'] = torch_mock


