import { Button, Slider, Tooltip } from "@fluentui/react-components";
import { SpeakerHigh, SpeakerSlash } from "@phosphor-icons/react";
import type { TrackPreviewState } from "./audioEngine";
import type { StudioModel } from "./types";

export function Mixer({ model, state, onChange, onWrite }: {
  model: StudioModel; state: TrackPreviewState;
  onChange: (part: string, patch: Partial<TrackPreviewState[string]>) => void;
  onWrite: (part: string, field: "gain_db" | "pan", value: number) => void;
}) {
  return <section className="mixer panel" aria-label="Mixer">
    <header className="panel-heading"><span>MIXER</span><small>live preview and authored mix</small></header>
    <div className="mixer-strips">
      {model.parts.map((part) => {
        const track = state[part.id];
        return <div className="channel" key={part.id}>
          <div className="channel-meter"><i style={{ height: `${Math.min(100, Math.max(8, 70 + track.gain_db * 2))}%` }} /></div>
          <label>GAIN <strong>{track.gain_db.toFixed(1)}</strong></label>
          <Slider vertical min={-36} max={12} step={0.5} value={track.gain_db}
            onChange={(_, data) => onChange(part.id, { gain_db: data.value })}
            onMouseUp={() => onWrite(part.id, "gain_db", track.gain_db)} />
          <label>PAN <strong>{track.pan.toFixed(2)}</strong></label>
          <Slider min={-1} max={1} step={0.05} value={track.pan}
            onChange={(_, data) => onChange(part.id, { pan: data.value })}
            onMouseUp={() => onWrite(part.id, "pan", track.pan)} />
          <div className="channel-buttons">
            <Tooltip content={track.mute ? "Unmute" : "Mute"} relationship="label">
              <Button size="small" appearance={track.mute ? "primary" : "subtle"} icon={track.mute ? <SpeakerSlash /> : <SpeakerHigh />}
                onClick={() => onChange(part.id, { mute: !track.mute })} />
            </Tooltip>
            <Button size="small" appearance={track.solo ? "primary" : "subtle"} onClick={() => onChange(part.id, { solo: !track.solo })}>S</Button>
          </div>
          <div className="channel-name" style={{ borderColor: part.color }}>{part.name}</div>
        </div>;
      })}
      <div className="channel master-channel"><div className="channel-meter master-meter"><i /></div><b>MASTER</b><small>0.0 dB</small></div>
    </div>
  </section>;
}
