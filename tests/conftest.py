import sys
import os
from unittest.mock import MagicMock
import dotenv

from pathlib import Path

# Clear DATABASE_URL initially and disable write-behind queue by default in tests
_telephony_keys = [
    "DATABASE_URL",
    "DANA_CONFIRM_PLACE_CALL",
    "TELEPHONY_LIVE_MODE",
    "DANA_ENABLE_OUTBOUND_DIALER",
    "LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
    "DANA_LIVEKIT_SIP_OUTBOUND_TRUNK_ID",
    "TELNYX_LIVEKIT_OUTBOUND_TRUNK_ID",
    "DANA_TELEPHONY_PROVIDER",
    "TELNYX_API_KEY",
    "TELNYX_DIDS",
    "TELNYX_PHONE_NUMBERS",
    "TELNYX_OUTBOUND_CALLER_ID",
    "DANA_OUTBOUND_CALLER_ID",
    "DANA_TEST_CALL_TO",
    "TEST_CALL_TO"
]

for k in _telephony_keys:
    os.environ.pop(k, None)
os.environ["DANA_WRITE_BEHIND_ENABLED"] = "false"

import subprocess
_orig_subprocess_run = subprocess.run
def _mock_subprocess_run(*args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = 60
    return _orig_subprocess_run(*args, **kwargs)
subprocess.run = _mock_subprocess_run

# Resolve real repo root
_real_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Intercept all calls to load_dotenv during tests to keep DATABASE_URL cleared and write-behind disabled
_orig_load_dotenv = dotenv.load_dotenv
def _mock_load_dotenv(*args, **kwargs):
    dotenv_path = kwargs.get("dotenv_path") or (args[0] if len(args) > 0 else None)
    is_real = True
    if dotenv_path:
        try:
            p = str(Path(dotenv_path).resolve())
            real_resolved = str(Path(_real_root).resolve())
            if "temp" in p.lower() or "pytest" in p.lower() or not p.startswith(real_resolved):
                is_real = False
        except Exception:
            pass

    if is_real:
        backup = {k: os.environ[k] for k in _telephony_keys if k in os.environ}
        res = _orig_load_dotenv(*args, **kwargs)
        for k in _telephony_keys:
            if k in backup:
                os.environ[k] = backup[k]
            else:
                os.environ.pop(k, None)
    else:
        res = _orig_load_dotenv(*args, **kwargs)

    os.environ["DANA_WRITE_BEHIND_ENABLED"] = "false"
    return res
dotenv.load_dotenv = _mock_load_dotenv

# Ensure root dir is in path before importing config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import env_loader
_orig_load_environment = env_loader.load_environment
def _mock_load_environment(*args, **kwargs):
    from config.env_loader import find_repo_root
    is_real_root = True
    try:
        repo_root = str(find_repo_root().resolve())
        real_resolved = str(Path(_real_root).resolve())
        if repo_root != real_resolved:
            is_real_root = False
    except Exception:
        pass

    if is_real_root:
        backup = {k: os.environ[k] for k in _telephony_keys if k in os.environ}
        res = _orig_load_environment(*args, **kwargs)
        for k in _telephony_keys:
            if k in backup:
                os.environ[k] = backup[k]
            else:
                os.environ.pop(k, None)
    else:
        res = _orig_load_environment(*args, **kwargs)

    os.environ["DANA_WRITE_BEHIND_ENABLED"] = "false"
    return res
env_loader.load_environment = _mock_load_environment

# Dummy base classes to allow clean subclassing in tests
class DummyTTS:
    sample_rate = 24000

    def __init__(self, *args, **kwargs):
        pass

    def stream(self, *args, **kwargs):
        pass

    def synthesize(self, *args, **kwargs):
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

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

class DummyChunkedStream:
    def __init__(self, *args, **kwargs):
        pass

class DummySTT:
    def __init__(self, *args, **kwargs):
        pass

class DummySTTStream:
    def __init__(self, *args, **kwargs):
        pass

class DummyLLM:
    def __init__(self, *args, **kwargs):
        pass

    def chat(self, *args, **kwargs):
        pass

class DummyLLMStream:
    def __init__(self, *args, **kwargs):
        pass

    async def aclose(self) -> None:
        pass

    def __aiter__(self):
        if hasattr(self, "_run"):
            self._iter = self._run().__aiter__()
            return self
        return self

    async def __anext__(self):
        if hasattr(self, "_iter"):
            return await self._iter.__anext__()
        raise StopAsyncIteration

class DummyAudioFrame:
    def __init__(self, data=b"", sample_rate=24000, num_channels=1, samples_per_channel=160):
        self.data = data
        self.sample_rate = sample_rate
        self.num_channels = num_channels
        self.samples_per_channel = samples_per_channel

# Create mock module containers as MagicMocks
rtc_mock = MagicMock()
rtc_mock.AudioFrame = DummyAudioFrame
sys.modules['livekit.rtc'] = rtc_mock
sys.modules['livekit.rtc._ffi_client'] = MagicMock()
sys.modules['livekit.rtc._proto'] = MagicMock()

class DummyAudioEmitter:
    def __init__(self, *args, **kwargs):
        pass

    def initialize(self, *args, **kwargs):
        pass

    def push(self, *args, **kwargs):
        pass

    def start_segment(self, *args, **kwargs):
        pass

    def end_segment(self, *args, **kwargs):
        pass

# Create agents.tts module
class MockAgentsTTS:
    def __init__(self):
        self.TTS = DummyTTS
        self.SynthesizeStream = DummySynthesizeStream
        self.ChunkedStream = DummyChunkedStream
        self.TTSCapabilities = MagicMock
        self.APIConnectOptions = MagicMock
        self.AudioEmitter = DummyAudioEmitter

    def __getattr__(self, name):
        if name == "TTSStream":
            raise AttributeError(f"module 'livekit.agents.tts' has no attribute '{name}'")
        return MagicMock()

agents_tts = MockAgentsTTS()
sys.modules['livekit.agents.tts'] = agents_tts

# Create agents.llm module
class MockAgentsLLM:
    def __init__(self):
        self.LLM = DummyLLM
        self.LLMStream = DummyLLMStream
        self.ChatContext = MagicMock

    def __getattr__(self, name):
        return MagicMock()

agents_llm = MockAgentsLLM()
sys.modules['livekit.agents.llm'] = agents_llm

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

# Create agents.types module
agents_types = MagicMock()
sys.modules['livekit.agents.types'] = agents_types

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
livekit_agents.llm = agents_llm
livekit_agents.stt = agents_stt
livekit_agents.voice = agents_voice
livekit_agents.utils = agents_utils
livekit_agents.types = agents_types
livekit_agents.Agent = DummyAgent
livekit_agents.function_tool = function_tool
livekit_agents.RunContext = DummyRunContext
sys.modules['livekit.agents'] = livekit_agents

# Create livekit.api mock module
api_mock = MagicMock()
api_mock.CreateSIPParticipantRequest = MockCreateSIPParticipantRequest
sys.modules['livekit.api'] = api_mock

# Create livekit.protocol and livekit.protocol.sip mock modules
protocol_mock = MagicMock()
sip_proto_mock = MagicMock()
sip_proto_mock.CreateSIPParticipantRequest = MockCreateSIPParticipantRequest
protocol_mock.sip = sip_proto_mock
sys.modules['livekit.protocol'] = protocol_mock
sys.modules['livekit.protocol.sip'] = sip_proto_mock

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
sys.modules['livekit.plugins.silero.vad'] = MagicMock()
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


