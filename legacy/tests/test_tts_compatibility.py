import inspect
from livekit.agents import tts
from tts_service import LocallyHostedKokoro, LocalTTSStream, LocalChunkedStream, FastPhraseChunker, normalize_text

def test_tts_service_imports():
    assert LocallyHostedKokoro is not None
    assert LocalTTSStream is not None
    assert LocalChunkedStream is not None

def test_instantiate_kokoro():
    kokoro = LocallyHostedKokoro()
    assert kokoro is not None
    assert kokoro.sample_rate == 16000

def test_sdk_base_classes():
    assert issubclass(LocalTTSStream, tts.SynthesizeStream)
    assert issubclass(LocalChunkedStream, tts.ChunkedStream)

def test_no_tts_stream_reference():
    assert not hasattr(tts, "TTSStream")

def test_signatures_match_sdk():
    stream_sig = inspect.signature(LocallyHostedKokoro.stream)
    synthesize_sig = inspect.signature(LocallyHostedKokoro.synthesize)
    
    assert "conn_options" in stream_sig.parameters
    assert "conn_options" in synthesize_sig.parameters

def test_phrase_chunker_passes():
    chunker = FastPhraseChunker(min_tokens=2)
    p = chunker.feed("Hello world! ")
    assert p == ["Hello world!"]

def test_normalize_text_passes():
    assert normalize_text("AI") == "A I"
