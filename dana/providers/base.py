from __future__ import annotations
import abc
from typing import AsyncIterable, List, Optional, Any

class LLMProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def supports_streaming(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def supports_tools(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def estimated_input_cost_per_1m_tokens(self) -> float:
        pass

    @property
    @abc.abstractmethod
    def estimated_output_cost_per_1m_tokens(self) -> float:
        pass

    @property
    @abc.abstractmethod
    def average_first_token_ms(self) -> float:
        pass

    @abc.abstractmethod
    async def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    def create_client(self) -> Any:
        pass

    @abc.abstractmethod
    async def stream_response(
        self,
        chat_ctx: Any,
        **kwargs
    ) -> AsyncIterable[str]:
        pass


class TTSProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def supports_streaming(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def supports_pcm(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def supports_ulaw(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def sample_rates(self) -> List[int]:
        pass

    @property
    @abc.abstractmethod
    def estimated_cost_per_minute(self) -> float:
        pass

    @property
    @abc.abstractmethod
    def average_first_audio_ms(self) -> float:
        pass

    @abc.abstractmethod
    async def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    def synthesize_stream(self) -> Any:
        pass


class STTProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def supports_streaming(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def languages(self) -> List[str]:
        pass

    @property
    @abc.abstractmethod
    def estimated_cost_per_minute(self) -> float:
        pass

    @property
    @abc.abstractmethod
    def average_final_transcript_ms(self) -> float:
        pass

    @abc.abstractmethod
    async def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    def transcribe_stream(self) -> Any:
        pass


class VADProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def average_detection_ms(self) -> float:
        pass

    @property
    @abc.abstractmethod
    def false_interrupt_risk(self) -> float:
        pass

    @abc.abstractmethod
    async def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    def create_detector(self) -> Any:
        pass


class TelephonyProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def supports_outbound(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def supports_transfer(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def supports_recording(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def supports_warm_bridge(self) -> bool:
        pass

    @abc.abstractmethod
    async def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    async def originate_call(self, destination: str, **kwargs) -> Any:
        pass

    @abc.abstractmethod
    async def end_call(self, call_id: str) -> bool:
        pass

    @abc.abstractmethod
    async def transfer_call(self, call_id: str, destination: str, warm: bool = False) -> bool:
        pass
