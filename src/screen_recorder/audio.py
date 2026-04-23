from enum import Enum
from dataclasses import dataclass
from .configs import AudioConfig

class AudioTrack(Enum):
    MIX = "mix"
    MIC = "mic"
    SYS = "sys"
    NATIVE = "native"

@dataclass(frozen=True)
class AudioResult:
    inputs: list[str]
    filters: list[str]
    maps: list[str]  # Ordered perfectly to match the requested_tracks
    next_index: int

def build_audio_args(
    settings: AudioConfig, 
    requested_tracks: list[AudioTrack], 
    start_index: int = 0, 
    include_native: bool = False
) -> AudioResult:
    inputs: list[str] = []
    filters: list[str] = []
    curr_idx = start_index

    @dataclass
    class Source:
        id: AudioTrack
        stream_ref: str 
        
    sources: list[Source] = []
    
    if include_native:
        sources.append(Source(AudioTrack.NATIVE, "0:a:0")) 
        
    if settings.microphone.enabled:
        inputs.extend(["-f", "pulse", "-thread_queue_size", "1024", "-i", settings.microphone.device])
        sources.append(Source(AudioTrack.MIC, f"{curr_idx}:a"))
        curr_idx += 1
        
    if settings.system.enabled:
        inputs.extend(["-f", "pulse", "-thread_queue_size", "1024", "-i", settings.system.device])
        sources.append(Source(AudioTrack.SYS, f"{curr_idx}:a"))
        curr_idx += 1

    mix_inputs: list[str] = []
    final_pads: dict[AudioTrack, str] = {}
    wants_mix = AudioTrack.MIX in requested_tracks

    for src in sources:
        wants_solo = src.id in requested_tracks
        # NEW RULE: Only MIC and SYS are allowed in the Master Mix
        goes_to_mix = wants_mix and src.id in (AudioTrack.MIC, AudioTrack.SYS)
        
        uses = int(wants_solo) + int(goes_to_mix)
        
        if uses == 0:
            continue
            
        res_tag = f"[a_{src.id.value}_res]"
        filters.append(f"[{src.stream_ref}]aresample=48000:async=1{res_tag}")
        
        if uses == 1:
            if wants_solo:
                final_pads[src.id] = res_tag
            else:
                mix_inputs.append(res_tag)
                
        elif uses == 2:
            solo_tag = f"[a_{src.id.value}_solo]"
            mix_tag = f"[a_{src.id.value}_mix]"
            filters.append(f"{res_tag}asplit=2{solo_tag}{mix_tag}")
            final_pads[src.id] = solo_tag
            mix_inputs.append(mix_tag)

    if wants_mix and mix_inputs:
        if len(mix_inputs) > 1:
            mix_pad = "[a_master_mix]"
            mix_in_str = "".join(mix_inputs)
            filters.append(f"{mix_in_str}amix=inputs={len(mix_inputs)}:duration=longest{mix_pad}")
            final_pads[AudioTrack.MIX] = mix_pad
        else:
            final_pads[AudioTrack.MIX] = mix_inputs[0]

    maps: list[str] = []
    for track in requested_tracks:
        if pad := final_pads.get(track):
            maps.append(pad)

    return AudioResult(inputs, filters, maps, curr_idx)